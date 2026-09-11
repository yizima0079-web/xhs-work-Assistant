"""审核编排：content → Claim 抽取 → 本地语料库交叉检索 → 模型判定 → 原子落库。

防幻觉底线（对齐 AGENTS.md"模型输出≠事实"）：
- judge 判 supported/contradicted 必须有至少一条来自本地检索的真实证据；
- evidence.content_id 必须 ∈ 候选集，模型编造的引用整体丢弃；
- 无证据支撑的结论降级 unclear，绝不硬撑；
- 模型调用失败/无 key → 明确异常（502/503），不伪造输出。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.adapters.llm import ModelAdapter, ModelError, ModelNotConfiguredError
from app.domain.enums import ClaimStatus, ClaimType, EvidenceKind, ReviewStatus
from app.domain.models import Claim, Content, Evidence, ReviewDecision
from app.repositories.base import ContentRepository, ReviewRepository
from app.services.prompt_templates import build_extract_messages, build_judge_messages

REVIEW_MIN_CONFIDENCE = 0.6
# 人工改判为已判定结论时写入的置信度。人既然下了 supported/contradicted 的结论，
# 可信度就由人的判断定 —— 若不写，模型遗留的 None/0.0 会让改判卡在
# REVIEW_MIN_CONFIDENCE 门槛上，状态永远推不动，人工复核通道形同虚设。
HUMAN_CONFIDENCE = 1.0
MAX_CLAIMS = 12
MAX_EXCERPT_CHARS = 80
MAX_RATIONALE_CHARS = 500
VALID_CLAIM_TYPES = {t.value for t in ClaimType}
VALID_CLAIM_STATUSES = {s.value for s in ClaimStatus}
# model 判定在无证据时只能给 unverified/unclear，避免无依据的 supported/contradicted
_JUDGED = {ClaimStatus.SUPPORTED, ClaimStatus.CONTRADICTED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_content_review_status(outcomes: list[tuple[ClaimStatus, float | None]]) -> ReviewStatus:
    """按单内容全部 claim 的最新状态/置信度汇总出内容终态（纯函数）。

    outcomes 元素 = (claim.status, claim.confidence)。规则：
    空 → PENDING；任一 unverified/unclear 或置信度不达标 → EXTRACTED（待人工）；
    同时存在 contradicted 与 supported → DISPUTED；全 supported 达标 → APPROVED；
    其余（全 contradicted / 无可核结论）→ REJECTED。
    """
    if not outcomes:
        return ReviewStatus.PENDING
    statuses = set()
    for status, confidence in outcomes:
        statuses.add(status)
        if status in (ClaimStatus.UNVERIFIED, ClaimStatus.UNCLEAR):
            return ReviewStatus.EXTRACTED
        if confidence is None or confidence < REVIEW_MIN_CONFIDENCE:
            return ReviewStatus.EXTRACTED
    if ClaimStatus.CONTRADICTED in statuses and ClaimStatus.SUPPORTED in statuses:
        return ReviewStatus.DISPUTED
    if statuses and statuses <= {ClaimStatus.SUPPORTED}:
        return ReviewStatus.APPROVED
    return ReviewStatus.REJECTED


class ReviewService:
    def __init__(
        self,
        review_repo: ReviewRepository,
        content_repo: ContentRepository,
        llm: ModelAdapter | None,
        searcher,
    ):
        self.review_repo = review_repo
        self.content_repo = content_repo
        self.llm = llm
        self.searcher = searcher

    # ---- 主流程 ----
    async def review_content(
        self, content_id: str, force: bool = False, app_visible_only: bool = False
    ) -> dict:
        """自动审核。force=True 为「重新审核」：忽略既有状态，**新增一个审核批次**。

        旧批次的断言/证据/判定原样保留（可回看、可切回生效），是「多版本管理」的基础。
        落库只发生在真正写那一刻（save_review_batch 内同一事务），
        因此模型中途失败时不会留下半成品，旧版本更不会被清空。

        `app_visible_only`（app 通道）下，已隐藏的内容在这里**等同于不存在** → 404。
        与 app 的视角一致（app 里这条内容确实不存在），顺带避免为一条隐藏内容白烧
        一次模型调用。候选检索也一并收窄，防止隐藏内容长进新批次的证据里。
        """
        content = await self.content_repo.get(content_id, app_visible_only=app_visible_only)
        if content is None:
            raise KeyError(f"内容不存在: {content_id}")
        if content.review_status != ReviewStatus.PENDING and not force:
            raise ValueError(f"内容已审核过（{content.review_status.value}），不能重复自动审核")
        if self.llm is None or not self.llm.is_configured():
            raise ModelNotConfiguredError("服务端未配置模型 API key，无法自动审核")

        raw = await self.llm.chat_json(build_extract_messages(content))
        claims = self._clean_claims(raw, content)
        if not claims:
            # 无可抽断言 → 归入待人工，不伪造结论
            await self.review_repo.save_review_batch(
                content_id, [], [], [], ReviewStatus.EXTRACTED
            )
            return self._summary(content_id, ReviewStatus.EXTRACTED, [])

        processed: list[Claim] = []
        evidences: list[Evidence] = []
        decisions: list[ReviewDecision] = []
        try:
            for claim in claims:
                candidates = await self.searcher.find_candidates(
                    claim.text,
                    exclude_content_id=content_id,
                    limit=8,
                    app_visible_only=app_visible_only,
                )
                outcome = await self.llm.chat_json(build_judge_messages(claim, candidates))
                _claim, new_evidences, decision = self._clean_judgment(outcome, claim, candidates)
                processed.append(_claim)
                evidences.extend(new_evidences)
                decisions.append(decision)
        except ModelError as exc:
            # 部分 claim 已判定：落为独立的新批次（extracted）后向上抛（502/503），不伪造。
            # 旧批次照旧原样保留，用户可在版本条里切回去。
            if processed:
                await self._save(
                    content_id, processed, evidences, decisions, ReviewStatus.EXTRACTED
                )
            raise

        status = resolve_content_review_status([(c.status, c.confidence) for c in claims])
        await self._save(content_id, claims, evidences, decisions, status)
        return self._summary(content_id, status, claims)

    async def _save(
        self,
        content_id: str,
        claims: list[Claim],
        evidences: list[Evidence],
        decisions: list[ReviewDecision],
        status: ReviewStatus,
    ) -> None:
        await self.review_repo.save_review_batch(
            content_id, claims, evidences, decisions, status
        )

    # ---- 查询 ----
    async def get_content_review(
        self, content_id: str, batch_id: str | None = None, app_visible_only: bool = False
    ) -> dict:
        """返回某批次的断言明细 + 全部历史批次。batch_id 为空 → 生效批次。

        `app_visible_only` 下，被 app 隐藏的内容在此**等同于不存在**（KeyError →
        路由 404）。断言与证据是内容的拆解，内容对 app 不可见，它们就不该可见；而
        且 `evidence.excerpt` 里带着候选内容的正文片段，泄露面不止这一条。
        """
        content = await self.content_repo.get(content_id, app_visible_only=app_visible_only)
        if content is None:
            raise KeyError(f"内容不存在: {content_id}")
        batches = await self.review_repo.list_review_batches(content_id)
        if batch_id is None:
            batch_id = content.active_batch_id or next(
                (b["batch_id"] for b in batches if b["active"]), None
            )
        claims = await self.review_repo.list_claims(content_id, batch_id)
        out = []
        for c in claims:
            out.append(
                {
                    "claim": c,
                    "evidence": await self.review_repo.list_evidence(c.claim_id),
                    "decisions": await self.review_repo.list_decisions(c.claim_id),
                }
            )
        return {
            "content": content,
            "claims": out,
            "batches": batches,
            "batch_id": batch_id,
        }

    async def list_review_batches(
        self, content_id: str, app_visible_only: bool = False
    ) -> list[dict]:
        content = await self.content_repo.get(content_id, app_visible_only=app_visible_only)
        if content is None:
            raise KeyError(f"内容不存在: {content_id}")
        return await self.review_repo.list_review_batches(content_id)

    async def activate_review_batch(self, content_id: str, batch_id: str) -> ReviewStatus:
        """把某历史批次切为生效版本，并按该批次的断言重算内容状态。

        内容状态必须由**生效批次**决定：否则切到旧版本后状态还停留在新版本的判定上，
        会出现「看着第 2 版、状态却是第 3 版算出来的」这种自相矛盾的界面。
        """
        ok = await self.review_repo.set_active_batch(content_id, batch_id)
        if not ok:
            raise KeyError(f"审核批次不存在或不属于该内容: {batch_id}")
        claims = await self.review_repo.list_claims(content_id, batch_id)
        status = resolve_content_review_status([(c.status, c.confidence) for c in claims])
        await self.content_repo.update_review_status(content_id, status)
        return status

    # ---- 人工复核 ----
    async def human_decide(
        self,
        claim_id: str,
        status: ClaimStatus,
        rationale: str = "",
        evidence_ids: list[str] | None = None,
        reviewer: str = "human",
    ) -> ReviewStatus:
        claim = await self.review_repo.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"断言不存在: {claim_id}")
        evidence_ids = evidence_ids or []
        if evidence_ids:
            existing = {e.evidence_id for e in await self.review_repo.list_evidence(claim_id)}
            unknown = [eid for eid in evidence_ids if eid not in existing]
            if unknown:
                raise ValueError(f"evidence 引用越界（不属于该 claim）: {', '.join(unknown)}")

        await self.review_repo.add_decision(
            ReviewDecision(
                decision_id=uuid.uuid4().hex,
                claim_id=claim_id,
                status=status,
                rationale=rationale[:MAX_RATIONALE_CHARS],
                reviewer=reviewer,
                evidence_ids=evidence_ids,
            )
        )
        claim.status = status
        # 人工改判为 supported/contradicted 时置信度必须一并跟上：模型当初给不出
        # 置信度（None 或 0.0）的断言，人工拍板后仍会被 resolve_content_review_status
        # 的 conf < REVIEW_MIN_CONFIDENCE 判回 EXTRACTED，改判等于没改。
        # 已达标的置信度不覆盖，保留模型给出的信息。
        if status in _JUDGED and (
            claim.confidence is None or claim.confidence < REVIEW_MIN_CONFIDENCE
        ):
            claim.confidence = HUMAN_CONFIDENCE
        claim.updated_at = _now()
        await self.review_repo.update_claim(claim)

        # 重算内容状态（人工改判可回落 approved → disputed/extracted）。
        # 只取该 claim 所属批次：版本化后若取全部批次，会把他版本的断言也算进来，
        # 一个批次里改一条就能把内容状态带偏。
        siblings = await self.review_repo.list_claims(claim.content_id, claim.batch_id)
        batch_status = resolve_content_review_status(
            [(c.status, c.confidence) for c in siblings]
        )
        # 只有生效批次的判定才写回 contents.review_status：
        # 在历史版本里改判一条，不该动摇当前生效版本的结论。
        batches = await self.review_repo.list_review_batches(claim.content_id)
        if any(b["batch_id"] == claim.batch_id and b["active"] for b in batches):
            await self.content_repo.update_review_status(claim.content_id, batch_status)
            return batch_status
        content = await self.content_repo.get(claim.content_id)
        return content.review_status if content else batch_status

    # ---- 模型产出净化（防幻觉核心）----
    def _clean_claims(self, raw: dict, content: Content) -> list[Claim]:
        items = raw.get("claims") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        now = _now()
        out: list[Claim] = []
        for raw_item in items[:MAX_CLAIMS]:
            if not isinstance(raw_item, dict):
                continue
            text = raw_item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            ctype_raw = raw_item.get("claim_type")
            ctype = (
                ClaimType(ctype_raw)
                if isinstance(ctype_raw, str) and ctype_raw in VALID_CLAIM_TYPES
                else ClaimType.PREDICTION  # 非法类型保守归类（预测待核）
            )
            conf = raw_item.get("confidence")
            out.append(
                Claim(
                    claim_id=uuid.uuid4().hex,
                    content_id=content.content_id,
                    text=text.strip()[:500],
                    status=ClaimStatus.UNVERIFIED,
                    confidence=_clamp(conf),
                    claim_type=ctype,
                    meta={},
                    created_at=now,
                    updated_at=now,
                )
            )
        return out

    def _clean_judgment(
        self, outcome: dict, claim: Claim, candidates: list[Content]
    ) -> tuple[Claim, list[Evidence], ReviewDecision]:
        """把模型判定净化成可落库结构。返回 (claim, evidences, decision)。"""
        candidates_map = {c.content_id: c for c in candidates}

        raw_status = outcome.get("status") if isinstance(outcome, dict) else None
        status = (
            ClaimStatus(raw_status)
            if isinstance(raw_status, str) and raw_status in VALID_CLAIM_STATUSES
            else ClaimStatus.UNCLEAR
        )

        evidences: list[Evidence] = []
        raw_evs = outcome.get("evidence") if isinstance(outcome, dict) else None
        if isinstance(raw_evs, list):
            now = _now()
            for ev in raw_evs:
                if not isinstance(ev, dict):
                    continue
                content_id = ev.get("content_id")
                if not isinstance(content_id, str) or content_id not in candidates_map:
                    continue  # 编造/越界的引用直接丢弃
                if not isinstance(ev.get("supports"), bool):
                    continue  # supports 必须是真布尔，拒绝字符串化
                src = candidates_map[content_id]
                excerpt = ((src.text or src.title) or "").strip()[:MAX_EXCERPT_CHARS]
                evidences.append(
                    Evidence(
                        evidence_id=uuid.uuid4().hex,
                        claim_id=claim.claim_id,
                        source_kind=EvidenceKind.CONTENT,
                        source_ref=content_id,
                        excerpt=excerpt,
                        supports=ev["supports"],
                        strength=_clamp(ev.get("strength"), default=0.8),
                    )
                )

        # 无证据支撑的 supported/contradicted 结论降级 unclear（防幻觉核心）
        if status in _JUDGED and not evidences:
            status = ClaimStatus.UNCLEAR
        elif status == ClaimStatus.UNCLEAR and not evidences:
            pass  # 本来就无需证据

        claim.status = status
        claim.confidence = _clamp(outcome.get("confidence")) if isinstance(outcome, dict) else claim.confidence
        claim.updated_at = _now()

        rationale = outcome.get("rationale") if isinstance(outcome, dict) else ""
        if not isinstance(rationale, str):
            rationale = ""
        decision = ReviewDecision(
            decision_id=uuid.uuid4().hex,
            claim_id=claim.claim_id,
            status=status,
            rationale=rationale[:MAX_RATIONALE_CHARS],
            reviewer="model",
            evidence_ids=[e.evidence_id for e in evidences],
        )
        return claim, evidences, decision

    @staticmethod
    def _summary(content_id: str, status: ReviewStatus, claims: list[Claim]) -> dict:
        return {
            "content_id": content_id,
            "review_status": status.value,
            "claim_count": len(claims),
        }


def _clamp(value, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))

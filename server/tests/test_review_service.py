"""审核服务测试：状态机、主流程、防幻觉、人工复核、异常语义。全程离线（FakeModel）。"""
from __future__ import annotations

import pytest

from app.adapters.llm import ModelNotConfiguredError, ModelProviderError
from app.domain.enums import ClaimStatus, ReviewStatus
from app.domain.models import Content
from app.repositories.sqlite import SqliteContentRepository, SqliteReviewRepository
from app.services.review import ReviewService, resolve_content_review_status
from app.services.searcher import CorpusSearcher

REVIEWS = [
    # (claim_status, confidence) 列表 -> 内容终态
    ([], ReviewStatus.PENDING),
    ([(ClaimStatus.UNVERIFIED, 0.9)], ReviewStatus.EXTRACTED),
    ([(ClaimStatus.SUPPORTED, 0.9), (ClaimStatus.UNCLEAR, 0.8)], ReviewStatus.EXTRACTED),
    ([(ClaimStatus.SUPPORTED, 0.4)], ReviewStatus.EXTRACTED),          # 置信度不达标
    ([(ClaimStatus.SUPPORTED, 0.9), (ClaimStatus.CONTRADICTED, 0.8)], ReviewStatus.DISPUTED),
    ([(ClaimStatus.SUPPORTED, 0.95), (ClaimStatus.SUPPORTED, 0.7)], ReviewStatus.APPROVED),
    ([(ClaimStatus.CONTRADICTED, 0.9)], ReviewStatus.REJECTED),
    ([(ClaimStatus.SUPPORTED, None)], ReviewStatus.EXTRACTED),         # 无置信度 → 待人工
]


@pytest.mark.parametrize("outcomes,expected", REVIEWS)
def test_resolve_content_review_status(outcomes, expected):
    assert resolve_content_review_status(outcomes) == expected


def make_content(cid: str, text: str) -> Content:
    p, i = cid.split(":", 1)
    return Content(content_id=cid, platform=p, platform_item_id=i, text=text)


async def _seed(init_test_db, docs):
    repo = SqliteContentRepository()
    for d in docs:
        await repo.upsert(d)
    return repo


def _service(content_repo, llm) -> ReviewService:
    return ReviewService(
        review_repo=SqliteReviewRepository(),
        content_repo=content_repo,
        llm=llm,
        searcher=CorpusSearcher(content_repo),
    )


async def _seed_subject_and_corpus(init_test_db):
    """subject 内容文本命中语料 doc1/doc2，便于 judge 引用。"""
    content_repo = await _seed(
        init_test_db,
        [
            make_content("xhs:subject", "这款咖啡真的能提神，长期喝可能心悸。"),
            make_content("xhs:doc1", "很多人反馈咖啡提神效果明显。"),
            make_content("xhs:doc2", "医生提示大量咖啡因可能引发心悸。"),
        ],
    )
    return content_repo


def extract_resp(*claims):
    return {"claims": list(claims)}


def judge_supported(cid):
    return {
        "status": "supported",
        "evidence": [{"content_id": cid, "supports": True, "strength": 0.9}],
        "rationale": "本地语料佐证", "confidence": 0.9,
    }


async def test_review_not_configured(init_test_db):
    content_repo = await _seed(init_test_db, [make_content("xhs:subject", "无 key 也应触发 503")])
    service = _service(content_repo, llm=None)
    with pytest.raises(ModelNotConfiguredError):
        await service.review_content("xhs:subject")


async def test_review_missing_content_and_already_reviewed(init_test_db):
    content_repo = await _seed(init_test_db, [make_content("xhs:subject", "hmm")])
    llm = FakeLLM([])
    service = _service(content_repo, llm)
    with pytest.raises(KeyError):
        await service.review_content("xhs:nope")

    from app.repositories.sqlite import SqliteReviewRepository
    # 预置已审核状态 → 拒绝重复审核
    await SqliteContentRepository().update_review_status("xhs:subject", ReviewStatus.EXTRACTED)
    with pytest.raises(ValueError):
        await service.review_content("xhs:subject")


async def test_review_empty_claims_goes_extracted(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    llm = FakeLLM([extract_resp()])  # 模型抽不出断言
    result = await _service(content_repo, llm).review_content("xhs:subject")
    assert result["review_status"] == ReviewStatus.EXTRACTED.value
    assert result["claim_count"] == 0
    review = SqliteReviewRepository()
    assert await review.list_claims("xhs:subject") == []
    content = await content_repo.get("xhs:subject")
    assert content.review_status == ReviewStatus.EXTRACTED
    # 空批次不得留下悬空指针：没有断言就没有可生效的版本。若这里写入了 batch_id，
    # 会指向一个 claims 里不存在的批次（list_review_batches 从 claims 聚合，列不出来）。
    assert content.active_batch_id is None
    assert await review.list_review_batches("xhs:subject") == []


async def test_rereview_after_empty_batch_activates_new_batch(init_test_db):
    """空审核留下的 NULL 不该妨碍后续重审：force 出新断言后必须正常生效。"""
    content_repo = await _seed_subject_and_corpus(init_test_db)
    review = SqliteReviewRepository()
    await _service(content_repo, FakeLLM([extract_resp()])).review_content("xhs:subject")
    assert (await content_repo.get("xhs:subject")).active_batch_id is None

    llm = FakeLLM(
        [
            extract_resp({"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9}),
            judge_supported("xhs:doc1"),
        ]
    )
    await _service(content_repo, llm).review_content("xhs:subject", force=True)

    content = await content_repo.get("xhs:subject")
    assert content.review_status == ReviewStatus.APPROVED
    batches = await review.list_review_batches("xhs:subject")
    assert len(batches) == 1
    # 批次条里必须恰有一项生效，且与 contents.active_batch_id 指向同一批次
    assert batches[0]["active"] is True
    assert content.active_batch_id == batches[0]["batch_id"]


async def test_review_approve_flow_persists_everything(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    llm = FakeLLM(
        [
            extract_resp({"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9}),
            judge_supported("xhs:doc1"),
        ]
    )
    result = await _service(content_repo, llm).review_content("xhs:subject")
    assert result["review_status"] == ReviewStatus.APPROVED.value
    assert result["claim_count"] == 1

    review = SqliteReviewRepository()
    claims = await review.list_claims("xhs:subject")
    assert len(claims) == 1
    claim = claims[0]
    assert claim.status == ClaimStatus.SUPPORTED
    assert claim.claim_type.value == "fact"
    evidences = await review.list_evidence(claim.claim_id)
    assert len(evidences) == 1
    assert evidences[0].source_ref == "xhs:doc1"
    assert evidences[0].source_kind.value == "content"
    assert "提神效果明显" in evidences[0].excerpt  # 摘录来自真实候选内容，非模型编造
    decisions = await review.list_decisions(claim.claim_id)
    assert len(decisions) == 1
    assert decisions[0].status == ClaimStatus.SUPPORTED
    assert decisions[0].evidence_ids == [evidences[0].evidence_id]
    assert decisions[0].reviewer == "model"


async def test_review_fabricated_evidence_dropped_downgrade_unclear(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    llm = FakeLLM(
        [
            extract_resp({"text": "咖啡能防癌", "claim_type": "fact", "confidence": 0.9}),
            {
                "status": "supported",
                "evidence": [
                    {"content_id": "xhs:GHOST", "supports": True},        # 编造 → 丢弃
                    {"content_id": "xhs:doc1", "supports": "yes"},        # supports 非 bool → 丢弃
                ],
                "rationale": "依据网文", "confidence": 0.9,
            },
        ]
    )
    result = await _service(content_repo, llm).review_content("xhs:subject")
    # 无任何可用证据的 supported 降级 unclear → 内容待人工
    assert result["review_status"] == ReviewStatus.EXTRACTED.value
    review = SqliteReviewRepository()
    claims = await review.list_claims("xhs:subject")
    assert claims[0].status == ClaimStatus.UNCLEAR
    assert await review.list_evidence(claims[0].claim_id) == []


async def test_review_model_failure_partial_saved_then_raise(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    first_judge = judge_supported("xhs:doc1")
    # 计数：第 1 次调用 = extract，第 2 次 = 第一个 judge；第 3 次 judge 抛 ProviderError
    calls = {"n": 0}

    class Boom:
        def is_configured(self):
            return True
        async def chat_json(self, messages):
            calls["n"] += 1
            if calls["n"] == 1:
                return extract_resp(
                    {"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9},
                    {"text": "咖啡心悸", "claim_type": "fact", "confidence": 0.9},
                )
            if calls["n"] == 2:
                return first_judge
            raise ModelProviderError("上游超时")

    service = _service(content_repo, Boom())
    with pytest.raises(ModelProviderError):
        await service.review_content("xhs:subject")

    # 部分已判定 claim 落库，内容停 extracted 供人工
    review = SqliteReviewRepository()
    claims = await review.list_claims("xhs:subject")
    assert len(claims) == 1
    assert claims[0].status == ClaimStatus.SUPPORTED
    content = await content_repo.get("xhs:subject")
    assert content.review_status == ReviewStatus.EXTRACTED


async def test_get_content_review_shape(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    await _service(content_repo, FakeLLM(
        [extract_resp({"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9}),
         judge_supported("xhs:doc1")]
    )).review_content("xhs:subject")

    detail = await _service(content_repo, FakeLLM([])).get_content_review("xhs:subject")
    assert detail["content"].content_id == "xhs:subject"
    assert len(detail["claims"]) == 1
    item = detail["claims"][0]
    assert item["claim"].status == ClaimStatus.SUPPORTED
    assert len(item["evidence"]) == 1
    assert len(item["decisions"]) == 1


async def test_human_decide_rejects_out_of_scope_evidence(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    await _service(content_repo, FakeLLM(
        [extract_resp({"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9}),
         judge_supported("xhs:doc1")]
    )).review_content("xhs:subject")
    claim = (await SqliteReviewRepository().list_claims("xhs:subject"))[0]
    service = _service(content_repo, FakeLLM([]))
    with pytest.raises(ValueError):
        await service.human_decide(claim.claim_id, ClaimStatus.CONTRADICTED, evidence_ids=["ghost"])


async def test_human_decide_flips_approved_to_disputed(init_test_db):
    content_repo = await _seed_subject_and_corpus(init_test_db)
    # 两条 claim 都 supported → content approved
    llm = FakeLLM(
        [
            extract_resp(
                {"text": "咖啡提神效果好", "claim_type": "fact", "confidence": 0.9},
                {"text": "咖啡因引发心悸", "claim_type": "fact", "confidence": 0.9},
            ),
            judge_supported("xhs:doc1"),
            judge_supported("xhs:doc2"),
        ]
    )
    service = _service(content_repo, llm)
    result = await service.review_content("xhs:subject")
    assert result["review_status"] == ReviewStatus.APPROVED.value

    claims = await SqliteReviewRepository().list_claims("xhs:subject")
    assert len(claims) == 2
    service2 = _service(content_repo, FakeLLM([]))
    status = await service2.human_decide(claims[0].claim_id, ClaimStatus.CONTRADICTED,
                                         rationale="人工复核：证据不足")
    # 一条 contradicted + 一条 supported → DISPUTED（approved 回落）
    assert status == ReviewStatus.DISPUTED
    content = await content_repo.get("xhs:subject")
    assert content.review_status == ReviewStatus.DISPUTED


async def test_human_decide_lifts_confidence_so_status_can_advance(init_test_db):
    """模型给不出置信度的断言，人工改判 supported 后内容必须能推进到 approved。

    回归点：human_decide 原先只写 status、不动 confidence，重算时 conf=0.0 仍被
    resolve_content_review_status 判回 EXTRACTED —— 人工复核整条通道是断的，
    人拍板了也不算数。真库现状正是如此：19 条 claim 全 unclear 且 confidence=0.0。
    """
    content_repo = await _seed_subject_and_corpus(init_test_db)
    llm = FakeLLM(
        [
            extract_resp({"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9}),
            # 模型找不到本地证据 → 判 unclear 且给不出置信度（真库实测形态）
            {"status": "unclear", "confidence": 0.0,
             "rationale": "无候选内容可供参考", "evidence": []},
        ]
    )
    service = _service(content_repo, llm)
    assert (await service.review_content("xhs:subject"))["review_status"] == ReviewStatus.EXTRACTED.value

    repo = SqliteReviewRepository()
    claim = (await repo.list_claims("xhs:subject"))[0]
    assert claim.confidence == 0.0

    status = await service.human_decide(
        claim.claim_id, ClaimStatus.SUPPORTED, rationale="人工复核：确认成立"
    )
    assert status == ReviewStatus.APPROVED
    assert (await content_repo.get("xhs:subject")).review_status == ReviewStatus.APPROVED
    assert (await repo.get_claim(claim.claim_id)).confidence == 1.0


async def test_human_decide_keeps_sufficient_model_confidence(init_test_db):
    """模型给出的达标置信度不该被人工改判覆盖 —— 那是模型的判定信息，改判只补人不补数。"""
    content_repo = await _seed_subject_and_corpus(init_test_db)
    llm = FakeLLM(
        [
            extract_resp({"text": "咖啡提神", "claim_type": "fact", "confidence": 0.9}),
            judge_supported("xhs:doc1"),
        ]
    )
    service = _service(content_repo, llm)
    await service.review_content("xhs:subject")

    repo = SqliteReviewRepository()
    claim = (await repo.list_claims("xhs:subject"))[0]
    assert claim.confidence == 0.9

    await service.human_decide(claim.claim_id, ClaimStatus.SUPPORTED, rationale="复核确认")
    assert (await repo.get_claim(claim.claim_id)).confidence == 0.9


class FakeLLM:
    """极简脚本化模型：按序吐 dict；空脚本时返回 {}。"""

    def __init__(self, responses):
        self._queue = list(responses)
        self.calls = 0

    def is_configured(self):
        return True

    async def chat_json(self, messages):
        self.calls += 1
        if not self._queue:
            return {}
        return self._queue.pop(0)

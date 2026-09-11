"""模型 prompt 纯函数。全部显式要求 JSON 输出；内容不含未检索事实。

防御幻觉的策略落在服务层（见 services/review.py），但 prompt 先约定：
- extract 只许抽"原文里有依据"的可核断言，无依据 → 空 claims；
- judge 只能引用候选列表里给出的 content_id，其余字段给空/低置信。
"""
from __future__ import annotations

import json

from app.adapters.llm import ChatMessage

_SYSTEM_JSON = (
    "你是内容审核分析引擎。只输出一个合法 JSON 对象，不要 Markdown 围栏、不要额外文字。"
)


def _content_digest(title: str | None, text: str | None) -> str:
    title = (title or "").strip()
    text = (text or "").strip()
    if title and text:
        return f"标题：{title}\n正文：{text}"
    return title or text or "（无正文）"


def build_extract_messages(content) -> list[ChatMessage]:
    """从单条内容抽取可核断言。输出形如 {"claims": [{text, claim_type, confidence}]}。"""
    system = (
        _SYSTEM_JSON
        + "\n从给定内容抽取可被事实核验的断言（claims），规则："
        + "1) 只抽原文明确陈述、可查证的断言；观点/情绪/纯推广照实标 claim_type。"
        + "2) 抽取内容如果完全无法引用原文依据，就返回空 claims。"
        + '3) 输出 JSON 形如 {"claims": [{"text": "...", "claim_type": "fact|opinion|prediction|promotion", "confidence": 0.0}]}，'
        + "confidence 为 0~1，表示断言在原文中的明确程度，最多 12 条。"
    )
    user = f"内容 ID：{content.content_id}\n平台：{content.platform}\n{_content_digest(content.title, content.text)}"
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def build_judge_messages(claim, candidates: list) -> list[ChatMessage]:
    """让模型对照检索出的候选内容判定 claim。只能引用候选的 content_id。"""
    candidate_lines = []
    for c in candidates:
        digest = _content_digest(c.title, c.text)
        candidate_lines.append(f"- content_id: {c.content_id}\n  {digest}")
    system = (
        _SYSTEM_JSON
        + "\n你是多源核验裁判。规则："
        + "1) 只能引用上面提供的候选 content_id 作为 evidence，不得编造任何 content_id/URL。"
        + "2) 无任何候选能支撑或反驳时，status 判 unclear、evidence 给空数组，绝不硬撑结论。"
        + "3) 有候选明显支撑 → status=supported；明显相反 → contradicted。"
        + '4) 输出 JSON 形如 {"status": "supported|contradicted|unclear", '
        + '"evidence": [{"content_id": "...", "supports": true, "strength": 0.0}], '
        + '"rationale": "一句话理由", "confidence": 0.0}'
    )
    user = (
        f"待判定断言（content_id={claim.content_id}）：{claim.text}\n"
        f"断言类型：{claim.claim_type.value if claim.claim_type else 'unknown'}\n"
        "候选内容：\n" + ("\n".join(candidate_lines) if candidate_lines else "（无候选）")
    )
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


_ANALYSIS_TEXT_MAX = 600   # 单条正文喂给模型的上限（title/tags/作者/互动完整保留）
_ANALYSIS_CAND_MAX = 8


def analysis_digest(content) -> str:
    """把一条内容压缩成分析模型可用的紧凑行（含真实互动数字，供爆款/平淡归因）。"""
    parts = [f"content_id: {content.content_id}", f"作者: {content.author_name or '（未知）'}"]
    ctype = content.content_type.value if content.content_type else ""
    if ctype:
        parts.append(f"类型: {ctype}")
    if content.published_at:
        parts.append(f"发布时间: {content.published_at[:10]}")
    title = (content.title or "").strip()
    if title:
        parts.append(f"标题: {title[:100]}")
    text = (content.text or "").strip()
    if text:
        parts.append("正文: " + text[:_ANALYSIS_TEXT_MAX])
    if content.tags:
        parts.append("标签: " + " ".join(content.tags))
    eng = content.engagement.model_dump() if content.engagement else {}
    if eng:
        parts.append("互动(点赞/评论/转发/收藏): " + json.dumps(
            {k: v for k, v in eng.items() if v is not None}, ensure_ascii=False
        ))
    if content.cover_url:
        parts.append("有封面图")
    return "\n".join(parts)


def build_analysis_messages(content, candidates: list, focus: str = "") -> list[ChatMessage]:
    """单条作品爆款/平淡归因。给出目标内容 + 同话题候选（可选横向对比基线）。

    模型只能引用候选 content_id 作为 comparison.baseline_content_ids；数字性
    evidence 只能来自上面给定的数据，不得编造互动数字或候选。

    focus：用户在「重新分析」时补充的观察视角。**只影响观察侧重**，不改判定口径、
    不改输出结构、不放开防幻觉约束 —— 主体分析方向仍由下面固定的 system 规则决定。
    """
    candidate_lines = [
        analysis_digest(c)[:900] for c in candidates[:_ANALYSIS_CAND_MAX]
    ] or ["（无同话题候选，本次只做单条深度分析，comparison.baseline_content_ids 给空数组）"]
    system = (
        _SYSTEM_JSON
        + "\n你是小红书爆款分析专家。规则："
        + "1) 分析对象是上面标注的【目标内容】，verdict 判定它相对同话题内容算爆款(viral)还是平淡(flat)；"
        + "   数据不足或无法判定时用 uncertain。"
        + "2) 所有互动数字、依据只能来自给定数据，禁止编造数字或候选内容。"
        + "3) comparison.baseline_content_ids 只能从候选的 content_id 里选真实基线；"
        + '   候选为空或都不可比就给空数组，此时 differentiators/shared_patterns 也置空。'
        + '4) 输出 JSON 形如 {"verdict": "viral|flat|uncertain", "summary": "一句话结论", '
        + '"topic": "同话题归类", "viral_reasons": [{"factor": "...", "evidence": "...", "confidence": 0.0}], '
        + '"flat_reasons": [{"factor": "...", "evidence": "...", "confidence": 0.0}], '
        + '"hooks": ["开头钩子"], "audience": ["目标人群"], '
        + '"comparison": {"baseline_content_ids": [], "differentiators": [], "shared_patterns": []}, '
        + '"suggestions": ["复刻/改进建议"], "confidence": 0.0, "limitations": []}'
    )
    user = f"【目标内容】\n{analysis_digest(content)}\n\n【同话题候选】\n" + "\n\n".join(candidate_lines)
    extra = (focus or "").strip()
    if extra:
        # 补充视角只加在 user 侧、且显式限定作用域：system 的分析框架/口径/防幻觉规则一字不改。
        system += (
            "4) 用户提供了【补充关注点】：把它当作额外的观察侧重，在既有分析框架内多花笔墨；"
            "不得改变 verdict 判定口径、不得改变输出 JSON 结构、不得引入给定数据之外的事实或数字。"
        )
        user += f"\n\n【补充关注点（用户指定·仅补充观察角度）】\n{extra}"
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def build_report_messages(scope_stats: dict) -> list[ChatMessage]:
    """基于样本统计生成 §9.1 报告 JSON（分析字段部分）。"""
    system = (
        _SYSTEM_JSON
        + "\n你是内容趋势分析师。规则："
        + "1) 所有 evidence_content_ids / evidence 只能从提供的内容 ID 里选，不得编造。"
        + "2) 爆点/人设/趋势必须有样本支撑；样本不足时 trend_direction.direction 用 uncertain。"
        + '3) 输出 JSON 形如 {"executive_summary": "...", "viral_patterns": [{"pattern": "...", '
        + '"evidence_content_ids": [], "confidence": 0.0, "counterexamples": []}], '
        + '"presentation_style": {"visual": [], "text": [], "video": [], "interaction": []}, '
        + '"account_persona": {"hypotheses": [], "evidence": [], "confidence": 0.0}, '
        + '"trend_direction": {"topics": [], "direction": "rising|stable|falling|uncertain", "window": ""}, '
        + '"limitations": []}'
    )
    user = "样本范围与内容摘录：\n" + json_dumps_zh(scope_stats)
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def build_markdown_messages(text: str) -> list[ChatMessage]:
    """把口语化原始文本整理为"标题级别分明"的 markdown（入库前唯一清洗出口）。

    LLM 允许改写表达、删除废话，但不得新增原文不存在的事实、数据或出处。
    输出裸 markdown 文本，不包代码围栏，不给多余解释。
    """
    system = (
        "你是知识库文本整理引擎。把用户给出的口语化、带时间戳/语气词/废话的原始文本，"
        "严格整理成标题级别分明的 Markdown：\n"
        "1) 用 `#` 做总标题、`##` 分章节、`###`/`####` 做小标题与要点，层级只按内容深度推进，不要跳级滥用。\n"
        "2) 保留原文所有事实、数据、观点、做法、出处/引用；删掉时间戳、语气词（呢/哈/嘛/啦/宝子们等）、"
        "口头禅、重复句和无关废话。\n"
        "3) 可以按主题重组顺序、合并重复，但绝不新增原文没有的事实，绝不断章取义。\n"
        "4) 只输出整理后的 Markdown 文本本身，不要代码围栏，不要开头寒暄、结尾总结。"
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=f"待整理的原始文本：\n\n{text}"),
    ]


_QA_MATERIAL_MAX = 6000
_QA_REF_MAX = 12


def _qa_ref_lines(allowed_refs: list[dict[str, str]]) -> str:
    if not allowed_refs:
        return "（本次没有可引用的结构化来源，evidence 一律给空数组）"
    lines = []
    for ref in allowed_refs[:_QA_REF_MAX]:
        lines.append(
            f"- ref: {ref.get('ref', '')} | source_kind: {ref.get('kind', '')} "
            f"| 说明: {ref.get('label', '')}"
        )
    return "\n".join(lines)


def build_viral_qa_messages(
    *,
    title: str,
    material: str,
    allowed_refs: list[dict[str, str]],
    max_pairs: int = 8,
    extra_instruction: str = "",
) -> list[ChatMessage]:
    """把一条爆款作品的素材蒸馏成标准「问题 + 答案」知识条目（拆解 + 归因 + 迁移）。

    铁律（对应审核红线，服务层还会做净化求交）：
    - evidence.ref 只能取下面列出的 ref，越界引用会被丢弃；
    - 所有互动数字、证据只能来自给定素材，禁止编造数字、账号、作品或出处；
    - 证据不足以支撑结论时**不产出该对**，宁可少给，不要凑数；
    - answer 必须写明适用条件与失败风险，禁止写成放之四海皆准的空话。
    """
    system = (
        _SYSTEM_JSON
        + "\n你是小红书爆款方法论蒸馏引擎。任务：把给定素材（爆款作品 + 归因分析）"
        + "拆解成可复用、可迁移的「问答对」知识条目。规则："
        + "\n1) 每条 = 一个真实会被问到的问题 + 一个能直接照做的答案。"
        + "问题要写成运营者会问的口吻（如「为什么这种夸张演绎能爆？」「换成平铺直叙还行吗？」）；"
        + "答案必须包含：结论 → 拆解 → 归因 → 迁移建议，并写清适用条件与失效风险。"
        + "\n2) dimensions 必须尽量填满六个维度："
        + "technique（表现手法/演绎方式）、persona（IP 人设打造）、hook（开头钩子）、"
        + "structure（结构节奏）、transfer（数组，换其他表达方式的迁移建议）、"
        + "transfer_risk（数组，迁移后可能失败的风险点）。某一维度素材里没有依据就留空串或空数组，不要硬编。"
        + "\n3) evidence 的 ref 只能从下面【可引用来源】里原样挑选，source_kind 用该行的 source_kind；"
        + "excerpt 必须摘自素材原文（可截断），不得改写或编造。越界 ref 会被系统丢弃。"
        + "\n4) 所有互动数字、账号、作品名、出处只能来自素材，禁止编造。"
        + "证据不足以支撑的结论不要产出该对；宁缺毋滥。"
        + "\n5) 禁止产出「保持真诚」「内容为王」这类无信息量的空话；每条都要能落到具体动作。"
        + f"\n6) 最多输出 {max_pairs} 对，按重要性降序。"
        + "\n7) 输出 JSON 形如 "
        + '{"qa_pairs": [{"question": "...", "answer": "...", '
        + '"dimensions": {"technique": "...", "persona": "...", "hook": "...", "structure": "...", '
        + '"transfer": ["..."], "transfer_risk": ["..."]}, '
        + '"evidence": [{"source_kind": "analysis|content", "ref": "...", "excerpt": "..."}], '
        + '"tags": ["爆款拆解"], "confidence": 0.0}], '
        + '"limitations": ["本次蒸馏的局限"]}'
    )
    if extra_instruction:
        system += "\n补充要求：" + extra_instruction

    user = (
        f"【素材标题】{title or '（未命名）'}\n\n"
        f"【可引用来源】\n{_qa_ref_lines(allowed_refs)}\n\n"
        f"【素材正文】\n{material[:_QA_MATERIAL_MAX]}"
    )
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


_RAG_CHUNK_MAX = 1500  # 单条命中喂给模型的文本上限（引用原文校验用全量，见 qa_agent）


def build_rag_answer_messages(context: dict, max_chars: int = 6000) -> list[ChatMessage]:
    """基于检索命中生成问答 prompt。模型只能引用给定块的 chunk_id，quote 必须逐字摘自原文。

    context = {"query", "hits": [{chunk_id, text, score, meta}], ...}。总长受 max_chars 约束，
    从后截断；至少保证首条命中完整（闸 2 已按最高分筛过）。
    """
    hits = context.get("hits") or []
    lines: list[str] = []
    used = 0
    for i, h in enumerate(hits, 1):
        text = (h.get("text") or "").strip()
        if not text:
            text = "（空块）"
        text = text[:_RAG_CHUNK_MAX]
        block = f"[{i}] chunk_id: {h['chunk_id']}\n{text}"
        if lines and used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    shown = len(lines)

    system = (
        _SYSTEM_JSON
        + "\n你是知识库问答引擎。只基于下面给定的【检索结果】作答，规则："
        + "\n1) 答案必须来自检索结果，禁止使用检索结果之外的任何知识；"
        + "检索结果不足以回答时，answer 给空串。"
        + "\n2) citations 里的 chunk_id 只能从【检索结果】里原样挑选，禁止编造；"
        + "quote 必须逐字摘自对应块的原文（可截断），不得改写或补全。"
        + "\n3) 无法用检索结果支撑的论断一律不要写，宁缺毋滥。"
        + '\n4) 输出 JSON 形如 {"answer": "...", "citations": [{"chunk_id": "...", "quote": "..."}], '
        + '"limitations": ["..."]}'
    )
    tail = (
        f"\n\n（另有 {len(hits) - shown} 条命中因长度未展示）"
        if shown < len(hits) else ""
    )
    user = (
        f"问题：{context.get('query', '')}\n\n"
        "【检索结果】\n" + "\n\n".join(lines) + tail
    )
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def json_dumps_zh(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)[:12000]

"""入库前知识文本清洗：规则预处理（确定性）+ LLM 结构化（标题分级 markdown）。

技术手册 §7.1 预留 processing-service 的实现之一。规则层做第一道粗筛（时间戳/
语气词/emoji/空白），LLM 负责把口语化文本重排成标题分级 markdown。两条铁律：
- 无 key / LLM 未配置 → 抛 ModelNotConfiguredError(503)，**绝不把未清洗文本入库**；
- LLM 可改写表达、删除废话，但 prompt 禁止它新增原文没有的事实。
清洗规范与 .claude/skills/markdown-normalize/SKILL.md 对齐，两边规则保持一致。
"""
from __future__ import annotations

import re

from app.adapters.llm import ChatMessage, ModelAdapter, ModelNotConfiguredError, ModelParseError
from app.services.prompt_templates import build_markdown_messages

# 时间戳：2024-01-02 / 2024/1/2 / 2024年1月2日 / 01-02 12:30 / 2024.1.2
_TS_LONG = re.compile(
    r"(?<![\w一-鿿])\d{4}[-/年.]\s?\d{1,2}(?:[-/月.]\s?\d{1,2})?\s*[日号]?"
    r"(?:\s*[T ]\s*\d{1,2}[:：]\d{2}(?::\d{2})?)?(?![：\d])"
)
# 时分：12:30 / 12:30:05（避开 "3:2" 一类比例，仅匹配合法时/分）
_TS_CLOCK = re.compile(r"(?<![\d:])[0-2]?\d[:：][0-5]\d(?:(?:[:：][0-5]\d))?(?!\d)")
# 相对时间词：仅删除"刚刚/现在"这类起止即失效的引导，不删今天/昨天（可能含实义，交给 LLM）
_TS_ABBR = re.compile(r"^(?:刚刚|现在|这会儿)\s+")
# 主播/口播套话（整段删除）
_FILLER_PHRASE = re.compile(
    r"(家人们|宝子们|姐妹们|兄弟们|铁子们|宝宝们|友友们|哈喽大家好|大家好呀|"
    r"喜欢的点点关注|点个赞吧|一键三连|评论区告诉我|记得收藏|关注我|别忘[了]?点赞)"
)
# 句末/句内语气词：仅在"词尾 + 标点/空白/行尾"时剔除，避免误伤实义字
_TAIL_PARTICLE = re.compile(r"[呢嘛啦吧哈呀哦噢喔喽呗咯呵诶]\s*([，。；：！？、….,;:!?…\s]|$)")
# emoji / 装饰符号（保留文字与 CJK 标点）
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # 交通/图形/扩展
    "\U00002600-\U000027BF"   # 杂项符号+装饰（含 ☀☂✔✿ 等）
    "\U0000FE00-\U0000FE0F"   # 变体选择符
    "\U0000200D\U0000200C"    # 零宽连接/非连接
    "\U0001F900-\U0001F9FF"   # 补充符号
    "]+"
)
_MULTI_BLANK = re.compile(r"[ \t]+\n")
_BLANK = re.compile(r"\n{3,}")


def preprocess(text: str) -> str:
    """确定性规则清洗（不依赖模型）：换行归一 → 去 emoji → 去时间戳/套话 → 去语气词 → 收白。"""
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = _EMOJI.sub("", s)
    s = _TS_LONG.sub(" ", s)
    s = _TS_CLOCK.sub(" ", s)
    s = _TS_ABBR.sub(" ", s)
    s = _FILLER_PHRASE.sub("", s)
    s = _TAIL_PARTICLE.sub(lambda m: m.group(1), s)
    s = _MULTI_BLANK.sub("\n", s)
    s = _BLANK.sub("\n\n", s)
    # 逐行收首尾空格
    lines = [ln.strip() for ln in s.split("\n")]
    s = "\n".join(lines).strip()
    return s


async def clean(raw_text: str, llm: ModelAdapter | None) -> str:
    """混合清洗：规则预处理 → LLM 结构化 markdown。LLM 缺失 → 503，绝不返回未清洗文本。"""
    pre = preprocess(raw_text)
    if not pre.strip():
        raise ModelParseError("文本预处理后为空，无法入库")
    if llm is None or not llm.is_configured():
        raise ModelNotConfiguredError("服务端未配置模型 API key（DATAPP_LLM_API_KEY），无法完成入库清洗")
    messages: list[ChatMessage] = build_markdown_messages(pre)
    out = await llm.chat_text(messages)
    cleaned = preprocess(out) if out else ""
    if not cleaned.strip():
        raise ModelParseError("模型清洗结果为空，未入库")
    return cleaned

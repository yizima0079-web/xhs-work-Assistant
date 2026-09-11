"""本地已采集语料库交叉检索：claim → 候选 content。

多源证据源 = 自己采集过的内容库（手册要求多源，当前 M2 用本地库兜底，
外部搜索源后续可加 Searcher 实现）。split_keywords 负责把一句话切成
可 LIKE 的 token，Repo search 是 OR 语义，token 太碎只会招噪声。
"""
from __future__ import annotations

import re

# 中文无实义单字（出现在 2-gram 里会让检索词失去区分度；全由这些字构成则整词丢弃）
_CJK_STOP = "的了是在有与和就不都而及或把被对从向让将等要会可能很也这那吧呢啊吗呀啦嘛"

_ASCII_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")
_SPLIT_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)

_MAX_TOKENS = 12
_CJK_WHOLE_MAX = 4   # <= 4 字整段作为独立词；更长须补子 gram，否则连续 LIKE 无法命中


def _is_useless(token: str) -> bool:
    return all(ch in _CJK_STOP for ch in token)


def _cjk_terms(chunk: str) -> list[str]:
    """中文串 → 检索词。独立短词整段保留；长串整段+滑窗 2/3-gram，剔除无实义片。"""
    n = len(chunk)
    if n <= _CJK_WHOLE_MAX:
        return [chunk]
    grams: list[str] = []
    grams += [chunk[i:i + 2] for i in range(n - 1)]
    grams += [chunk[i:i + 3] for i in range(n - 2)]
    if n <= 6:
        grams.insert(0, chunk)  # 不长，整段也是候选词
    seen: set[str] = set()
    out: list[str] = []
    for g in grams:
        if g in seen or _is_useless(g):
            continue
        seen.add(g)
        out.append(g)
    return out


def split_keywords(text: str, max_tokens: int = _MAX_TOKENS) -> list[str]:
    """把查询文本切成检索 token：ASCII 词原样、中文串 n-gram，去停用词。"""
    if not text:
        return []
    tokens: list[str] = []
    for part in _SPLIT_RE.split(text):
        if not part:
            continue
        if _ASCII_RE.fullmatch(part):
            tok = part.lower()
            if len(tok) >= 2:
                tokens.append(tok)
        else:
            for chunk in _CJK_RE.findall(part):
                tokens.extend(_cjk_terms(chunk))
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if tok not in seen and not _is_useless(tok):
            seen.add(tok)
            out.append(tok)
    return out[:max_tokens]


class CorpusSearcher:
    """在本地 content 库交叉检索 claim 的候选佐证内容。"""

    def __init__(self, content_repo):
        self._repo = content_repo

    async def find_candidates(
        self,
        text: str,
        exclude_content_id: str | None = None,
        limit: int = 8,
        app_visible_only: bool = False,
    ) -> list:
        """`app_visible_only` 由 app 通道传 True，**必须透传**。

        这条不是"读路径顺手过滤"，而是防**写入污染**：审核与分析会把候选内容的
        `title` 与正文前 80 字写进 `evidence.excerpt`（`review.py:_clean_judgment`），
        候选 id 还会进 `payload.comparison.baseline_content_ids`。不过滤的话，一条
        已经隐藏的内容会继续"长进"**可见内容**的派生数据里 —— 而那份派生数据是
        app 能看到的，等于绕过了隐藏。
        """
        keywords = split_keywords(text)
        if not keywords:
            return []
        # OR 语义一次查回；SQLite LIKE 不做评分，命中的多取较新内容
        return await self._repo.search(
            " ".join(keywords), exclude_content_id, limit, app_visible_only=app_visible_only
        )

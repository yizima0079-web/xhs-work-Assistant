"""文本分块（RAG 切片）。纯函数、零依赖。块尽量在句/段边界切断，超出则硬切，带 overlap 保上下文。"""
from __future__ import annotations

_SENT_END = "。！？!?；;\n"


def chunk_text(text: str, max_chars: int = 500, overlap: int = 80) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            cut = _last_boundary(text, start, end)
            if cut is not None:
                end = cut
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        nxt = max(end - overlap, start + 1)  # overlap 续写 + 保证前进
        if nxt <= start:
            break
        start = nxt
    return chunks


def _last_boundary(text: str, start: int, end: int) -> int | None:
    """在 [start+max//2, end) 内找最后一个句/段边界；找不到返回 None 交给硬切。"""
    low = start + (end - start) // 2
    for i in range(end - 1, low - 1, -1):
        if text[i] in _SENT_END:
            return i + 1
    return None

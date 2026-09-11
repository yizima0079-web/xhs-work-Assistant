"""余弦相似度 / top-k（纯 Python，零 numpy）。万级 chunk 全量扫描可接受。"""
from __future__ import annotations

from typing import Iterable


def cosine(a: list[float], b: list[float]) -> float:
    """两向量余弦相似度。零向量视为无方向 → 0.0。"""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)


def top_k(
    query: list[float],
    candidates: Iterable[tuple[object, list[float]]],
    k: int = 8,
) -> list[tuple[float, object]]:
    """对 (id, vector) 候选算余弦并取前 k，按相似度降序。返回 [(score, id)]。"""
    scored: list[tuple[float, object]] = []
    for cid, vec in candidates:
        if not vec:
            continue
        scored.append((cosine(query, vec), cid))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[:k]

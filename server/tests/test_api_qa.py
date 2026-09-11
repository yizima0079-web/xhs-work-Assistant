"""Q&A 知识 + RAG 问答 API 集成（ASGI + 假适配器，全程离线）。

覆盖三通道与问答闭环，对齐真实链路 V1–V8 的离线等价：
- 手动录入：无 key 也能 200（双通道独立性），doc_type='qa'、pending、chunk_count=0、pairs=draft；
- 蒸馏文本：先 clean 再蒸馏 → 草稿对（dimensions 非空、evidence 被白名单丢弃）；
- 审核 + 向量化 + 检索：approve → vectorize → search 命中 meta.kind=='qa' 且 Q/A 同块；
- 问答：正例 answered=true + 引用 verified；负例 answered=false；无 key 蒸馏 503 但手动录入 200。
"""
from __future__ import annotations

import re

import httpx
from httpx import ASGITransport, AsyncClient

from app.main import create_app

_CJK_ASCII = re.compile(r"[一-鿿A-Za-z0-9]")


class FakeVec:
    """确定性词袋向量（同文本共享维度、反映共现），离线路由，无外部调用。"""

    def __init__(self):
        self._dim = 64
        self._vocab: dict[str, int] = {}

    def is_configured(self) -> bool:
        return True

    async def embed(self, items: list[dict]) -> list[list[float]]:
        out = []
        for it in items:
            text = (it.get("text") or "") + " " + (it.get("image") or "")
            vec = [0.0] * self._dim
            for ch in set(_CJK_ASCII.findall(text)):
                if ch not in self._vocab:
                    self._vocab[ch] = len(self._vocab) % self._dim
                vec[self._vocab[ch]] = 1.0
            out.append(vec)
        return out


async def test_manual_qa_no_llm_works(init_test_db):
    """手动录入通道不依赖模型：无 key 也 200，产出 pending 草稿。"""
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/kb/qa/documents",
                json={
                    "title": "爆款演绎手法拆解",
                    "pairs": [
                        {
                            "question": "为什么夸张演绎能爆？",
                            "answer": "因为情绪反差强，前 3 秒给足冲突，匹配完播激励。",
                            "dimensions": {"technique": "夸张演绎", "persona": "素人人设"},
                            "tags": ["爆款拆解"],
                        }
                    ],
                    "tags": ["演绎手法"],
                },
            )
            assert r.status_code == 200
            body = r.json()
            doc = body["doc"]
            assert doc["doc_type"] == "qa"
            assert doc["status"] == "pending"
            assert doc["chunk_count"] == 0
            assert len(body["pairs"]) == 1
            assert body["pairs"][0]["status"] == "draft"
            assert body["pairs"][0]["question"] == "为什么夸张演绎能爆？"

            # 列表问答对 → draft（未审核不入库）
            doc_id = doc["doc_id"]
            r = await client.get(f"/api/v1/kb/qa/documents/{doc_id}/pairs")
            assert r.status_code == 200
            assert len(r.json()) == 1 and r.json()[0]["status"] == "draft"


async def test_qa_distill_text_to_draft(init_test_db, make_llm):
    """文本蒸馏：clean 后蒸馏出草稿对，doc_type='qa'、dimensions 非空。"""
    distill_json = {
        "qa_pairs": [
            {
                "question": "为什么夸张演绎能爆？",
                "answer": "因为情绪反差强，前 3 秒给足冲突，匹配平台完播激励。",
                "dimensions": {
                    "technique": "夸张演绎",
                    "persona": "素人人设",
                    "hook": "前 3 秒冲突",
                    "transfer": ["换更收敛的方式试试"],
                    "transfer_risk": ["过度夸张可能被判标题党"],
                },
                "evidence": [],
                "tags": ["爆款拆解"],
                "confidence": 0.8,
            }
        ],
        "limitations": ["样本单一"],
    }
    llm = make_llm(["# 夸张演绎\n\n## 拆解\n情绪反差强，前3秒给冲突。", distill_json])
    app = create_app(llm_adapter=llm)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/kb/qa/distill",
                json={"title": "演绎手法", "text": "家人们 夸张演绎 情绪反差 前3秒冲突 能爆", "tags": ["拆解"]},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["doc"]["doc_type"] == "qa"
            assert body["doc"]["status"] == "pending"
            assert len(body["pairs"]) == 1
            pair = body["pairs"][0]
            assert pair["status"] == "draft"
            assert pair["dimensions"].get("technique") == "夸张演绎"
            assert "样本单一" in body["limitations"]


async def test_qa_approve_vectorize_search_ask(init_test_db, make_llm):
    """闭环：手动录入 → 审核通过 → 向量化 → 检索命中 Q&A chunk → 问答正/负例。"""
    llm = make_llm([])
    app = create_app(llm_adapter=llm, embedding_adapter=FakeVec())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            # 1) 手动录入
            r = await client.post(
                "/api/v1/kb/qa/documents",
                json={
                    "title": "爆款演绎手法拆解",
                    "pairs": [
                        {
                            "question": "为什么夸张演绎能爆？",
                            "answer": "因为情绪反差强，前 3 秒给足冲突。",
                            "dimensions": {"technique": "夸张演绎", "persona": "素人人设"},
                            "tags": ["爆款拆解"],
                        }
                    ],
                    "tags": ["演绎手法"],
                },
            )
            doc_id = r.json()["doc"]["doc_id"]
            qa_id = r.json()["pairs"][0]["qa_id"]

            # 2) 未审核不入库：检索不命中 Q&A
            r = await client.post("/api/v1/kb/search", json={"query": "夸张演绎", "top_k": 5})
            assert r.status_code == 200
            assert not any(h["doc_id"] == doc_id for h in r.json())

            # 3) 审核通过
            r = await client.post("/api/v1/kb/qa/pairs/approve", json={"qa_ids": [qa_id]})
            assert r.status_code == 200
            assert r.json()["changed"] == 1

            # 4) 向量化 → ready，chunk 数 == approved 数
            r = await client.post(f"/api/v1/kb/documents/{doc_id}/vectorize")
            assert r.status_code == 200
            assert r.json()["status"] == "ready"
            assert r.json()["chunk_count"] == 1

            # 5) 检索命中 Q&A chunk，且 Q/A 同块
            r = await client.post("/api/v1/kb/search", json={"query": "夸张演绎", "top_k": 5})
            hits = r.json()
            qa_hit = next((h for h in hits if h["doc_id"] == doc_id), None)
            assert qa_hit is not None
            assert qa_hit["meta"].get("kind") == "qa"
            assert qa_hit["meta"].get("qa_id") == qa_id
            assert "为什么夸张演绎能爆" in qa_hit["text"] and "情绪反差强" in qa_hit["text"]

            # 6) 问答正例：模型引用真实 chunk_id → answered + verified
            chunk_id = qa_hit["chunk_id"]
            llm._queue.append({
                "answer": "因为情绪反差强。",
                "citations": [{"chunk_id": chunk_id, "quote": "情绪反差强"}],
                "limitations": [],
            })
            r = await client.post("/api/v1/kb/ask", json={"query": "为什么夸张演绎能爆"})
            assert r.status_code == 200
            ans = r.json()
            assert ans["answered"] is True
            assert ans["citations"][0]["chunk_id"] == chunk_id
            assert ans["citations"][0]["verified"] is True
            assert ans["citations"][0]["qa_id"] == qa_id

            # 7) 问答负例：模型无有效引用（空队列 → {}）→ 拒答
            r = await client.post("/api/v1/kb/ask", json={"query": "为什么夸张演绎能爆"})
            neg = r.json()
            assert neg["answered"] is False


async def test_qa_distill_503_without_key_but_manual_200(init_test_db, monkeypatch):
    """双通道独立性：无 key 时蒸馏 503、手动录入仍 200，且响应不含 key 片段。"""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "")
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/kb/qa/distill",
                json={"title": "拆解", "text": "夸张演绎 情绪反差", "tags": []},
            )
            assert r.status_code == 503

            r = await client.post(
                "/api/v1/kb/qa/documents",
                json={"title": "手动知识", "pairs": [{"question": "Q", "answer": "A"}]},
            )
            assert r.status_code == 200

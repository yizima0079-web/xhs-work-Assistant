"""知识库两阶段 API 集成（ASGI + 假适配器，全程离线）。

验证 HTTP 层接线：multipart 文件解析 → 手动文档清洗存 pending（503 路径）→
单个/批量向量化 → ready 可检索 → 单个/批量删除 → 404。配合单元层已覆盖的
service 语义，端到端不依赖真实模型/向量 key。
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


async def test_kb_manual_upload_clean_vectorize_search_delete(init_test_db, make_llm):
    md = "# AI 眼镜\n\n## 佩戴体验\n- 防蓝光实测有效\n- 长时间佩戴偏重"
    llm = make_llm([md])
    app = create_app(llm_adapter=llm, embedding_adapter=FakeVec())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            # 1) multipart 文件解析 → 纯文本回填（不落库）
            r = await client.post(
                "/api/v1/kb/upload",
                files={"file": ("笔记.md", ("# 原始文件内容\n\n要点").encode("utf-8"), "text/markdown")},
            )
            assert r.status_code == 200
            assert r.json()["filename"] == "笔记.md"
            assert r.json()["text"] == "# 原始文件内容\n\n要点"

            # 2) 手动文段：清洗存 pending，未向量化
            raw = "姐妹们 昨天试了AI眼镜 绝了哈 防蓝光有效 戴久偏重啦"
            r = await client.post(
                "/api/v1/kb/documents",
                json={"title": "AI 眼镜实测", "text": raw, "tags": ["AI", "眼镜"], "url": "https://x/1"},
            )
            assert r.status_code == 200
            doc = r.json()
            assert doc["status"] == "pending" and doc["chunk_count"] == 0
            assert doc["markdown"] == md  # 入库的是清洗后 markdown
            doc_id = doc["doc_id"]

            # 3) 详情含原文与清洗结果（供悬停预览）
            r = await client.get(f"/api/v1/kb/documents/{doc_id}")
            assert r.status_code == 200
            assert r.json()["raw_text"] == raw and r.json()["markdown"] == md

            # 4) pending 无 chunk → 检索不命中
            r = await client.post("/api/v1/kb/search", json={"query": "防蓝光", "top_k": 5})
            assert r.status_code == 200 and r.json() == []

            # 5) 单个向量化 → ready，落 chunk，检索命中
            r = await client.post(f"/api/v1/kb/documents/{doc_id}/vectorize")
            assert r.status_code == 200
            assert r.json()["status"] == "ready" and r.json()["chunk_count"] >= 1
            r = await client.post("/api/v1/kb/search", json={"query": "防蓝光偏重", "top_k": 5})
            assert r.status_code == 200
            assert any(hit["doc_id"] == doc_id for hit in r.json())

            # 6) 单个删除 → 204，再读 404
            r = await client.delete(f"/api/v1/kb/documents/{doc_id}")
            assert r.status_code == 204
            r = await client.get(f"/api/v1/kb/documents/{doc_id}")
            assert r.status_code == 404


async def test_kb_batch_vectorize_partial_and_batch_delete(init_test_db, make_llm):
    md_a, md_b = "# A\n\n- 要点 A 内容", "# B\n\n- 要点 B 内容"
    llm = make_llm([md_a, md_b])
    app = create_app(llm_adapter=llm, embedding_adapter=FakeVec())
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            a = (await client.post("/api/v1/kb/documents", json={"title": "A", "text": "A 原始"})).json()
            b = (await client.post("/api/v1/kb/documents", json={"title": "B", "text": "B 原始"})).json()

            # 批量向量化：成功 + 未知 id 失败逐条回执，不整体 500
            r = await client.post(
                "/api/v1/kb/documents/vectorize", json={"doc_ids": [a["doc_id"], b["doc_id"], "nope"]}
            )
            assert r.status_code == 200
            by = {x["doc_id"]: x for x in r.json()}
            assert by[a["doc_id"]]["ok"] and by[a["doc_id"]]["status"] == "ready"
            assert by[b["doc_id"]]["ok"]
            assert by["nope"]["ok"] is False and by["nope"]["status"] == "failed"
            assert by["nope"]["error"]

            # 批量删除：只删存在的，回执 deleted/requested
            r = await client.post(
                "/api/v1/kb/documents/delete", json={"doc_ids": [a["doc_id"], b["doc_id"], "missing"]}
            )
            assert r.status_code == 200
            assert r.json() == {"deleted": 2, "requested": 3}
            assert (await client.get("/api/v1/kb/documents")).json() == []


async def test_kb_create_without_llm_key_is_503_and_not_stored(init_test_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "")  # 无 key → 不建 llm/embedding 适配器
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/kb/documents", json={"title": "标题", "text": "未清洗文本不该入库"}
            )
            assert r.status_code == 503  # 无 key 拒绝清洗，绝不落库
            assert (await client.get("/api/v1/kb/documents")).json() == []


def test_status_maps_both_provider_errors_to_502():
    """`_status` 必须对 LLM / 向量两侧的 Provider 错误对称判断。

    回归：此前只 `isinstance(exc, EmbeddingProviderError)`，`ModelProviderError`
    （上游超时 / 断连）落进兜底分支返回 503「未配置」，把「上游不可达」伪装成
    「没配 key」—— 实测排障时被这个错误码带偏过。docstring 一直写着 502，实现没跟上。
    """
    from app.adapters.embedding import EmbeddingNotConfiguredError, EmbeddingProviderError
    from app.adapters.llm import ModelNotConfiguredError, ModelProviderError
    from app.api.routes.knowledge_base import _status

    assert _status(ModelProviderError("上游超时")) == 502        # ← 回归点
    assert _status(EmbeddingProviderError("向量服务不可达")) == 502
    assert _status(ModelNotConfiguredError("未配 key")) == 503
    assert _status(EmbeddingNotConfiguredError("未配 key")) == 503


async def test_llm_provider_error_maps_to_502_not_503(init_test_db):
    """端到端：蒸馏时 LLM 上游超时 → 502，而不是 503。

    走 `/kb/qa/distill` 的 `clean()` → `chat_text()` 路径，验证 `_kb_or_error`
    的映射在真实请求里生效（qa 路由复用 knowledge_base 的同一函数）。
    """
    from app.adapters.llm import ModelProviderError

    class _BoomLlm:
        """已配置但上游不可达的模型适配器。"""

        def is_configured(self) -> bool:
            return True

        async def chat_text(self, messages) -> str:
            raise ModelProviderError("模型服务不可达: ReadTimeout")

        async def chat_json(self, messages) -> dict:
            raise ModelProviderError("模型服务不可达: ReadTimeout")

    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.qa_distiller.llm = _BoomLlm()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/kb/qa/distill", json={"title": "标题", "text": "一段待清洗的素材文本"}
            )
            assert r.status_code == 502  # 上游不可达 = 可重试，不是「未配置」
            assert "ReadTimeout" in r.json()["detail"]  # 错误信息如实透出，便于定位

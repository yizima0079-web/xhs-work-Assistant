"""知识库 API：文件解析上传 / 手动文档清洗存 pending / 逐个与批量向量化 / 删除 / 列表 / 检索。

两阶段语义：POST /kb/documents 只做 LLM 清洗并存 pending（未向量化、不可检索）；
向量化由 POST /kb/documents/{id}/vectorize（单个）或 POST /kb/documents/vectorize（批量）
触发，落 chunk 后 status=ready。未清洗文本（无 key 或模型失败）一律 503 不落库。
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.adapters.embedding import (
    EmbeddingError,
    EmbeddingNotConfiguredError,
    EmbeddingProviderError,
)
from app.adapters.llm import ModelError, ModelNotConfiguredError, ModelProviderError
from app.auth import app_channel
from app.domain.models import KbDocument
from app.schemas.api import (
    KbDocIdsRequest,
    KbFileText,
    KbManualDocumentRequest,
    KbSearchHit,
    KbSearchRequest,
    KbVectorizeResult,
)
from app.services.ingest import extract_text
from app.services.knowledge_base import ReviewGateError

router = APIRouter()


def _status(exc: Exception) -> int:
    """模型/向量层错误统一映射：NotConfigured→503，Provider(网络可重试)→502，其余→503。

    LLM 侧与向量侧必须对称判断。此前只判了 EmbeddingProviderError，
    ModelProviderError（上游超时/断连）落进兜底分支被误报成 503「未配置」，
    把「上游不可达」伪装成「没配 key」——排障时指向完全错误的方向。
    """
    if isinstance(exc, (EmbeddingNotConfiguredError, ModelNotConfiguredError)):
        return 503
    if isinstance(exc, (EmbeddingProviderError, ModelProviderError)):
        return 502
    return 503


async def _kb_or_error(svc, coro):
    """执行 KB 服务调用并把领域错误映射为 HTTP。"""
    try:
        return await coro
    except (KeyError,) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewGateError as exc:  # 红线：未审核内容不得入库 → 409（状态冲突）
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (EmbeddingError, ModelError) as exc:
        raise HTTPException(status_code=_status(exc), detail=str(exc)) from exc


@router.post("/api/v1/kb/upload", response_model=KbFileText)
async def upload_file(file: UploadFile = File(...)) -> KbFileText:
    """解析上传文件（txt/md/docx/pdf）→ 纯文本。仅抽取，不落库。"""
    try:
        data = await file.read()
        text = extract_text(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return KbFileText(filename=file.filename or "", text=text)


@router.post("/api/v1/kb/documents", response_model=KbDocument)
async def add_manual_document(body: KbManualDocumentRequest, request: Request) -> KbDocument:
    """清洗并保存 pending 文档（需模型 key；未清洗文本不进库）。"""
    svc = request.app.state.kb_service
    return await _kb_or_error(
        svc, svc.create_manual_document(body.title, body.text, body.tags, body.url)
    )


@router.get("/api/v1/kb/documents", response_model=list[KbDocument])
async def list_documents(request: Request, limit: int = 50, offset: int = 0) -> list[KbDocument]:
    """app 通道下，源内容被隐藏的文档不返回；manual 文档无源内容，不受影响。"""
    docs = await request.app.state.kb_repo.list_documents(
        limit=limit, offset=offset, app_visible_only=app_channel(request)
    )
    return await request.app.state.kb_service.fill_qa_counts(docs)  # Q&A 文档带对数


@router.get("/api/v1/kb/documents/{doc_id}", response_model=KbDocument)
async def get_document(doc_id: str, request: Request) -> KbDocument:
    doc = await request.app.state.kb_repo.get_document(
        doc_id, app_visible_only=app_channel(request)
    )
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return (await request.app.state.kb_service.fill_qa_counts([doc]))[0]


@router.post("/api/v1/kb/documents/{doc_id}/vectorize", response_model=KbDocument)
async def vectorize_document(doc_id: str, request: Request) -> KbDocument:
    svc = request.app.state.kb_service
    doc = await _kb_or_error(svc, svc.vectorize_document(doc_id))
    # 与 list/get 一致：Q&A 文档回填对数，避免同一 doc 在不同接口字段含义不一致
    return (await svc.fill_qa_counts([doc]))[0]


@router.post("/api/v1/kb/documents/vectorize", response_model=list[KbVectorizeResult])
async def vectorize_documents(body: KbDocIdsRequest, request: Request) -> list[KbVectorizeResult]:
    """批量向量化：单条失败不阻断其余，逐条回执。"""
    svc = request.app.state.kb_service
    outcomes = await svc.vectorize_documents(body.doc_ids)
    return [
        KbVectorizeResult(
            doc_id=o["doc_id"], ok=o["ok"], status=o.get("status", "failed"),
            title=o["doc"].title if o.get("doc") else None,
            error=o.get("error"),
        )
        for o in outcomes
    ]


@router.delete("/api/v1/kb/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, request: Request) -> None:
    svc = request.app.state.kb_service
    await _kb_or_error(svc, svc.delete_document(doc_id))


@router.post("/api/v1/kb/documents/delete", response_model=dict)
async def delete_documents(body: KbDocIdsRequest, request: Request) -> dict:
    svc = request.app.state.kb_service
    deleted = await svc.delete_documents(body.doc_ids)
    return {"deleted": deleted, "requested": len(body.doc_ids)}


@router.post("/api/v1/kb/contents/{content_id}", response_model=KbDocument)
async def vectorize_content(content_id: str, request: Request) -> KbDocument:
    svc = request.app.state.kb_service
    doc = await _kb_or_error(svc, svc.vectorize_content(content_id))
    return (await svc.fill_qa_counts([doc]))[0]


@router.post("/api/v1/kb/analyses/{analysis_id}", response_model=KbDocument)
async def vectorize_analysis(analysis_id: str, request: Request) -> KbDocument:
    svc = request.app.state.kb_service
    doc = await _kb_or_error(svc, svc.vectorize_analysis(analysis_id))
    return (await svc.fill_qa_counts([doc]))[0]


@router.post("/api/v1/kb/search", response_model=list[KbSearchHit])
async def search_kb(body: KbSearchRequest, request: Request) -> list[dict]:
    """app 通道下被隐藏内容的 chunk 命中不到 —— 否则会出现「内容已删掉、检索还
    能搜出来」的漏，用户看到的就是一条已经删掉的内容被命中。"""
    svc = request.app.state.kb_service
    return await _kb_or_error(
        svc,
        svc.search(
            body.query,
            top_k=body.top_k,
            qa_boost=body.qa_boost,
            app_visible_only=app_channel(request),
        ),
    )

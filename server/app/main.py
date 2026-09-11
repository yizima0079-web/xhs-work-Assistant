"""datapp server 入口：FastAPI + 采集后台执行器（单进程，并发=1）。

create_app(adapter=...) 工厂便于测试注入 FakeAdapter。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.adapters.embedding import DashScopeEmbeddingAdapter
from app.adapters.llm import DashScopeModelAdapter
from app.adapters.opencli import OpenCLIAdapter
from app.api.routes import (
    analysis,
    auth,
    browser,
    collection_requests,
    collection_runs,
    contents,
    health,
    knowledge_base,
    qa,
    reports,
    review,
)
from app.auth import app_token_ok, session_user
from app.config import settings
from app.db import init_db
from app.repositories.collection_request import SqliteCollectionRequestRepository
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.repositories.sqlite import (
    SqliteAnalysisRepository,
    SqliteContentRepository,
    SqliteEventRepository,
    SqliteRawAssetRepository,
    SqliteReviewRepository,
    SqliteRunRepository,
)
from app.services.analysis import AnalysisService
from app.services.collector import CollectorService
from app.services.content import ContentService
from app.services.knowledge_base import KnowledgeBaseService
from app.services.qa_agent import QaAgent
from app.services.qa_distiller import QaDistillerService
from app.services.reporting import ReportService
from app.services.review import ReviewService
from app.services.searcher import CorpusSearcher
from app.services.throttle import Throttle


def _make_llm(override=None):
    """惰性装配：无 key 返回 None（服务触达审核/报告才报 503），有 key 才建 httpx 客户端。"""
    if override is not None:
        return override
    if not settings.llm_api_key:
        return None
    return DashScopeModelAdapter(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout
    )


def _make_embedding(override=None):
    """向量适配器惰性装配（key 复用 llm key）。无 key 返回 None → KB 服务 503 不伪造。"""
    if override is not None:
        return override
    if not settings.llm_api_key:
        return None
    return DashScopeEmbeddingAdapter(
        settings.embedding_base_url,
        settings.llm_api_key,
        settings.embedding_model,
        settings.embedding_dimension,
        settings.embedding_timeout,
    )


def create_app(
    adapter=None,
    llm_adapter=None,
    embedding_adapter=None,
    auth_enabled: bool = False,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db()
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        _adapter = adapter or OpenCLIAdapter(settings)
        throttle = Throttle(
            settings.rate_per_second,
            settings.rate_capacity,
            settings.circuit_breaker_threshold,
            settings.rate_jitter,
        )
        run_repo = SqliteRunRepository()
        event_repo = SqliteEventRepository()
        content_repo = SqliteContentRepository()
        review_repo = SqliteReviewRepository()
        raw_repo = SqliteRawAssetRepository()
        analysis_repo = SqliteAnalysisRepository()
        kb_repo = SqliteKnowledgeBaseRepository()
        collection_request_repo = SqliteCollectionRequestRepository()
        collector = CollectorService(
            _adapter, throttle, run_repo, event_repo, content_repo, raw_repo, settings
        )

        _llm = _make_llm(llm_adapter)
        _embedding = _make_embedding(embedding_adapter)
        searcher = CorpusSearcher(content_repo)
        review_service = ReviewService(review_repo, content_repo, _llm, searcher)
        report_service = ReportService(review_repo, content_repo, _llm)
        analysis_service = AnalysisService(analysis_repo, content_repo, _llm, searcher)
        kb_service = KnowledgeBaseService(
            kb_repo, content_repo, analysis_repo, _embedding, _llm, qa_boost=settings.qa_boost
        )
        qa_distiller = QaDistillerService(
            kb_repo, content_repo, analysis_repo, _llm, max_pairs=settings.qa_max_pairs
        )
        qa_agent = QaAgent(
            kb_service, _llm,
            top_k=settings.rag_top_k,
            min_score=settings.rag_min_score,
            max_context_chars=settings.rag_max_context_chars,
        )

        app.state.adapter = _adapter
        app.state.throttle = throttle
        app.state.collector = collector
        app.state.content_service = ContentService(content_repo, settings)
        # /health 的探测缓存（TTL 见 settings.health_cache_ttl），跨请求复用
        app.state.health_cache = {"at": 0.0, "probe": None, "checked_at": ""}
        app.state.run_repo = run_repo
        app.state.event_repo = event_repo
        app.state.content_repo = content_repo
        app.state.review_repo = review_repo
        app.state.raw_repo = raw_repo
        app.state.analysis_repo = analysis_repo
        app.state.kb_repo = kb_repo
        app.state.collection_request_repo = collection_request_repo
        app.state.llm = _llm
        app.state.review_service = review_service
        app.state.report_service = report_service
        app.state.analysis_service = analysis_service
        app.state.kb_service = kb_service
        app.state.qa_distiller = qa_distiller
        app.state.qa_agent = qa_agent
        yield

    app = FastAPI(title="datapp server", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def admin_gate(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/v1/") and path != "/api/v1/health":
            user = session_user(request)
            token_ok = app_token_ok(request)
            # 0) 通道判定：**必须在下面的认证分支之外、无条件算一次**。
            #    那个 `if not user` 块只有 Cookie 校验失败才进入，把判定写在里面会让
            #    管理员 Cookie 请求永远拿不到这个字段 —— web 看板会跟着被过滤掉。
            #    也与 auth_enabled 无关：本地关掉认证时，带了 app 令牌的请求依然按
            #    app 通道收窄（这是「APP 令牌泄露后果可控」的一部分）。
            request.state.app_channel = not user and token_ok
            # 1) 认证端点自身不做会话校验（否则永远登不进去）
            if not path.startswith("/api/v1/auth/") and auth_enabled:
                # app 通道：设备令牌，读写全权（采集模块由 auth.app_token_ok 拦掉）
                if not user and not token_ok:
                    return JSONResponse({"detail": "需要管理员登录"}, status_code=401)
        return await call_next(request)

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(browser.router)
    app.include_router(collection_runs.router)
    app.include_router(collection_requests.router)
    app.include_router(contents.router)
    app.include_router(review.router)
    app.include_router(reports.router)
    app.include_router(analysis.router)
    app.include_router(knowledge_base.router)
    app.include_router(qa.router)
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

    @app.get("/health")
    async def root_health() -> dict:
        return {"status": "ok"}

    return app


settings.media_dir.mkdir(parents=True, exist_ok=True)
app = create_app(auth_enabled=True)

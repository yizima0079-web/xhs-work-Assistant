"""app 端「删除」= 软隐藏：双端可见性分离。

app 是**消费端**、web 看板是**管理端**，两条通道共用同一套端点、同一个库。所以
可见性必须在**服务端按通道**过滤 —— 客户端藏起来不算数，绕过 UI 直接 curl 就能
拿到。本文件锁三件事：

1. **app 通道看不到**：列表 / 详情 / 概览 / 报告 / 审核 / 分析 / 知识库文档 /
   检索命中 / 问答引用都不含被隐藏的内容及其派生数据。
2. **web 通道照常可见**：同一个端点、同一份数据，只换管理员 Cookie 就是全量。
   这是「web 端保持内容、还能把它恢复回 app」的前提 —— 管理员看不见就没有恢复的入口。
3. **默认不过滤**：不传通道标记时行为与改造前一致（仓储存根因，这里单独锁一条）。

测试手法上刻意让**每条断言都是「同端点 + 同数据 + 只换认证通道」**：这样测出来的
差异只可能来自通道判定，不会掺进别的原因。
"""
from __future__ import annotations

import httpx
import pytest

from app.auth import COOKIE_NAME, create_session
from app.config import settings
from app.domain.enums import EvidenceKind, ReviewStatus
from app.domain.models import Analysis, Claim, Content, Evidence, KbChunk, KbDocument, Report
from app.main import create_app
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.repositories.sqlite import (
    SqliteAnalysisRepository,
    SqliteContentRepository,
    SqliteReviewRepository,
)
from tests.test_kb_service import FakeEmbeddingAdapter

APP_TOKEN = "app-token-for-test"
APP_HEADER = "X-Datapp-App-Token"

HIDDEN = "xhs:hidden"
SHOWN = "xhs:shown"
# 两条内容用完全不同的话题词，检索时能分开；断言只看 doc_id 集合，不依赖向量质量
TEXT_HIDDEN = "藏起来的苹果香蕉橘子话题"
TEXT_SHOWN = "露出来的钢铁水泥砖头话题"

HIDDEN_DOC = "doc-hidden"
HIDDEN_CHUNK = "chunk-hidden"
HIDDEN_ANALYSIS = "an-hidden"
HIDDEN_CLAIM = "cl-hidden"
HIDDEN_REPORT = "rp-hidden"


def _client(app, cookies=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", cookies=cookies
    )


def _admin_cookies() -> dict:
    return {COOKIE_NAME: create_session(settings.admin_username)}


@pytest.fixture
def with_app_token(monkeypatch):
    monkeypatch.setattr(settings, "app_token", APP_TOKEN)


@pytest.fixture
async def seeded(init_test_db, make_llm):
    """一隐藏一可见，两边都带齐派生数据（KB 文档 / 分析 / 断言 / 报告）。"""
    emb = FakeEmbeddingAdapter()
    contents = SqliteContentRepository()
    kb = SqliteKnowledgeBaseRepository()
    reviews = SqliteReviewRepository()
    analyses = SqliteAnalysisRepository()

    for cid, text in ((HIDDEN, TEXT_HIDDEN), (SHOWN, TEXT_SHOWN)):
        await contents.upsert(Content(
            content_id=cid, platform="xhs", platform_item_id=cid.split(":")[-1],
            title=text, text=text, review_status=ReviewStatus.APPROVED,
        ))
        doc_id = HIDDEN_DOC if cid == HIDDEN else "doc-shown"
        await kb.add_document(KbDocument(
            doc_id=doc_id, source_type="content", source_id=cid,
            title=text, content_hash=f"h-{doc_id}", status="ready", chunk_count=1,
        ))
        await kb.add_chunks([KbChunk(
            chunk_id=f"{doc_id}-c0", doc_id=doc_id, chunk_index=0, text=text,
            embedding=(await emb.embed([{"text": text}]))[0], meta={"kind": "doc"},
        )])
        await analyses.add(Analysis(
            analysis_id=HIDDEN_ANALYSIS if cid == HIDDEN else "an-shown",
            content_id=cid, verdict="viral", markdown=f"分析-{cid}",
        ))

    await reviews.add_claim(Claim(
        claim_id=HIDDEN_CLAIM, content_id=HIDDEN, text="隐藏内容的断言",
    ))
    await reviews.add_evidence(Evidence(
        evidence_id="ev-hidden", claim_id=HIDDEN_CLAIM,
        source_kind=EvidenceKind.CONTENT, source_ref=SHOWN, excerpt="旁证片段",
    ))
    await reviews.add_report(Report(
        report_id=HIDDEN_REPORT, title="含隐藏内容的报告",
        content_ids=[HIDDEN, SHOWN], markdown="报告正文",
    ))

    return emb


@pytest.fixture
async def hidden(seeded):
    """参数化用例的**前置条件**：HIDDEN 这条已被标记为 app 端隐藏。

    单独提一个 fixture 而不是在每个用例里重复调一遍 —— 参数化的读路径用例有
    十几条，前置条件必须完全一致，否则测出来的差异分不清是通道判定还是构造差异。
    """
    assert await SqliteContentRepository().set_app_hidden(HIDDEN, True)
    return seeded


# ---------------------------------------------------- 核心不变式：响应体里没有它

# app 通道下必须**过滤掉**隐藏项、但端点本身仍正常返回的读路径
FILTERED_PATHS = [
    "/api/v1/contents",
    "/api/v1/contents/summary",
    "/api/v1/contents/{cid}/analyses",
    "/api/v1/reports",
    "/api/v1/kb/documents",
]

# app 通道下必须**等同于不存在**（404，不是 403 —— 403 等于承认「存在但被隐藏」，
# 那就是一个存在性预言机）的读路径
DENIED_PATHS = [
    "/api/v1/contents/{cid}",
    "/api/v1/contents/{cid}/claims",
    "/api/v1/contents/{cid}/review-batches",
    "/api/v1/analyses/{aid}",
    "/api/v1/claims/{claim_id}",
    "/api/v1/reports/{rid}",
    "/api/v1/kb/documents/{doc_id}",
]


def _fill(path: str) -> str:
    return (path
            .replace("{cid}", HIDDEN)
            .replace("{aid}", HIDDEN_ANALYSIS)
            .replace("{claim_id}", HIDDEN_CLAIM)
            .replace("{rid}", HIDDEN_REPORT)
            .replace("{doc_id}", HIDDEN_DOC))


@pytest.mark.parametrize("path", FILTERED_PATHS)
async def test_app_channel_never_returns_hidden_id(
    init_test_db, make_adapter, with_app_token, hidden, path
):
    """**这一条是整套机制的核心不变式**：app 通道的任何响应体里都不该出现
    被隐藏内容的 content_id。

    比逐字段断言更耐改：以后给某个端点加字段、加嵌套结构，只要把隐藏内容的 id
    带出来了，这条就会红 —— 不需要有人记得回来补测。
    """
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.get(_fill(path), headers={APP_HEADER: APP_TOKEN})
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"
    assert HIDDEN not in resp.text, f"{path} 把隐藏内容的 id 泄给了 app: {resp.text}"


@pytest.mark.parametrize("path", DENIED_PATHS)
async def test_app_channel_hidden_returns_404(
    init_test_db, make_adapter, with_app_token, hidden, path
):
    """按 id 直取的入口：隐藏 = 不存在。404 而非 403（不当地存在性预言机）。"""
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.get(_fill(path), headers={APP_HEADER: APP_TOKEN})
    assert resp.status_code == 404, f"{path} -> {resp.status_code} {resp.text}"


# ------------------------------------------------ 同端点同数据，换 Cookie 就是全量

@pytest.mark.parametrize("path", FILTERED_PATHS + DENIED_PATHS)
async def test_web_channel_still_sees_everything(
    init_test_db, make_adapter, with_app_token, hidden, path
):
    """**「web 端保持内容」的硬证据**：同一份数据、同一个端点，换管理员 Cookie。

    这条同时是恢复功能的前提 —— 管理员看不到被隐藏的内容，就没有恢复它的入口。
    """
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app, cookies=_admin_cookies()) as client:
            resp = await client.get(_fill(path))
    assert resp.status_code == 200, f"{path} 对管理员也不可见了: {resp.status_code} {resp.text}"


async def test_web_channel_list_contains_hidden_and_marks_it(
    init_test_db, make_adapter, with_app_token, seeded
):
    """web 通道的列表里，隐藏项**带标记**而不是消失 —— 看板据此显示「app 端已隐藏」。"""
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            await client.post(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
        async with _client(app, cookies=_admin_cookies()) as client:
            resp = await client.get("/api/v1/contents")
    rows = {c["content_id"]: c for c in resp.json()}
    assert HIDDEN in rows, "管理员看不到被 app 隐藏的内容 —— 没有恢复入口了"
    assert rows[HIDDEN]["app_hidden_at"], "隐藏标记没回传，看板无法显示状态"
    assert rows[SHOWN]["app_hidden_at"] is None


# ------------------------------------------------------------ 检索与问答（派生数据）

async def test_hidden_chunk_not_retrievable_on_app_channel(
    init_test_db, make_adapter, with_app_token, seeded
):
    """**内容删了、检索还搜得出来**是最典型的漏。命中集里必须没有它的 doc。

    断言写成「命中集不含该 doc_id」而不是「命中数为 0」：后者依赖向量模型把
    两条内容分得开，前者只依赖过滤本身，是真正要测的东西。
    """
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            await client.post(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
        payload = {"query": TEXT_HIDDEN, "top_k": 5}
        async with _client(app) as client:
            app_hits = await client.post(
                "/api/v1/kb/search", json=payload, headers={APP_HEADER: APP_TOKEN}
            )
        async with _client(app, cookies=_admin_cookies()) as client:
            web_hits = await client.post("/api/v1/kb/search", json=payload)
    app_docs = {h["doc_id"] for h in app_hits.json()}
    web_docs = {h["doc_id"] for h in web_hits.json()}
    assert HIDDEN_DOC not in app_docs, f"隐藏内容的 chunk 仍被 app 检索到: {app_docs}"
    assert HIDDEN_DOC in web_docs, f"管理员也检索不到了（检索本身坏了）: {web_docs}"


async def test_ask_refuses_when_only_hidden_content_matches(
    init_test_db, make_adapter, with_app_token, seeded, make_llm
):
    """问答共用同一个检索视图：隐藏内容的 chunk 进不了命中集，**答案里就没有它**。

    守卫不需要为隐藏单独写一套 —— 命中集里没有它，既有的「无有效引用 → 强制拒答」
    红线自然生效。这里不锁死 `reason`：命中集为空走 `empty_retrieval`，只命中别的
    内容时走 `no_valid_citation`，两条都是拒答，都说明隐藏内容没被引用到。

    **必须注入假模型**（`llm_adapter=make_llm()`）：不传就会走 `_make_llm(None)` →
    `.env` 里的真实 key → 真实外发一次 DashScope 调用。后果有两条：一是把构造内容
    发给了第三方（红线），二是 `answered` 取决于模型能否输出可解析的引用，本用例
    因此变成**不确定**的（实测同一份代码两次运行一过一挂）。同文件其余用例都传了，
    只有这一条漏了。
    """
    app = create_app(
        adapter=make_adapter(),
        llm_adapter=make_llm(),
        embedding_adapter=seeded,
        auth_enabled=True,
    )
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            await client.post(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
            resp = await client.post(
                "/api/v1/kb/ask",
                json={"query": TEXT_HIDDEN},
                headers={APP_HEADER: APP_TOKEN},
            )
    body = resp.json()
    assert body["answered"] is False, f"app 拿到了答案：{body}"
    assert body["reason"] in ("empty_retrieval", "no_valid_citation"), f"没走拒答: {body}"
    assert body["citations"] == [], f"引用里带了东西：{body['citations']}"
    assert HIDDEN not in resp.text


# ------------------------------------------------------------------- 隐藏 / 恢复

async def test_hide_then_restore_roundtrip(
    init_test_db, make_adapter, with_app_token, seeded
):
    """完整闭环：app 隐藏 → app 看不到 → web 恢复 → app 重新看到。"""
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            hidden = await client.post(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
            gone = await client.get(
                f"/api/v1/contents/{HIDDEN}", headers={APP_HEADER: APP_TOKEN}
            )
        assert hidden.status_code == 204, hidden.text
        assert gone.status_code == 404

        async with _client(app, cookies=_admin_cookies()) as client:
            restored = await client.delete(f"/api/v1/contents/{HIDDEN}/app-hidden")
        assert restored.status_code == 204, restored.text

        async with _client(app) as client:
            back = await client.get(
                f"/api/v1/contents/{HIDDEN}", headers={APP_HEADER: APP_TOKEN}
            )
    assert back.status_code == 200, "恢复后 app 仍看不到 —— 隐藏标记没被清掉"
    assert back.json()["app_hidden_at"] is None


async def test_app_token_cannot_restore(init_test_db, make_adapter, with_app_token, seeded):
    """**取消隐藏只归管理员 Cookie。**

    设备令牌若能自行恢复，用户点一下「删除」再点一下「恢复」就把整套可见性机制
    绕过去了，隐藏形同虚设。隐藏本身仍放行 —— 那是 app 的本职功能。
    """
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            await client.post(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
            resp = await client.delete(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
    assert resp.status_code == 401, f"设备令牌能自行恢复，隐藏形同虚设: {resp.status_code}"


async def test_hide_nonexistent_404(init_test_db, make_adapter, with_app_token, seeded):
    """不静默成功：隐藏一条不存在的内容必须 404，否则前端会显示「已删除」。"""
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/contents/xhs:nope/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
    assert resp.status_code == 404


async def test_hide_is_idempotent_and_keeps_first_timestamp(
    init_test_db, make_adapter, with_app_token, seeded
):
    """重复隐藏幂等，且**不刷新时间戳** —— 「自何时起被隐藏」要能追溯，
    被一次重放刷成新时间就失去意义了。"""
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app, cookies=_admin_cookies()) as client:
            first = await client.post(f"/api/v1/contents/{HIDDEN}/app-hidden")
            stamp = (await client.get(f"/api/v1/contents/{HIDDEN}")).json()["app_hidden_at"]
            second = await client.post(f"/api/v1/contents/{HIDDEN}/app-hidden")
            again = (await client.get(f"/api/v1/contents/{HIDDEN}")).json()["app_hidden_at"]
    assert first.status_code == 204 and second.status_code == 204
    assert stamp and again == stamp, f"重复隐藏刷新了时间戳: {stamp} -> {again}"


async def test_restore_unhidden_is_noop(
    init_test_db, make_adapter, with_app_token, seeded
):
    """恢复一条本就可见的内容也返回 204（幂等），不报 404 ——
    否则前端下拉刷新时的重复点击会弹一个没有意义的错误。"""
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app, cookies=_admin_cookies()) as client:
            resp = await client.delete(f"/api/v1/contents/{SHOWN}/app-hidden")
    assert resp.status_code == 204


# ------------------------------------------------------ 派生数据的写入侧不被污染

async def test_candidate_search_excludes_hidden(init_test_db, seeded):
    """审核/分析的**候选检索**必须过滤，这一条防的是「写入污染」而非读取泄露。

    候选内容的 title 与正文前 80 字会被写进 `evidence.excerpt`，候选 id 还会进
    `payload.comparison.baseline_content_ids`。不过滤的话，一条已隐藏内容会继续
    「长进」**可见内容**的派生数据里 —— 而那份派生数据 app 是能看到的。
    """
    from app.services.searcher import CorpusSearcher

    repo = SqliteContentRepository()
    await repo.set_app_hidden(HIDDEN, True)
    searcher = CorpusSearcher(repo)
    # 关键词取自隐藏内容的正文，不带 flag 时应当命中它
    leaky = await searcher.find_candidates(TEXT_HIDDEN, limit=8)
    clean = await searcher.find_candidates(TEXT_HIDDEN, limit=8, app_visible_only=True)
    assert HIDDEN in {c.content_id for c in leaky}, "前置条件不成立：关键词本应命中隐藏内容"
    assert HIDDEN not in {c.content_id for c in clean}, "隐藏内容仍会成为可见内容的审核候选"


async def test_repo_defaults_are_unfiltered(init_test_db, seeded):
    """**默认值即安全方向**：不传 `app_visible_only` 时仓储返回全量。

    反过来的默认（默认隐藏）会让 web 看板突然看不见数据，而且只在不走中间件的
    路径上出现 —— 极难排查。这条把方向钉死。
    """
    repo = SqliteContentRepository()
    await repo.set_app_hidden(HIDDEN, True)
    ids = {c.content_id for c in await repo.list()}
    assert HIDDEN in ids, "仓储默认过滤了 —— web 看板会静默丢数据"
    assert await repo.get(HIDDEN) is not None
    assert HIDDEN in {c["content_id"] for c in await repo.summary()}


async def test_evidence_pointing_at_hidden_source_is_dropped(
    init_test_db, make_adapter, with_app_token, seeded
):
    """断言可见，不代表它的**证据**都可见：证据的 `excerpt` 带着来源内容的片段。

    这里 `cl-hidden` 断言本身就属于隐藏内容，所以整条 404；换个角度验证过滤本身 ——
    用一条属于**可见内容**的断言，其证据却指向隐藏内容。
    """
    reviews = SqliteReviewRepository()
    await reviews.add_claim(Claim(
        claim_id="cl-shown", content_id=SHOWN, text="可见内容的断言",
    ))
    await reviews.add_evidence(Evidence(
        evidence_id="ev-leak", claim_id="cl-shown",
        source_kind=EvidenceKind.CONTENT, source_ref=HIDDEN, excerpt="隐藏内容的正文片段",
    ))
    await reviews.add_evidence(Evidence(
        evidence_id="ev-ok", claim_id="cl-shown",
        source_kind=EvidenceKind.CONTENT, source_ref=SHOWN, excerpt="可见内容的正文片段",
    ))
    await SqliteContentRepository().set_app_hidden(HIDDEN, True)

    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.get(
                "/api/v1/claims/cl-shown", headers={APP_HEADER: APP_TOKEN}
            )
        async with _client(app, cookies=_admin_cookies()) as client:
            web = await client.get("/api/v1/claims/cl-shown")
    assert resp.status_code == 200
    kept = {e["evidence_id"] for e in resp.json()["evidence"]}
    assert kept == {"ev-ok"}, f"指向隐藏内容的证据没被滤掉: {kept}"
    assert {e["evidence_id"] for e in web.json()["evidence"]} == {"ev-leak", "ev-ok"}, \
        "管理员那边证据也少了 —— 过滤误伤了 web 通道"


async def test_report_hidden_when_any_referenced_content_hidden(
    init_test_db, make_adapter, with_app_token, seeded
):
    """报告用的是 **any-hidden** 规则，不是 all-hidden。

    报告全文里逐字嵌了每条内容的标题与正文前 600 字（`reporting.py` 拼 prompt，
    渲染的 markdown 又把整个 payload 打印出来），「部分隐藏」在这个载体上无法表达。
    这条报告同时引用了隐藏与可见内容 —— 只要有一条被隐藏，整篇就得藏。
    """
    app = create_app(adapter=make_adapter(), embedding_adapter=seeded, auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            await client.post(
                f"/api/v1/contents/{HIDDEN}/app-hidden", headers={APP_HEADER: APP_TOKEN}
            )
        async with _client(app) as client:
            resp = await client.get(
                f"/api/v1/reports/{HIDDEN_REPORT}", headers={APP_HEADER: APP_TOKEN}
            )
        async with _client(app, cookies=_admin_cookies()) as client:
            web = await client.get(f"/api/v1/reports/{HIDDEN_REPORT}")
    assert resp.status_code == 404, "含隐藏内容的报告仍对 app 可见"
    assert web.status_code == 200, "管理员也看不到这份报告了"

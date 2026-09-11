"""重新分析的「补充视角」注入：只补充观察侧重，不放开判定口径与防幻觉约束。"""
from __future__ import annotations

from app.domain.models import Content
from app.repositories.sqlite import SqliteAnalysisRepository, SqliteContentRepository
from app.services.analysis import AnalysisService
from app.services.prompt_templates import build_analysis_messages
from app.services.searcher import CorpusSearcher
from tests.test_analysis_service import _raw_analysis, make_content


def _msgs(focus: str):
    target = make_content("xhs:a", "AI 眼镜自费实测", "自费买回实测", tags=["AI眼镜"])
    return build_analysis_messages(target, [], focus)


# ---- 纯函数：prompt 组装 ----

def test_no_focus_is_byte_identical_to_baseline():
    """focus 为空时 prompt 必须与改造前完全一致（既有分析方向零漂移）。"""
    plain = _msgs("")
    blank = _msgs("   ")
    assert [m.content for m in plain] == [m.content for m in blank]
    assert "补充关注点" not in plain[0].content
    assert "补充关注点" not in plain[1].content
    assert "用户提供了" not in plain[0].content


def test_focus_lands_in_user_and_scopes_in_system():
    system, user = _msgs("重点看开头 3 秒的钩子设计")
    assert "重点看开头 3 秒的钩子设计" in user.content
    # 关注点只出现在 user 侧；system 只加「限定作用域」的约束句
    assert "重点看开头 3 秒的钩子设计" not in system.content
    assert "不得改变 verdict 判定口径" in system.content
    assert "不得引入给定数据之外的事实或数字" in system.content
    # 固定框架仍在
    assert "verdict 判定它相对同话题内容算爆款(viral)还是平淡(flat)" in system.content


def test_focus_is_stripped():
    _, user = _msgs("   看钩子   ")
    assert "【补充关注点（用户指定·仅补充观察角度）】\n看钩子" in user.content


# ---- 服务：落库 + markdown ----

async def test_analyze_with_focus_persists_and_renders(init_test_db, make_llm):
    repo = SqliteContentRepository()
    await repo.upsert(make_content("xhs:a", "AI 眼镜自费实测", "自费买回实测", tags=["AI眼镜"]))
    llm = make_llm([_raw_analysis([])])
    svc = AnalysisService(
        analysis_repo=SqliteAnalysisRepository(), content_repo=repo, llm=llm,
        searcher=CorpusSearcher(repo),
    )

    analysis = await svc.analyze("xhs:a", focus="对比同期账号的人设差异")

    assert analysis.focus == "对比同期账号的人设差异"
    assert analysis.payload["focus"] == "对比同期账号的人设差异"
    assert "## 补充关注点（用户指定）" in analysis.markdown
    assert "对比同期账号的人设差异" in analysis.markdown
    assert "不改变判定口径" in analysis.markdown

    # 真的传到了模型
    sent = llm.calls[-1]
    assert "对比同期账号的人设差异" in sent[-1].content
    assert "对比同期账号的人设差异" not in sent[0].content  # 关注点不进 system

    # roundtrip：重新读回来 focus 仍在（DB 列生效）
    again = await AnalysisService(
        analysis_repo=SqliteAnalysisRepository(), content_repo=repo, llm=llm,
        searcher=CorpusSearcher(repo),
    ).get(analysis.analysis_id)
    assert again is not None and again.focus == "对比同期账号的人设差异"


async def test_analyze_without_focus_has_no_section(init_test_db, make_llm):
    repo = SqliteContentRepository()
    await repo.upsert(make_content("xhs:a", "AI 眼镜自费实测", "自费买回实测", tags=["AI眼镜"]))
    llm = make_llm([_raw_analysis([])])
    svc = AnalysisService(
        analysis_repo=SqliteAnalysisRepository(), content_repo=repo, llm=llm,
        searcher=CorpusSearcher(repo),
    )

    analysis = await svc.analyze("xhs:a")

    assert analysis.focus == ""
    assert "补充关注点" not in analysis.markdown


async def test_focus_is_truncated_to_500(init_test_db, make_llm):
    repo = SqliteContentRepository()
    await repo.upsert(make_content("xhs:a", "AI 眼镜", "正文", tags=["AI眼镜"]))
    llm = make_llm([_raw_analysis([])])
    svc = AnalysisService(
        analysis_repo=SqliteAnalysisRepository(), content_repo=repo, llm=llm,
        searcher=CorpusSearcher(repo),
    )

    analysis = await svc.analyze("xhs:a", focus="观" * 900)
    assert len(analysis.focus) == 500


async def test_two_analyses_keep_history(init_test_db, make_llm):
    """重新分析是新增记录，旧分析保留可回看。"""
    repo = SqliteContentRepository()
    await repo.upsert(make_content("xhs:a", "AI 眼镜", "正文", tags=["AI眼镜"]))
    llm = make_llm([_raw_analysis([]), _raw_analysis([])])
    svc = AnalysisService(
        analysis_repo=SqliteAnalysisRepository(), content_repo=repo, llm=llm,
        searcher=CorpusSearcher(repo),
    )

    first = await svc.analyze("xhs:a")
    second = await svc.analyze("xhs:a", focus="换个角度")

    items = await svc.list_by_content("xhs:a")
    assert len(items) == 2
    assert {i.analysis_id for i in items} == {first.analysis_id, second.analysis_id}


# ---- API：body 可省略 + focus 透传 ----

async def test_analyze_endpoint_body_is_optional(init_test_db, make_adapter, make_llm):
    """不带 body 的旧调用必须照常工作（向后兼容），focus 落空串。"""
    import httpx

    from app.main import create_app
    from tests.test_api_review import seed
    from tests.test_analysis_service import _raw_analysis

    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([_raw_analysis([])]))
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            r = await client.post("/api/v1/contents/xhs:subject/analyze")
            assert r.status_code == 200, r.text
            assert r.json()["focus"] == ""


async def test_analyze_endpoint_passes_focus(init_test_db, make_adapter, make_llm):
    import httpx

    from app.main import create_app
    from tests.test_api_review import seed
    from tests.test_analysis_service import _raw_analysis

    llm = make_llm([_raw_analysis([])])
    app = create_app(adapter=make_adapter(), llm_adapter=llm)
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            r = await client.post(
                "/api/v1/contents/xhs:subject/analyze",
                json={"focus": "只看开头钩子与评论区互动"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["focus"] == "只看开头钩子与评论区互动"
            assert "## 补充关注点（用户指定）" in r.json()["markdown"]
            assert "只看开头钩子与评论区互动" in llm.calls[-1][-1].content

            # 超长 focus → 422（schema 限 500）
            bad = await client.post(
                "/api/v1/contents/xhs:subject/analyze", json={"focus": "观" * 600}
            )
            assert bad.status_code == 422

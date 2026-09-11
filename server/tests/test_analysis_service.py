"""作品分析服务测试：候选检索 + 模型归因 → 净化 → 落库 + Markdown。"""
from __future__ import annotations

import pytest

from app.adapters.llm import ModelNotConfiguredError, ModelParseError
from app.domain.models import Content
from app.repositories.sqlite import SqliteAnalysisRepository, SqliteContentRepository
from app.services.analysis import AnalysisService, render_analysis_markdown
from app.services.searcher import CorpusSearcher


def make_content(
    cid: str, title: str, text: str, url: str | None = None, tags: list[str] | None = None
) -> Content:
    p, i = cid.split(":", 1)
    return Content(
        content_id=cid, platform=p, platform_item_id=i,
        title=title, text=text, canonical_url=url, tags=tags or [],
    )


async def _seed(init_test_db, docs):
    repo = SqliteContentRepository()
    for d in docs:
        await repo.upsert(d)
    return repo


def _service(content_repo, llm) -> AnalysisService:
    return AnalysisService(
        analysis_repo=SqliteAnalysisRepository(),
        content_repo=content_repo,
        llm=llm,
        searcher=CorpusSearcher(content_repo),
    )


def _raw_analysis(baselines: list[str]) -> dict:
    return {
        "verdict": "viral",
        "summary": "自费实测+反差选材推动互动",
        "topic": "AI 眼镜测评",
        "viral_reasons": [
            {"factor": "自费实测的信任感", "evidence": "点赞 2177 高于同话题", "confidence": 0.9}
        ],
        "flat_reasons": [],
        "hooks": ["自费 2000 块实测 AI 眼镜"],
        "audience": ["数码爱好者"],
        "comparison": {
            "baseline_content_ids": baselines,
            "differentiators": ["同话题多为云评测"],
            "shared_patterns": ["标题都点 AI 眼镜"],
        },
        "suggestions": ["突出成本增加反差"],
        "confidence": 0.85,
        "limitations": ["本地样本有限"],
    }


# ---- 纯函数：Markdown 渲染 ----

def test_render_analysis_markdown_covers_sections():
    target = make_content(
        "xhs:a", "AI 眼镜自费实测", "自费买回 AI 眼镜实测三天", url="https://xhs/a",
        tags=["AI眼镜", "测评"],
    )
    raw = _raw_analysis(["xhs:b"])
    payload = _service_clean(raw, ["xhs:b"])
    md = render_analysis_markdown(payload.model_dump(), content=target, created_at="2026-09-09")
    assert md.startswith("# 作品分析")
    for section in [
        "## 内容快照", "## 判定", "## 话题归类", "## 爆款归因", "## 开头钩子",
        "## 目标人群", "## 同话题横向对比", "## 复刻/改进建议", "## 审计原文",
    ]:
        assert section in md, f"缺段落 {section}"
    assert "爆款" in md
    assert "85%" in md
    assert "`xhs:b`" in md
    assert "AI 眼镜自费实测" in md      # 标题
    assert "自费买回 AI 眼镜实测三天" in md  # 正文摘录


def _service_clean(raw, allowed):
    from app.schemas.analysis import sanitize_analysis_payload
    return sanitize_analysis_payload(raw, analysis_id="an-x", content_id="xhs:a", allowed_baselines=allowed)


# ---- build 编排 ----

async def test_analyze_requires_key(init_test_db):
    content_repo = await _seed(
        init_test_db, [make_content("xhs:a", "AI 眼镜自费实测", "正文文本")]
    )
    with pytest.raises(ModelNotConfiguredError):
        await _service(content_repo, None).analyze("xhs:a")


async def test_analyze_missing_content(init_test_db, make_llm):
    content_repo = await _seed(init_test_db, [make_content("xhs:a", "AI 眼镜", "文本")])
    with pytest.raises(KeyError):
        await _service(content_repo, make_llm()).analyze("xhs:nope")


async def test_analyze_happy_path_persists(init_test_db, make_llm):
    content_repo = await _seed(
        init_test_db,
        [
            make_content(
                "xhs:a", "AI 眼镜自费实测", "自费买回实测，戴上一天的视力变化很明显",
                tags=["AI眼镜", "测评"],
            ),
            make_content(
                "xhs:b", "AI 眼镜到底值不值得买", "同话题内容，也在聊 AI 眼镜值不值",
                tags=["AI眼镜"],
            ),
            make_content(
                "xhs:c", "AI 眼镜上手云测评", "同话题云测评，也在聊 AI 眼镜",
                tags=["AI眼镜"],
            ),
        ],
    )
    llm = make_llm([_raw_analysis(["xhs:b", "xhs:c"])])
    analysis = await _service(content_repo, llm).analyze("xhs:a")

    assert analysis.verdict == "viral"
    assert analysis.content_id == "xhs:a"
    assert set(analysis.compared_with) == {"xhs:b", "xhs:c"}
    payload = analysis.payload
    assert payload["comparison"]["baseline_count"] == 2
    assert payload["viral_reasons"][0]["factor"] == "自费实测的信任感"
    assert analysis.schema_version == "1.0"

    # markdown 内嵌审计原文 + 快照
    assert "## 审计原文" in analysis.markdown
    assert "AI 眼镜自费实测" in analysis.markdown
    assert "视力变化" in analysis.markdown       # 正文摘录可回看
    assert analysis.analysis_id in analysis.markdown

    # 落库可查（get + list_by_content）
    got = await SqliteAnalysisRepository().get(analysis.analysis_id)
    assert got is not None and got.markdown == analysis.markdown
    listed = await SqliteAnalysisRepository().list_by_content("xhs:a")
    assert [x.analysis_id for x in listed] == [analysis.analysis_id]

    # 模型收到了目标内容与候选（防 prompt 缺数据）
    user_content = llm.calls[0][1].content
    assert "目标内容" in user_content
    assert "视力变化" in user_content          # 正文
    assert "xhs:b" in user_content             # 候选基线 id


async def test_analyze_no_candidates_adds_limitation(init_test_db, make_llm):
    # 只有目标内容一条 → 检索不到同话题候选
    content_repo = await _seed(
        init_test_db, [make_content("xhs:a", "独一无二的冷门话题", "没有任何同话题文本")]
    )
    raw = _raw_analysis([])
    llm = make_llm([raw])
    analysis = await _service(content_repo, llm).analyze("xhs:a")
    assert analysis.compared_with == []
    assert analysis.payload["comparison"]["baseline_count"] == 0
    assert any("无同话题候选" in lim for lim in analysis.payload["limitations"])
    assert "同话题横向对比" not in analysis.markdown


async def test_analyze_strips_fabricated_baselines(init_test_db, make_llm):
    content_repo = await _seed(
        init_test_db,
        [
            make_content("xhs:a", "AI 眼镜自费实测", "自费买回实测三天", tags=["AI眼镜"]),
            make_content("xhs:b", "AI 眼镜云测评", "同话题云测评也在聊 AI 眼镜", tags=["AI眼镜"]),
        ],
    )
    raw = _raw_analysis(["xhs:b", "xhs:ghost", "xhs:c"])   # ghost/c 越界
    analysis = await _service(content_repo, make_llm([raw])).analyze("xhs:a")
    assert analysis.compared_with == ["xhs:b"]
    assert analysis.payload["comparison"]["baseline_count"] == 1


async def test_analyze_unparseable_structure_raises(init_test_db, make_llm):
    content_repo = await _seed(
        init_test_db,
        [make_content("xhs:a", "AI 眼镜自费实测", "自费实测", tags=["AI眼镜"])],
    )
    llm = make_llm(["不是对象"])
    with pytest.raises(ModelParseError):
        await _service(content_repo, llm).analyze("xhs:a")

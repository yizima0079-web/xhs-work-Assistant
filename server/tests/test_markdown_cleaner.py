"""入库清洗测试：preprocess 确定性规则 + clean 混合管线（FakeModelAdapter 离线）。

规则层对齐 .claude/skills/markdown-normalize/SKILL.md：去时间戳/套话/语气词/emoji，
绝不删除实义字；LLM 层只负责结构化，缺模型即 503，空结果绝不入库。
"""
from __future__ import annotations

import pytest

from app.adapters.llm import ModelNotConfiguredError, ModelParseError
from app.services.markdown_cleaner import clean, preprocess


# ---- 规则层 preprocess ----

def test_preprocess_strips_timestamps_filler_particles_emoji():
    raw = (
        "哈喽大家好 家人们😍 2024-01-02 昨天下午 12:30 试了AI眼镜 绝了哈 点个赞吧 "
        "一键三连 记得收藏\n\n\n第一款299 防蓝光实测可以\n但佩戴久了有点重啦，还会掉！"
    )
    out = preprocess(raw)
    assert "2024" not in out          # 绝对时间戳
    assert "12:30" not in out         # 时钟时间戳
    assert "哈喽大家好" not in out     # 主播套话
    assert "点个赞吧" not in out and "一键三连" not in out and "记得收藏" not in out
    assert "😍" not in out            # emoji
    assert "啦" not in out and "重，" in out  # 句末语气词仅剔"啦"，保留其后的标点与实义字
    assert "第一款299" in out and "防蓝光实测可以" in out and "还会掉" in out  # 事实保留
    assert "\n\n\n" not in out        # 多余空行收敛


def test_preprocess_clock_keeps_ratio_words():
    # 只删合法时分，避开 "3:2"、"A:B" 等非时钟表达
    out = preprocess("参数 3:2 比例，视频 4K 60fps，开启时间 12:30 录的")
    assert "12:30" not in out
    assert "3:2" in out and "4K 60fps" in out


def test_preprocess_relative_lead_removed_only_at_start():
    assert preprocess("刚刚 说AI眼镜翻车").startswith("说AI眼镜")
    # 今天/昨天等可能含实义，不做规则删除（交由 LLM 判断）
    out = preprocess("今天试了，昨天已经下单")
    assert "今天试了" in out and "昨天已经下单" in out


def test_preprocess_collapses_blank_lines():
    assert preprocess("a😀b\n\n\n\n  c  ") == "ab\n\nc"  # emoji 去除 + 多余空行收敛为单个空行分隔
    assert preprocess("") == ""


# ---- 混合 clean ----

async def test_clean_runs_llm_and_returns_markdown(make_llm):
    raw = "姐妹们 昨天试了三款AI眼镜 真的绝了哈 第一款防蓝光实测有效 不过戴久了偏重啦"
    md = "# AI 眼镜实测\n\n## 佩戴体验\n- 第一款防蓝光有效\n- 长时间佩戴偏重"
    llm = make_llm([md])
    out = await clean(raw, llm)
    assert out == md
    assert len(llm.calls) == 1  # 恰好一次模型调用


async def test_clean_without_llm_raises_not_configured():
    with pytest.raises(ModelNotConfiguredError):
        await clean("有正文内容 但没配模型", None)


async def test_clean_all_noise_raises_before_llm(make_llm):
    llm = make_llm([])
    with pytest.raises(ModelParseError):  # 规则层已清空 → 不调模型
        await clean("家人们 点个赞吧 一键三连", llm)
    assert llm.calls == []


async def test_clean_empty_model_output_raises_not_stored(make_llm):
    llm = make_llm([""])  # 模型返回空
    with pytest.raises(ModelParseError):
        await clean("试了AI眼镜 防蓝光有效", llm)


async def test_clean_garbage_model_output_raises_not_stored(make_llm):
    llm = make_llm(["😀😀😀   \n\n\n  "])  # 模型输出经规则层只剩空
    with pytest.raises(ModelParseError):
        await clean("试了AI眼镜 防蓝光有效", llm)

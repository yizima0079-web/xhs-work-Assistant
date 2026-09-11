"""标准化器：真实 OpenCLI fixtures -> 统一 Content。

fixtures 为 2026-09-09 实测输出：note=8 字段 SSR 全量；search=3 条轻量行；
feed/user 为按实测形状构造的样本。
"""
from __future__ import annotations

from app.domain.enums import ContentType
from app.services.normalizer import extract_author_id, extract_note_id, normalize_rows

URL = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=test"
NOTE_ID = "0123456789abcdef01234567"


def test_extract_note_id():
    assert extract_note_id(URL) == NOTE_ID
    assert extract_note_id(None) is None
    assert extract_note_id("https://example.com/no-id") is None
    # search_result 尾段同样可解
    assert (
        extract_note_id("https://www.xiaohongshu.com/search_result/69d22c7e0000000021005489?xsec_token=x")
        == "69d22c7e0000000021005489"
    )


def test_extract_author_id():
    assert (
        extract_author_id("https://www.xiaohongshu.com/user/profile/64af6cb2000000002a0358c9?xsec_token=x")
        == "64af6cb2000000002a0358c9"
    )
    assert extract_author_id(None) is None


def test_normalize_search(load_fixture):
    cs = normalize_rows("search", load_fixture("search.json"))
    assert len(cs) == 3  # 真实：3 条
    c = cs[0]
    assert c.content_id == "xhs:69d22c7e0000000021005489"
    assert c.title == "第一次买智能眼镜千万别冲动！买前必看"
    assert c.author_name == "我有异见"
    assert c.author_id == "64af6cb2000000002a0358c9"  # author_url 反解
    assert c.engagement.likes == 183  # 数值归一（int，非 '183' 串）
    assert c.engagement.comments is None  # 列表行无互动明细
    assert c.tags == []
    assert c.cover_url is None
    assert c.published_at == "2026-04-05"
    # 横向对比基准：engagement 里其余空位保持 None
    assert c.engagement.shares is None


def test_normalize_note_full_fields(load_fixture):
    """真实 note SSR：8 核心字段全量入库。"""
    cs = normalize_rows("note", load_fixture("note.json"))
    assert len(cs) == 1
    c = cs[0]
    assert c.content_id == "xhs:6a40c127000000001603d24d"
    assert c.content_type == ContentType.VIDEO
    assert c.title == "自费测评AI眼镜👓，我们真的需要一副吗？"
    assert c.author_name == "昊吃的纸哥"
    assert c.author_id == "5c11e47b44363b5e471bab32"
    assert c.cover_url and c.cover_url.startswith("http")
    assert "AI眼镜" in c.tags and "测评" in c.tags  # 去 # 前缀
    assert c.text.startswith("从2025年开始")
    # 678 动画数据源：整数
    assert c.engagement.likes == 2177
    assert c.engagement.collects == 702
    assert c.engagement.comments == 90
    assert c.engagement.shares == 102
    assert c.published_at == "2026-06-28T06:37:27.000Z"


def test_normalize_note_legacy_kv_shape():
    """旧版 note [{field,value}] 形状仍兼容。"""
    rows = [
        {"field": "title", "value": "标题"},
        {"field": "author", "value": "作者"},
        {"field": "content", "value": "正文"},
        {"field": "likes", "value": "1.2万"},
        {"field": "collects", "value": "456"},
        {"field": "comments", "value": "78"},
        {"field": "tags", "value": "#AI眼镜, #测评"},
    ]
    cs = normalize_rows("note", rows, source_url=URL)
    assert len(cs) == 1
    c = cs[0]
    assert c.title == "标题"
    assert c.text == "正文"
    assert c.engagement.likes == 12000
    assert c.engagement.collects == 456
    assert c.engagement.comments == 78
    assert c.tags == ["AI眼镜", "测评"]


def test_parse_count_variants():
    from app.services.normalizer import _parse_count

    assert _parse_count("1.2万") == 12000
    assert _parse_count("3.5万") == 35000
    assert _parse_count("2177") == 2177
    assert _parse_count("1,234") == 1234
    assert _parse_count("12k") == 12000
    assert _parse_count("896") == 896
    assert _parse_count(896) == 896
    assert _parse_count("1.2万+") == 12000
    assert _parse_count(None) is None
    assert _parse_count("--") is None


def test_normalize_feed_type(load_fixture):
    cs = normalize_rows("feed", load_fixture("feed.json"))
    assert cs[0].content_type == ContentType.VIDEO
    assert cs[0].engagement.likes == 99


def test_normalize_user_normal_type(load_fixture):
    cs = normalize_rows("user", load_fixture("user.json"))
    assert cs[0].content_type == ContentType.NOTE  # type: normal -> 图文 note
    assert cs[0].engagement.likes == 50
    assert cs[0].cover_url.startswith("http")


def test_normalize_comments_no_content(load_fixture):
    assert normalize_rows("comments", load_fixture("comments.json")) == []

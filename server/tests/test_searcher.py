"""切词 + 本地语料库交叉检索测试。"""
from __future__ import annotations

import pytest

from app.domain.models import Content
from app.repositories.sqlite import SqliteContentRepository
from app.services.searcher import CorpusSearcher, split_keywords


def make_content(content_id: str, text: str) -> Content:
    platform, item = content_id.split(":", 1)
    return Content(content_id=content_id, platform=platform, platform_item_id=item, text=text)


def test_split_keywords_short_chinese_keeps_whole():
    assert split_keywords("咖啡因") == ["咖啡因"]
    assert split_keywords("奶茶") == ["奶茶"]


def test_split_keywords_long_chinese_ngrams():
    toks = split_keywords("咖啡可能造成心悸")
    assert "咖啡可能造成心悸" not in toks  # 长串整段连续命中率低，不单作 token
    assert "咖啡" in toks
    assert "心悸" in toks
    assert "可能" not in toks  # 纯停用字 2-gram 被滤
    assert len(toks) > 2


def test_split_keywords_stop_and_noise():
    assert split_keywords("的了 是在 呀") == []
    assert split_keywords("咖啡 100% 有效") == ["咖啡", "100", "有效"]


def test_split_keywords_cjk_mixed_with_ascii():
    toks = split_keywords("iPhone 拍照评测")
    assert "iphone" in toks
    assert "拍照评测" in toks  # 4 字内整段作词


def test_split_keywords_dedupe_and_cap():
    toks = split_keywords("咖啡咖啡咖啡" * 20)
    assert len(set(toks)) == len(toks)
    assert len(toks) <= 12


@pytest.fixture
async def corpus(init_test_db):
    repo = SqliteContentRepository()
    docs = [
        make_content("xhs:doc1", "咖啡对提神有帮助，但长期大量饮用可能造成心悸。"),
        make_content("xhs:doc2", "绿茶含咖啡因，能促进代谢与提神。"),
        make_content("xhs:doc3", "牛奶补钙，睡前一杯助眠。"),
    ]
    for d in docs:
        await repo.upsert(d)
    return repo


async def test_find_candidates_hits_corpus(corpus):
    searcher = CorpusSearcher(corpus)
    hits = await searcher.find_candidates("咖啡因提神")
    ids = {c.content_id for c in hits}
    assert "xhs:doc1" in ids or "xhs:doc2" in ids


async def test_find_candidates_excludes_self(corpus):
    searcher = CorpusSearcher(corpus)
    hits = await searcher.find_candidates("牛奶助眠", exclude_content_id="xhs:doc3")
    assert all(c.content_id != "xhs:doc3" for c in hits)
    assert hits == []  # 语料里只有 doc3 命中


async def test_find_candidates_empty_query(corpus):
    searcher = CorpusSearcher(corpus)
    assert await searcher.find_candidates("   了 的") == []


async def test_find_candidates_limit(corpus):
    searcher = CorpusSearcher(corpus)
    # 全语料命中但 limit 兜底
    hits = await searcher.find_candidates("咖啡 绿茶 牛奶 提神 补钙 助眠 心悸", limit=2)
    assert len(hits) <= 2

"""脱敏纯函数：签名参数不得进日志/事件/报告文本。"""
from __future__ import annotations

from app.services.redact import redact_text, redact_url

_URL = (
    "https://www.xiaohongshu.com/explore/abc123"
    "?xsec_token=SECRETTOKEN&xsec_source=pc_search&type=normal"
)


def test_redact_url_masks_sensitive_params_only():
    out = redact_url(_URL)
    assert "SECRETTOKEN" not in out
    assert "xsec_token=***" in out
    # 非敏感参数保留，否则排查来源时反而没线索
    assert "xsec_source=pc_search" in out
    assert "type=normal" in out
    assert out.startswith("https://www.xiaohongshu.com/explore/abc123?")


def test_redact_url_leaves_plain_url_untouched():
    plain = "https://www.xiaohongshu.com/explore/abc123"
    assert redact_url(plain) == plain


def test_redact_url_survives_garbage():
    assert redact_url("not a url") == "not a url"


def test_redact_text_masks_every_embedded_url():
    text = "note 失败 https://x.com/a?xsec_token=S1 exit=1；重试 https://x.com/b?sign=S2"
    out = redact_text(text)
    assert "S1" not in out and "S2" not in out
    assert "note 失败" in out
    assert "exit=1" in out


def test_redact_text_passthrough():
    assert redact_text(None) is None
    assert redact_text("") == ""
    assert redact_text("没有链接") == "没有链接"

"""文件导入解析测试：extract_text 对 txt/md/docx/pdf 抽文本 + 编码回退 + 错误路径。

docx 用 python-docx 在内存构造；pdf 用字节级最小合法 PDF（带真实文本流）。
上传层只抽文本不落库；扫描 PDF 无文本层 / 老 .doc 走明确报错提示。
"""
from __future__ import annotations

import io

import pytest

from app.services.ingest import extract_text


def _build_docx(*paragraphs: str) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_pdf(text: str) -> bytes:
    """字节级最小合法 PDF：Catalog/Pages/Page/Contents(Helvetica 文本流)/Font。"""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        None,  # contents stream，长度占位下面填
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode("latin-1") + b") Tj ET"
    objs[3] = b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream)

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, o)
    xref_pos = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % xref_pos
    return bytes(out)


def test_txt_utf8_and_bom():
    assert extract_text("a.txt", "纯文本正文".encode("utf-8")) == "纯文本正文"
    bom = b"\xef\xbb\xbf" + "带 BOM 的文本".encode("utf-8")
    assert extract_text("note.md", bom) == "带 BOM 的文本"


def test_txt_gbk_fallback():
    text = "第一章 中文笔记正文"
    assert extract_text("a.txt", text.encode("gbk")) == text  # utf-8 解码失败 → gb18030 回退


def test_md_markdown_passthrough():
    md = "# 标题\n\n- 要点一\n- 要点二"
    assert extract_text("doc.markdown", md.encode("utf-8")) == md


def test_docx_paragraphs_joined():
    data = _build_docx("第一段", "第二段", "第三段")
    assert extract_text("doc.docx", data) == "第一段\n第二段\n第三段"


def test_docx_empty_raises():
    with pytest.raises(ValueError):
        extract_text("blank.docx", _build_docx())


def test_pdf_text_layer_extracted():
    data = _build_pdf("Uploadable PDF body text 2024")
    assert "PDF body text 2024" in extract_text("scan.pdf", data)


def test_unsupported_extension_raises():
    with pytest.raises(ValueError):
        extract_text("a.xlsx", b"xx")
    with pytest.raises(ValueError):
        extract_text("noext", b"xx")


def test_legacy_doc_raises_with_hint():
    with pytest.raises(ValueError) as exc:
        extract_text("old.doc", b"\xd0\xcf\x11\xe0")
    assert "另存为 .docx" in str(exc.value)


def test_empty_file_raises():
    with pytest.raises(ValueError):
        extract_text("a.txt", b"")

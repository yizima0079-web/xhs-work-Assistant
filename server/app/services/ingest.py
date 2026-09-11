"""文件导入解析：txt/md/docx/pdf -> 纯文本（不落库，交由前端编辑后走清洗）。

- .txt/.md/.markdown：UTF-8 解码（带 BOM 容错），回退 GBK。
- .docx：python-docx 段落拼合。
- .pdf：pypdf 逐页抽文本；扫描版无文本层返回空串由上层提示手工录入。
- .doc（OLE 老格式）：不解析，明确提示转 .docx。
上传只抽取文本；模型/入库仍在两阶段清洗后执行，绝不把未处理文本直接进库。
"""
from __future__ import annotations

import io
from pathlib import Path

_SUPPORTED_EXT = {".txt", ".md", ".markdown", ".docx", ".pdf", ".doc"}
_DOC_LEGACY_HINT = "暂不支持老版 .doc（OLE 二进制），请用 Word 另存为 .docx 后再上传"


def _decode_utf8_or_gbk(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码，请转存为 UTF-8")


def extract_text(filename: str, data: bytes) -> str:
    """按扩展名把文件字节抽成纯文本。不支持/空内容抛 ValueError。"""
    name = filename or ""
    ext = Path(name).suffix.lower()
    if ext not in _SUPPORTED_EXT:
        raise ValueError(f"不支持的文件类型 {ext or '(无扩展名)'}，支持 {', '.join(sorted(_SUPPORTED_EXT))}")
    if not data:
        raise ValueError("文件为空")
    try:
        if ext == ".doc":
            raise ValueError(_DOC_LEGACY_HINT)
        if ext in (".txt", ".md", ".markdown"):
            return _decode_utf8_or_gbk(data).strip()
        if ext == ".docx":
            return _extract_docx(data)
        if ext == ".pdf":
            return _extract_pdf(data)
        raise ValueError("不支持的文件类型")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 解析库内部错误统一收敛为友好文案
        raise ValueError(f"解析失败（{ext}）：{type(exc).__name__}") from exc


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    paras = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    text = "\n".join(paras)
    if not text.strip():
        raise ValueError("docx 内无可提取的文字（可能只有图片/表格）")
    return text.strip()


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 单页抽失败不阻断整份
            pages.append("")
    text = "\n".join(pages)
    if not text.strip():
        raise ValueError("PDF 无可提取的文本层（疑似扫描件），请改用手动粘贴文段")
    return text.strip()

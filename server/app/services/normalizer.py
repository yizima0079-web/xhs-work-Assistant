"""OpenCLI 各命令输出 -> 统一 Content 模型（手册 §5.2）。

纯函数，便于离线快照单测；字段映射以 2026-09-09 实测契约为准：
- note:   SSR 全字段（唯一 8 字段完整源）
          [{note_id,url,title,desc,author,author_id,author_avatar,type,cover_url,tags[],
            likes,collects,comments,shares,published_at}]；旧版 [{field,value}] 兼容
- search: [{rank,author,author_url,likes,title,url,published_at}]（无 cover/type/互动明细）
- feed:   [{id,title,type,author,likes,url}]
- user:   [{id,title,type,likes,cover,url}]（作者名不在行内，url 可反解 author_id）
- collection: [{id,title,author,likes,type,url}]
列表类命令（search/feed/user）是轻量候选；完整 8 字段须由 note 详情命令补抓。
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.domain.enums import ContentType
from app.domain.models import Content, Engagement

_HEX = set("0123456789abcdef")


def extract_note_id(url: str | None) -> str | None:
    """从小红书 URL 提取 note_id（search_result/explore/note/discovery/item 尾段 24hex）。"""
    if not url:
        return None
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    for p in reversed(parts):
        if len(p) == 24 and all(c in _HEX for c in p.lower()):
            return p
    return None


def extract_author_id(author_url: str | None) -> str | None:
    """从 /user/profile/{24hex} 提取作者 id。"""
    if not author_url:
        return None
    path = urlparse(author_url).path
    parts = [p for p in path.split("/") if p]
    try:
        idx = parts.index("profile")
    except ValueError:
        return None
    for p in parts[idx + 1:]:
        if len(p) == 24 and all(c in _HEX for c in p.lower()):
            return p
    return None


def _parse_count(value) -> int | None:
    """互动数值归一：'1.2万'/'3.5万+'/'12k'/'1,234'/'2177'/2177 -> int。无法解析 -> None。

    678 可视化与横向对比都依赖数值化，不能把 '1.2万' 当字符串比大小。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower().replace(",", "")
    s = s.rstrip("+").strip()
    if not s:
        return None
    mult = 1
    tail = s[-1] if s else ""
    if tail in ("万", "w", "k"):
        mult = {"万": 10000, "w": 10000, "k": 1000}[tail]
        s = s[:-1].strip()
    try:
        return int(round(float(s) * mult))
    except ValueError:
        return None


def _content_type(raw) -> ContentType | None:
    if not raw:
        return None
    v = str(raw).lower()
    if v in ("normal", "note", "图文"):
        return ContentType.NOTE
    if v == "video":
        return ContentType.VIDEO
    if v == "image":
        return ContentType.IMAGE
    if v == "mixed":
        return ContentType.MIXED
    return None


def _coerce_tags(v) -> list[str]:
    """tags 兼容三种形态：list[str] / list[{"name"}] / 逗号分隔串（去 '#' 前缀）。"""
    if v is None:
        return []
    if isinstance(v, list):
        out: list[str] = []
        for t in v:
            name = t.get("name") if isinstance(t, dict) else t
            if name is None:
                continue
            s = str(name).strip().lstrip("#").strip()
            if s:
                out.append(s)
        return out
    s = str(v).strip()
    if not s:
        return []
    return [t.strip().lstrip("#").strip() for t in s.replace("，", ",").split(",") if t.strip()]


def _xhs_item(row: dict, source_url: str | None = None) -> Content | None:
    """统一单行 xhs 笔记映射，容错读取各命令可用字段（缺失自动降级 None/[]）。"""
    url = row.get("url") or row.get("canonical_url") or source_url or None
    raw_id = row.get("note_id") or row.get("id") or None
    note_id = str(raw_id).strip() if raw_id is not None else None
    if not note_id:
        note_id = extract_note_id(url)
    if not note_id:
        return None
    author_id = (
        row.get("author_id")
        or row.get("user_id")
        or extract_author_id(row.get("author_url"))
        or None
    )
    tags = _coerce_tags(row.get("tags"))
    return Content(
        content_id=f"xhs:{note_id}",
        platform="xhs",
        platform_item_id=note_id,
        content_type=_content_type(row.get("type")),
        canonical_url=url,
        author_id=author_id,
        author_name=(
            row.get("author") or row.get("author_name") or row.get("nickname") or None
        ),
        published_at=row.get("published_at") or None,
        title=row.get("title") or None,
        text=(
            row.get("desc")
            or row.get("text")
            or row.get("content")
            or row.get("body")
            or None
        ),
        cover_url=row.get("cover_url") or row.get("cover") or None,
        tags=tags,
        engagement=Engagement(
            likes=_parse_count(row.get("likes")),
            comments=_parse_count(row.get("comments") or row.get("comment_count")),
            shares=_parse_count(row.get("shares") or row.get("share_count")),
            collects=_parse_count(row.get("collects") or row.get("collected_count")),
        ),
    )


def _kv_rows_to_dict(rows: list[dict]) -> dict:
    d: dict = {}
    for r in rows:
        f = r.get("field")
        if f is not None:
            d[str(f)] = r.get("value")
    return d


def _note_to_content(rows: list[dict], source_url: str | None) -> Content | None:
    """note 命令输出：新版对象数组 or 旧版 [{field,value}]，统一走 _xhs_item。"""
    if not rows:
        return None
    first = rows[0]
    if not isinstance(first, dict):
        return None
    if "field" in first:
        d = _kv_rows_to_dict(rows)
    else:
        d = dict(first)
    return _xhs_item(d, source_url=source_url or first.get("url"))


def normalize_rows(kind: str, rows: list[dict], source_url: str | None = None) -> list[Content]:
    """把某命令的输出 rows 转成统一 Content 列表。kind 见文件头。"""
    if kind == "search":
        return [c for c in (_xhs_item(r) for r in rows) if c]
    if kind == "feed":
        return [c for c in (_xhs_item(r) for r in rows) if c]
    if kind == "user":
        return [c for c in (_xhs_item(r) for r in rows) if c]
    if kind == "note":
        c = _note_to_content(rows, source_url)
        return [c] if c else []
    # comments 不产出 Content（评论原文落 raw_assets，计数并入 note 的 engagement）
    return []

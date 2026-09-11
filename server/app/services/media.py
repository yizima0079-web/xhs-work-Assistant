"""封面图本地化：把 xhs 远程封面（带 xsec_token、会过期）下载落盘到 storage/media/。

浏览器 <img> 引远程 URL 是防盗链弱、token 过期即裂；落本地后由 /media 静态目录稳定服务。
规则：
- 带 `Referer: https://www.xiaohongshu.com` 防反盗链；
- 扩展名按响应 Content-Type 推断（webp/jpeg/png…），推断失败退 .jpg；
- 任何网络/写盘失败返回 None（采集流程降级保留远程 URL，不阻断）；
- 文件名 = {note_id}.{ext}，重采同 note 覆盖，保证 content 池按 id 复用。
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

_REFERER = "https://www.xiaohongshu.com"
_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
}
_MAX_BYTES = 20 * 1024 * 1024  # 封面单张上限 20MB，防异常大文件拖垮


def _ext_from_url(url: str) -> str | None:
    """从 URL 尾段（如 xxx.webp@w_640）取图片扩展名。"""
    m = re.search(r"\.(jpg|jpeg|png|webp|gif|avif)(?:@|$|\?)", url, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _normalize_ext(url: str, content_type: str | None) -> str:
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in _EXT_BY_MIME:
            return _EXT_BY_MIME[mime]
    return _ext_from_url(url) or "jpg"


async def download_cover(
    url: str,
    note_id: str,
    media_dir: Path,
    timeout: float = 8.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """下载封面到 media_dir，成功返回 /media/{filename}（挂载相对路径），失败返回 None。"""
    if not url or not note_id:
        return None
    client_kwargs: dict = {"timeout": timeout, "follow_redirects": True}
    if transport is not None:
        client_kwargs["transport"] = transport
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url, headers={"Referer": _REFERER, "User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.content
    except httpx.HTTPError:
        return None
    if not data or len(data) > _MAX_BYTES:
        return None

    ext = _normalize_ext(url, resp.headers.get("content-type"))
    filename = f"{note_id}.{ext}"
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / filename).write_bytes(data)
    except OSError:
        return None
    return f"/media/{filename}"


def delete_cover(cover_local: str | None, media_dir: Path) -> bool:
    """删除本地封面文件，返回是否真的删掉了一个文件。

    只接受 `/media/{纯文件名}`：取 basename 后要求它本身不含路径成分，
    避免脏数据或越界值把 cover_local 写成 `../../x` 时误删 media_dir 之外的文件。
    文件不存在/无权限一律返回 False —— 删除内容不该被一个孤儿文件卡住。
    """
    if not cover_local:
        return False
    name = cover_local.rsplit("/", 1)[-1]
    if not name or name in {".", ".."} or name != Path(name).name:
        return False
    try:
        (media_dir / name).unlink()
        return True
    except OSError:
        return False

"""浏览器连接 API：通过 OpenCLI Browser Bridge 连接本机浏览器（Edge/Chrome）。

- GET  /api/v1/browser/status   —— 列出 session 内标签页，检测小红书页面是否已打开。
- POST /api/v1/browser/connect  —— 直接调用 OpenCLI 打开小红书（建立/复用 Bridge 连接）。
- POST /api/v1/browser/retry    —— 先查是否有小红书标签页；有则复用，无则重新打开。

仅 OpenCLI 适配器具备 browser_* 能力；其它（测试 Fake）适配器返回 503。
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from app.schemas.api import BrowserConnectResult, BrowserStatus, BrowserTab

router = APIRouter()

SESSION = "datapp"
XHS_HOME = "https://www.xiaohongshu.com/"


def _is_xhs_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return "xiaohongshu.com" in host or "xhslink.com" in host


def _to_tab(row: dict) -> BrowserTab:
    return BrowserTab(
        index=_as_int(row.get("index")),
        page=str(row.get("page") or "") or None,
        url=str(row.get("url") or "") or None,
        title=str(row.get("title") or "") or None,
        active=bool(row.get("active")),
    )


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _require_browser(adapter) -> None:
    if not (hasattr(adapter, "browser_list_tabs") and hasattr(adapter, "browser_open_page")):
        raise HTTPException(status_code=503, detail="当前适配器不支持浏览器连接（需 OpenCLI Browser Bridge）")


def _fail(res) -> None:
    detail = f"{res.error.code}: {res.error.message}" if res.error else "browser command failed"
    raise HTTPException(status_code=502, detail=detail)


@router.get("/api/v1/browser/status", response_model=BrowserStatus)
async def browser_status(request: Request) -> BrowserStatus:
    adapter = request.app.state.adapter
    _require_browser(adapter)
    res = await adapter.browser_list_tabs(session=SESSION)
    if not res.ok:
        return BrowserStatus(session=SESSION, reachable=False, detail="browser bridge 不可达")
    tabs = [_to_tab(r) for r in (res.rows or [])]
    return BrowserStatus(
        session=SESSION,
        reachable=True,
        tabs=tabs,
        xhs_open=any(_is_xhs_url(t.url or "") for t in tabs),
    )


@router.post("/api/v1/browser/connect", response_model=BrowserConnectResult)
async def browser_connect(request: Request) -> BrowserConnectResult:
    """开始连接：直接调用 OpenCLI 打开小红书首页（open 幂等，可复用已有会话）。"""
    adapter = request.app.state.adapter
    _require_browser(adapter)
    res = await adapter.browser_open_page(session=SESSION, url=XHS_HOME, window="foreground")
    if not res.ok:
        _fail(res)
    url = (res.rows[0].get("url") if res.rows else None) or XHS_HOME
    page = (res.rows[0].get("page") if res.rows else None) or None
    return BrowserConnectResult(
        session=SESSION,
        action="connected",
        xhs_open=_is_xhs_url(url),
        url=url,
        detail=f"page target: {page}" if page else None,
    )


@router.post("/api/v1/browser/retry", response_model=BrowserConnectResult)
async def browser_retry(request: Request) -> BrowserConnectResult:
    """重试：监控本进程 Browser Bridge 内是否已有小红书页面；无则重新打开。"""
    adapter = request.app.state.adapter
    _require_browser(adapter)

    listed = await adapter.browser_list_tabs(session=SESSION)
    if listed.ok:
        for r in (listed.rows or []):
            tab = _to_tab(r)
            if tab.url and _is_xhs_url(tab.url):
                return BrowserConnectResult(
                    session=SESSION,
                    action="reused",
                    xhs_open=True,
                    url=tab.url,
                    detail=f"已存在小红书标签页（index={tab.index}），无需重开",
                )

    res = await adapter.browser_open_page(session=SESSION, url=XHS_HOME, window="foreground")
    if not res.ok:
        _fail(res)
    url = (res.rows[0].get("url") if res.rows else None) or XHS_HOME
    action = "opened" if listed.ok else "connected"
    detail = "已重新打开小红书页面" if listed.ok else "标签页查询失败，已直接打开小红书"
    return BrowserConnectResult(session=SESSION, action=action, xhs_open=True, url=url, detail=detail)

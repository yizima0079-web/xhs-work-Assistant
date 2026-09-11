"""管理员认证：PBKDF2 密码校验 + HMAC 签名 HttpOnly 会话。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Request

from app.config import settings

COOKIE_NAME = "datapp_admin_session"
APP_TOKEN_HEADER = "X-Datapp-App-Token"

# app 设备令牌**不允许**触达的路径前缀：采集任务（创建 / 取消 / 列表 / 详情）
# 与浏览器连接。用前缀而非 (method, path) 精确匹配 ——
# /collection-runs/{run_id}/cancel 含动态段，精确匹配在这里不适用。
# app 端本就不继承采集（设计边界），排除后功能零损失；而令牌是编进 APK 的，
# 拦掉采集就把「可被反编译」与「触发真实平台采集」隔开，守住不超出授权范围。
#
# collection-requests 的尾斜杠是**故意的**，改动时务必保住：
#   POST /api/v1/collection-requests        → 不命中前缀，app 可提交采集**意图**（纯写库、零外呼）
#   GET  /api/v1/collection-requests        → 不命中前缀，app 可看自己申请的进度
#   PATCH /api/v1/collection-requests/{id}  → 命中前缀，app 401，只有管理员 Cookie 能放行
# 即：app 能「申请」，不能「批准」。去掉尾斜杠会把放行权一起交给设备令牌，边界就塌了。
_APP_TOKEN_DENY_PREFIXES = (
    "/api/v1/collection-runs",
    "/api/v1/browser/",
    "/api/v1/collection-requests/",
)

# 设备令牌**不允许**触达的「动作级」排除（按方法 + 路径**后缀**）。
#
# 为什么单列一条而不用上面的前缀表：`/api/v1/contents/` 本身是 app 读详情、读分析、
# 提交审核、隐藏内容的入口，把整个前缀关掉等于把 app 的功能一起关掉；而前缀匹配
# 天然表达不了「只排除这个后缀」。所以这两个维度分开表达。
#
# 只挡 DELETE ——「取消隐藏」是管理端对 app 数据的主导权。若 app 能自行恢复，
# 用户点一下「删除」再点一下「恢复」就绕过了整套可见性机制，隐藏形同虚设。
# 隐藏本身（POST .../app-hidden）仍然放行，那是 app 的本职功能。
_APP_TOKEN_DENY_DELETE_SUFFIXES = ("/app-hidden",)


def app_token_ok(request: Request) -> bool:
    """校验移动端设备令牌：可读可写，但采集模块与「取消隐藏」不放行。

    未配置 → 通道关闭；命中 `_APP_TOKEN_DENY_PREFIXES` / `_APP_TOKEN_DENY_DELETE_SUFFIXES`
    → 不给权限（请求会继续走 Cookie 判定，因此这些端点仍可由管理员 Cookie 正常操作）。
    """
    configured = settings.app_token
    if not configured:
        return False
    if request.url.path.startswith(_APP_TOKEN_DENY_PREFIXES):
        return False
    if request.method == "DELETE" and request.url.path.endswith(
        _APP_TOKEN_DENY_DELETE_SUFFIXES
    ):
        return False
    supplied = request.headers.get(APP_TOKEN_HEADER, "")
    if not supplied:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), configured.encode("utf-8"))


def verify_password(password: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), b"datapp-admin", 310_000).hex()
    return hmac.compare_digest(digest, settings.admin_password_hash)


def create_session(username: str) -> str:
    payload = json.dumps({"sub": username, "exp": int(time.time()) + settings.auth_session_hours * 3600}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(settings.auth_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def app_channel(request: Request) -> bool:
    """本次请求是否来自受「app 可见性」约束的通道（设备令牌）。

    **管理员 Cookie 优先判为 web 通道** —— web 看板是全量视图，要能看到被 app
    隐藏的内容，否则管理员就没有恢复它们的入口了。即便同一个请求顺带带了 app
    令牌，也不该被降级。

    判定由中间件算一次写进 `request.state`，路由只读这个布尔，不重复做签名校验。
    **未设置时按 False（全量）处理**：默认方向必须是「多看见数据」，
    而不是「web 看板突然看不见内容」—— 后者只在不走中间件的路径上出现，极难排查。
    """
    return bool(getattr(request.state, "app_channel", False))


def session_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME, "")
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(settings.auth_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if payload.get("exp", 0) < int(time.time()) or payload.get("sub") != settings.admin_username:
            return None
        return str(payload["sub"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error):
        return None

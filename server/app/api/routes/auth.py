from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import COOKIE_NAME, create_session, session_user, verify_password
from app.config import settings

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/api/v1/auth/login")
async def login(body: LoginRequest, response: Response) -> dict:
    if body.username != settings.admin_username or not verify_password(body.password):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    response.set_cookie(COOKIE_NAME, create_session(body.username), httponly=True, samesite="lax", max_age=settings.auth_session_hours * 3600, path="/")
    return {"authenticated": True, "username": body.username}


@router.post("/api/v1/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}


@router.get("/api/v1/auth/session")
async def session(request: Request) -> dict:
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"authenticated": True, "username": user}

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import (
    SESSION_COOKIE,
    admin_password_hash,
    require_local,
    require_write,
)
from app.core.passwords import hash_password, verify_password
from app.core.session import SESSION_TTL_SECONDS, create_session, parse_session
from app.core.timeutil import utc_now_iso

router = APIRouter(prefix="/auth", tags=["auth"])


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=128)


def _issue_session(request: Request, response: Response) -> str:
    settings = request.app.state.settings
    token, info = create_session(request.app.state.secret)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.secure_cookies,
        path="/",
    )
    return info.csrf


@router.get("/status")
async def auth_status(request: Request) -> dict[str, object]:
    password_set = bool(await admin_password_hash(request))
    authenticated = False
    csrf = ""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        info = parse_session(request.app.state.secret, token)
        if info is not None:
            authenticated = True
            csrf = info.csrf
    return {"password_set": password_set, "authenticated": authenticated, "csrf": csrf}


@router.post("/setup")
async def auth_setup(request: Request, response: Response, body: PasswordBody) -> dict[str, str]:
    require_local(request)
    if await admin_password_hash(request):
        raise HTTPException(status_code=409, detail="口令已设置")
    await request.app.state.db.write(
        "INSERT INTO setting (key, value, updated_at) VALUES ('admin_password', ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (hash_password(body.password), utc_now_iso()),
    )
    csrf = _issue_session(request, response)
    return {"csrf": csrf}


@router.post("/login")
async def auth_login(request: Request, response: Response, body: PasswordBody) -> dict[str, str]:
    stored = await admin_password_hash(request)
    if not stored:
        raise HTTPException(status_code=428, detail="需要先设置口令")
    if not verify_password(body.password, stored):
        raise HTTPException(status_code=401, detail="口令错误")
    csrf = _issue_session(request, response)
    return {"csrf": csrf}


@router.post("/logout")
async def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    require_write(request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}

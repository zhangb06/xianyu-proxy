from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from app.core.session import SessionInfo, parse_session

SESSION_COOKIE = "xy_session"
LOCAL_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "testclient"}


async def admin_password_hash(request: Request) -> str:
    row = await request.app.state.db.fetch_one(
        "SELECT value FROM setting WHERE key = 'admin_password'"
    )
    return str(row["value"]) if row else ""


def get_session(request: Request) -> SessionInfo:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    info = parse_session(request.app.state.secret, token)
    if info is None:
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return info


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    host = request.headers.get("host", "")
    if host and (origin.startswith(f"http://{host}") or origin.startswith(f"https://{host}")):
        return
    raise HTTPException(status_code=403, detail="Origin 校验失败")


def require_admin(request: Request) -> SessionInfo:
    return get_session(request)


def require_write(request: Request) -> SessionInfo:
    """写接口：会话 + CSRF + Origin。"""
    info = get_session(request)
    check_origin(request)
    header = request.headers.get("x-csrf-token", "")
    if not header or not hmac.compare_digest(header.encode(), info.csrf.encode()):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
    return info


def require_local(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="仅允许本机访问")

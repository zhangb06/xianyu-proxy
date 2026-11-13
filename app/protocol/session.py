from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.protocol.mtop import CookieJar, mtop_call

LOGIN_USER_API = "mtop.taobao.idlemessage.pc.loginuser.get"

_EXPIRED_MARKERS = (
    "FAIL_SYS_SESSION_EXPIRED",
    "SESSION_EXPIRED",
    "FAIL_SYS_TOKEN",
    "TOKEN_EXOIRED",
    "令牌过期",
    "请重新登录",
)


@dataclass(frozen=True, slots=True)
class SessionState:
    ok: bool
    expired: bool = False
    message: str = ""


def parse_login_session(body: dict[str, Any]) -> SessionState:
    ret = " ".join(str(item) for item in (body.get("ret") or []))
    if "SUCCESS::" in ret:
        return SessionState(ok=True, message="会话正常")
    if any(marker in ret for marker in _EXPIRED_MARKERS):
        return SessionState(ok=False, expired=True, message=ret or "登录已失效")
    return SessionState(ok=False, message=ret or "会话检查失败")


async def probe_login_session(jar: CookieJar, *, proxy: str = "") -> SessionState:
    body = await mtop_call(jar, LOGIN_USER_API, "1.0", {}, proxy=proxy)
    return parse_login_session(body)
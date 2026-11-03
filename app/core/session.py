from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

SESSION_TTL_SECONDS = 12 * 3600


@dataclass(slots=True)
class SessionInfo:
    csrf: str
    expires_at: float


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session(secret: str, ttl: int = SESSION_TTL_SECONDS) -> tuple[str, SessionInfo]:
    csrf = secrets.token_urlsafe(16)
    payload = {"csrf": csrf, "exp": int(time.time()) + ttl}
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}", SessionInfo(csrf=csrf, expires_at=float(payload["exp"]))


def parse_session(secret: str, token: str) -> SessionInfo | None:
    try:
        body, signature = token.split(".")
    except ValueError:
        return None
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64decode(body))
        csrf = str(payload["csrf"])
        expires_at = float(payload["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires_at < time.time():
        return None
    return SessionInfo(csrf=csrf, expires_at=expires_at)

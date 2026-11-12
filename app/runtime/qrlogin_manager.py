from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.db.repo.accounts import AccountRepo
from app.db.repo.events import log_event
from app.protocol.qrlogin import QRLogin, QRLoginError, QRSession, data_url
from app.runtime.profile import fill_account_nickname

if TYPE_CHECKING:
    from app.runtime.supervisor import Supervisor

QR_TTL_SECONDS = 600.0
POLL_MIN_INTERVAL = 1.0
MAX_SESSIONS = 3


@dataclass(slots=True)
class _ActiveQR:
    client: QRLogin
    session: QRSession
    created_at: float
    last_poll: float
    account_id: int | None = None


class QRLoginManager:
    """管理进行中的扫码登录会话，供 Web 轮询。"""

    def __init__(self, accounts: AccountRepo, supervisor: Supervisor | None = None) -> None:
        self.accounts = accounts
        self.supervisor = supervisor
        self._active: dict[str, _ActiveQR] = {}

    async def start(self, *, timeout: float = 30.0, account_id: int | None = None) -> dict[str, Any]:
        self._sweep()
        if len(self._active) >= MAX_SESSIONS:
            raise QRLoginError("同时进行的扫码登录过多，请稍后再试")
        client = QRLogin(timeout=timeout)
        session = await client.generate()
        self._active[session.session_id] = _ActiveQR(
            client=client,
            session=session,
            created_at=time.monotonic(),
            last_poll=0.0,
            account_id=account_id,
        )
        return {"session_id": session.session_id, "qr": data_url(session)}

    async def status(self, session_id: str) -> dict[str, Any] | None:
        entry = self._active.get(session_id)
        if entry is None:
            return None
        now = time.monotonic()
        if now - entry.created_at > QR_TTL_SECONDS:
            self._active.pop(session_id, None)
            return {"state": "expired"}
        if now - entry.last_poll >= POLL_MIN_INTERVAL:
            entry.last_poll = now
            try:
                await entry.client.poll_once(entry.session)
            except Exception as exc:
                logger.warning(f"扫码状态轮询失败: {exc}")

        session = entry.session
        if session.status == "success":
            self._active.pop(session_id, None)
            return await self._complete(session, entry.account_id)
        if session.status == "verification_required":
            self._active.pop(session_id, None)
            return {"state": "verification_required", "url": session.verification_url or ""}
        return {"state": session.status}

    async def _complete(self, session: QRSession, account_id: int | None = None) -> dict[str, Any]:
        unb = session.unb or str(session.jar.get("unb") or "")
        if not unb:
            await self._log(None, "login.no_unb", "error", {})
            return {"state": "error", "message": "登录成功但未取得 unb"}
        if account_id is not None:
            target = await self.accounts.get(account_id)
            if target is None:
                return {"state": "error", "message": "要重新登录的账号已不存在"}
            if target.xianyu_id != unb:
                await self._log(
                    account_id, "login.mismatch", "warning", {"expected": target.xianyu_id, "scanned": unb}
                )
                return {
                    "state": "error",
                    "message": (
                        f"扫码的是闲鱼号 {unb}，不是要重新登录的 {target.xianyu_id}；"
                        "请用同一账号扫码，本次未新增或修改账号"
                    ),
                }
        account = await self.accounts.upsert(unb)
        await self.accounts.save_cookie(account.id, session.jar.as_dict())
        await self.accounts.set_status(account.id, "new")
        nickname = await fill_account_nickname(self.accounts.db, self.accounts, account.id)
        await self._log(
            account.id,
            "login.success",
            "info",
            {"xianyu_id": unb, "nickname": nickname},
        )
        if self.supervisor is not None:
            self.supervisor.polish.reset_backoff(account.id)
            await self.supervisor.reload()
        return {
            "state": "success",
            "account": {"id": account.id, "xianyu_id": account.xianyu_id, "remark": account.remark},
        }

    async def _log(self, account_id: int | None, type: str, level: str, detail: dict) -> None:
        try:
            await log_event(self.accounts.db, type=type, account_id=account_id, level=level, detail=detail)
        except Exception as exc:
            logger.warning(f"扫码登录事件写库失败: {exc}")

    def _sweep(self) -> None:
        now = time.monotonic()
        stale = [sid for sid, e in self._active.items() if now - e.created_at > QR_TTL_SECONDS]
        for sid in stale:
            self._active.pop(sid, None)

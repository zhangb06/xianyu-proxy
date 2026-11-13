from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.events import log_event
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.session import SessionState, probe_login_session

SESSION_CHECK_SECONDS = 1800.0
SESSION_EXPIRED_MESSAGE = "登录已失效，请重新扫码"

SessionProbe = Callable[..., Awaitable[SessionState]]


@dataclass(slots=True)
class SessionCheckResult:
    checked: int = 0
    expired: int = 0
    recovered: int = 0


class SessionKeeper:
    """定期调用只读接口，合并 Set-Cookie 顺延网页会话，并在失效时标记账号。"""

    def __init__(
        self,
        db: Database,
        accounts: AccountRepo,
        *,
        probe: SessionProbe = probe_login_session,
    ) -> None:
        self.db = db
        self.accounts = accounts
        self._probe = probe

    async def check_all(self) -> SessionCheckResult:
        result = SessionCheckResult()
        for account in await self.accounts.list_enabled():
            try:
                outcome = await self.check_account(account)
            except Exception as exc:
                logger.warning(f"账号 {account.id} 会话保活异常: {exc}")
                continue
            if outcome in ("ok", "recovered"):
                result.checked += 1
            if outcome == "recovered":
                result.recovered += 1
            elif outcome == "expired":
                result.expired += 1
        return result

    async def check_account(self, account: Account) -> str:
        cookie = await self.accounts.load_cookie(account.id)
        if not cookie:
            return "skipped"
        jar = CookieJar(dict(cookie))
        try:
            state = await self._probe(jar, proxy=account.proxy_url)
        except MtopError as exc:
            state = SessionState(ok=False, expired=True, message=str(exc))
        updated = jar.as_dict()
        if updated != cookie:
            await self.accounts.save_cookie(account.id, updated)
        if state.expired:
            if account.status != "error" or account.last_error != SESSION_EXPIRED_MESSAGE:
                await self.accounts.set_status(
                    account.id, "error", last_error=SESSION_EXPIRED_MESSAGE
                )
                await log_event(
                    self.db,
                    type="login.session_expired",
                    account_id=account.id,
                    level="warning",
                    detail={"message": state.message},
                )
            return "expired"
        if not state.ok:
            logger.info(f"账号 {account.id} 会话保活返回非成功: {state.message}")
            return "unknown"
        if account.status == "error" and account.last_error == SESSION_EXPIRED_MESSAGE:
            await self.accounts.set_status(account.id, "new")
            await log_event(
                self.db,
                type="login.session_recovered",
                account_id=account.id,
                level="info",
                detail={},
            )
            return "recovered"
        return "ok"
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from app.core.timeutil import beijing_date, beijing_today, utc_now_iso
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.items import Item, ItemRepo
from app.db.repo.ops import AccountOpsRepo
from app.protocol.listing import STATUS_ON_SALE
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.polish import PolishResult, polish_item

AUTO_POLISH_INTERVAL_SECONDS = 300.0
POLISH_SPACING_SECONDS = 2.0
AUTH_BACKOFF_SECONDS = 1800.0
AUTO_POLISH_LIMIT = 1000

_AUTH_MARKERS = (
    "FAIL_SYS_TOKEN",
    "FAIL_SYS_SESSION",
    "SESSION_EXPIRED",
    "TOKEN_EXOIRED",
    "令牌过期",
    "请重新登录",
)

Polisher = Callable[..., Awaitable[PolishResult]]


class PolishError(RuntimeError):
    """擦亮不可执行或请求失败；auth=True 时自动循环需按账号退避。"""

    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        self.auth = auth


def is_auth_error(message: str) -> bool:
    return any(marker in message for marker in _AUTH_MARKERS)


@dataclass(slots=True)
class PolishOutcome:
    item_id: str
    ok: bool
    already: bool = False
    offline: bool = False
    message: str = ""


@dataclass(slots=True)
class PolishRunResult:
    accounts: int = 0
    polished: int = 0
    already: int = 0
    failed: int = 0


class PolishService:
    """单商品擦亮与每日自动擦亮；手动/自动共享每账号锁与当天判断。"""

    def __init__(
        self,
        db: Database,
        accounts: AccountRepo,
        *,
        polisher: Polisher = polish_item,
    ) -> None:
        self.accounts = accounts
        self.repo = ItemRepo(db)
        self.ops = AccountOpsRepo(db)
        self._polisher = polisher
        self._locks: dict[int, asyncio.Lock] = {}
        self._backoff: dict[int, float] = {}

    def _lock(self, account_id: int) -> asyncio.Lock:
        lock = self._locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_id] = lock
        return lock

    @staticmethod
    def _polished_today(item: Item) -> bool:
        return bool(item.polished_at) and beijing_date(item.polished_at) == beijing_today()

    async def polish(self, account: Account, item_id: str) -> PolishOutcome:
        async with self._lock(account.id):
            item = await self.repo.get(account.id, item_id)
            if item is None:
                raise PolishError("商品不存在")
            if item.status != STATUS_ON_SALE:
                raise PolishError("仅支持在售商品擦亮")
            if self._polished_today(item):
                return PolishOutcome(item_id=item_id, ok=True, already=True, message="今天已擦亮")

            cookie = await self.accounts.load_cookie(account.id)
            if not cookie:
                raise PolishError("账号尚未登录（缺少 Cookie）")
            jar = CookieJar(dict(cookie))
            result: PolishResult | None = None
            error: Exception | None = None
            try:
                result = await self._polisher(jar, item_id, proxy=account.proxy_url)
            except MtopError as exc:
                error = exc
            except Exception as exc:
                error = exc
            updated = jar.as_dict()
            if updated != cookie:
                await self.accounts.save_cookie(account.id, updated)

            if result is None:
                message = str(error) or "擦亮请求失败"
                await self.repo.set_polish(
                    account.id, item_id, polished_at=item.polished_at, error=message
                )
                raise PolishError(message, auth=is_auth_error(message))
            if result.ok:
                await self.repo.set_polish(account.id, item_id, polished_at=utc_now_iso())
                return PolishOutcome(
                    item_id=item_id, ok=True, already=result.already, message=result.message
                )
            await self.repo.set_polish(
                account.id, item_id, polished_at=item.polished_at, error=result.message
            )
            return PolishOutcome(
                item_id=item_id, ok=False, offline=result.offline, message=result.message
            )

    async def run_auto(self) -> PolishRunResult:
        """遍历开启自动擦亮的启用账号，当天未擦亮的在售商品逐个擦亮。"""
        summary = PolishRunResult()
        account_ids = await self.ops.auto_polish_account_ids()
        if not account_ids:
            return summary
        for account_id in account_ids:
            if self._backoff.get(account_id, 0) > time.monotonic():
                continue
            account = await self.accounts.get(account_id)
            if account is None or not account.enabled:
                continue
            if not await self.accounts.load_cookie(account_id):
                continue
            items = await self.repo.list(account_id, status=STATUS_ON_SALE, limit=AUTO_POLISH_LIMIT)
            pending = [item for item in items if not self._polished_today(item)]
            if not pending:
                continue
            summary.accounts += 1
            for index, item in enumerate(pending):
                if index:
                    await asyncio.sleep(POLISH_SPACING_SECONDS)
                try:
                    outcome = await self.polish(account, item.item_id)
                except PolishError as exc:
                    if exc.auth:
                        self._backoff[account_id] = time.monotonic() + AUTH_BACKOFF_SECONDS
                        logger.warning(f"账号 {account_id} 擦亮登录态失效，退避重试: {exc}")
                        break
                    summary.failed += 1
                    logger.warning(f"账号 {account_id} 商品 {item.item_id} 擦亮失败: {exc}")
                    continue
                if not outcome.ok:
                    summary.failed += 1
                    logger.warning(
                        f"账号 {account_id} 商品 {item.item_id} 擦亮失败: {outcome.message}"
                    )
                elif outcome.already:
                    summary.already += 1
                else:
                    summary.polished += 1
        return summary

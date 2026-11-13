from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dtime

from loguru import logger

from app.core.timeutil import beijing_date, beijing_now, beijing_today, utc_now_iso
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.items import Item, ItemRepo
from app.db.repo.ops import AccountOpsRepo
from app.protocol.listing import STATUS_ON_SALE
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.polish import PolishResult, polish_item

AUTO_POLISH_INTERVAL_SECONDS = 60.0
AUTO_POLISH_WINDOW = (9, 12)
POLISH_SPACING_RANGE = (2.0, 6.0)
AUTH_BACKOFF_SECONDS = 1800.0
IDLE_RECHECK_SECONDS = 3600.0
BLOCKED_RECHECK_SECONDS = 1800.0
AUTO_POLISH_LIMIT = 1000

_BEIJING = timezone(timedelta(hours=8))

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


def auto_polish_target(account_id: int, day: str) -> datetime:
    """按 (账号, 北京日期) 确定性随机选取当天擦亮时刻，避免固定节奏。"""
    start = datetime.fromisoformat(day).replace(hour=AUTO_POLISH_WINDOW[0], tzinfo=_BEIJING)
    span = (AUTO_POLISH_WINDOW[1] - AUTO_POLISH_WINDOW[0]) * 3600
    offset = random.Random(f"polish-target:{account_id}:{day}").randrange(span)
    return start + timedelta(seconds=offset)


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
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """账号开关/配置变化时唤醒循环，立即重新计算下次检查时间。"""
        self._wake.set()

    def reset_backoff(self, account_id: int) -> None:
        """重新登录成功后清除退避，允许当天立即补擦。"""
        self._backoff.pop(account_id, None)
        self._wake.set()

    async def wait_for_wake(self, timeout: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except TimeoutError:
            pass

    async def next_check_seconds(self, *, now: datetime | None = None) -> float:
        """按各账号下次到期时间计算休眠时长；当天全部完成后睡到次日窗口开启。"""
        moment = now or beijing_now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_BEIJING)
        today = moment.date().isoformat()
        account_ids = await self.ops.auto_polish_account_ids()
        soonest: float | None = None
        for account_id in account_ids:
            ops = await self.ops.get(account_id)
            if ops.polish_ran_date == today:
                continue
            backoff = self._backoff.get(account_id, 0) - time.monotonic()
            if backoff > 0:
                wait = backoff
            else:
                target = auto_polish_target(account_id, today)
                wait = (
                    (target - moment).total_seconds()
                    if moment < target
                    else BLOCKED_RECHECK_SECONDS
                )
            soonest = wait if soonest is None else min(soonest, wait)
        if soonest is None:
            if not account_ids:
                soonest = IDLE_RECHECK_SECONDS
            else:
                tomorrow = datetime.combine(
                    moment.date() + timedelta(days=1),
                    dtime(AUTO_POLISH_WINDOW[0]),
                    tzinfo=_BEIJING,
                )
                soonest = (tomorrow - moment).total_seconds()
        return max(30.0, min(soonest, 86400.0))

    def _lock(self, account_id: int) -> asyncio.Lock:
        lock = self._locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_id] = lock
        return lock

    @staticmethod
    def _polished_today(item: Item, today: str | None = None) -> bool:
        return bool(item.polished_at) and beijing_date(item.polished_at) == (today or beijing_today())

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
            if is_auth_error(result.message):
                raise PolishError(result.message, auth=True)
            return PolishOutcome(
                item_id=item_id, ok=False, offline=result.offline, message=result.message
            )

    async def polish_all(self, account_id: int) -> PolishRunResult:
        """一键擦亮：按顺序擦完该账号当天未擦的在售商品，已擦的跳过。"""
        account = await self.accounts.get(account_id)
        if account is None:
            raise PolishError("账号不存在")
        if not await self.accounts.load_cookie(account_id):
            raise PolishError("账号尚未登录（缺少 Cookie）")
        today = beijing_today()
        items = await self.repo.list(account_id, status=STATUS_ON_SALE, limit=AUTO_POLISH_LIMIT)
        pending = [item for item in items if not self._polished_today(item, today)]
        summary = PolishRunResult(accounts=1 if pending else 0, already=len(items) - len(pending))
        rng = random.Random()
        for index, item in enumerate(pending):
            if index:
                low, high = POLISH_SPACING_RANGE
                await asyncio.sleep(rng.uniform(low, high))
            try:
                outcome = await self.polish(account, item.item_id)
            except PolishError as exc:
                if exc.auth:
                    self._backoff[account_id] = time.monotonic() + AUTH_BACKOFF_SECONDS
                    raise
                summary.failed += 1
                logger.warning(f"账号 {account_id} 商品 {item.item_id} 擦亮失败: {exc}")
                continue
            if not outcome.ok:
                summary.failed += 1
                logger.warning(f"账号 {account_id} 商品 {item.item_id} 擦亮失败: {outcome.message}")
            elif outcome.already:
                summary.already += 1
            else:
                summary.polished += 1
        return summary

    async def run_auto(self, *, now: datetime | None = None) -> PolishRunResult:
        """每个开启自动擦亮的账号，每天在上午窗口内的随机时刻擦一轮。"""
        summary = PolishRunResult()
        account_ids = await self.ops.auto_polish_account_ids()
        if not account_ids:
            return summary
        moment = now or beijing_now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_BEIJING)
        today = moment.date().isoformat()
        for account_id in account_ids:
            if self._backoff.get(account_id, 0) > time.monotonic():
                continue
            ops = await self.ops.get(account_id)
            if ops.polish_ran_date == today:
                continue
            if moment < auto_polish_target(account_id, today):
                continue
            account = await self.accounts.get(account_id)
            if account is None or not account.enabled:
                continue
            if not await self.accounts.load_cookie(account_id):
                continue
            items = await self.repo.list(account_id, status=STATUS_ON_SALE, limit=AUTO_POLISH_LIMIT)
            pending = [item for item in items if not self._polished_today(item, today)]
            await self.ops.set_polish_ran_date(account_id, today)
            if not pending:
                continue
            summary.accounts += 1
            rng = random.Random(f"polish-spacing:{account_id}:{today}")
            auth_failed = False
            for index, item in enumerate(pending):
                if index:
                    low, high = POLISH_SPACING_RANGE
                    await asyncio.sleep(rng.uniform(low, high))
                try:
                    outcome = await self.polish(account, item.item_id)
                except PolishError as exc:
                    if exc.auth:
                        self._backoff[account_id] = time.monotonic() + AUTH_BACKOFF_SECONDS
                        auth_failed = True
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
            if auth_failed:
                await self.ops.set_polish_ran_date(account_id, "")
        return summary

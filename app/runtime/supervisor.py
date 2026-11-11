from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from loguru import logger

from app.ai.items import ItemInfoService
from app.ai.replier import AIReplyService
from app.config import Settings
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.ops import AccountOpsRepo
from app.delivery.catalog import CatalogError, ItemCatalogService
from app.delivery.order_sync import OrderSyncService
from app.delivery.polish import AUTO_POLISH_INTERVAL_SECONDS, PolishService
from app.delivery.service import DeliveryService
from app.delivery.worker import DeliveryWorker
from app.pipeline.engine import ReplyPipeline
from app.runtime.account_actor import AccountActor, ActorDeps


class Supervisor:
    """账号编排：为启用账号维护 Actor 与发货 worker。"""

    def __init__(self, db: Database, settings: Settings, secret: str) -> None:
        self.settings = settings
        self.accounts = AccountRepo(db, secret)
        self.items = ItemInfoService(self.accounts)
        self.catalog = ItemCatalogService(db, self.accounts)
        self.ai = AIReplyService(db, secret, self.items)
        self.deliveries = DeliveryService(db, self.accounts, self.items)
        self.ops = AccountOpsRepo(db)
        self.polish = PolishService(db, self.accounts)
        self.order_sync = OrderSyncService(db, self.accounts)
        self.pipeline = ReplyPipeline(db, self.ai, self.deliveries)
        self.connect_semaphore = asyncio.Semaphore(max(1, settings.account_concurrency))
        self.actors: dict[int, AccountActor] = {}
        self.workers: dict[int, DeliveryWorker] = {}
        self._catalog_task: asyncio.Task[None] | None = None
        self._catalog_backoff: dict[int, float] = {}
        self._polish_task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        await self.deliveries.reset()
        for account in await self.accounts.list_enabled():
            await self.start_account(account)
        if self.settings.catalog_sync_seconds > 0:
            self._catalog_task = asyncio.create_task(self._catalog_loop())
        self._polish_task = asyncio.create_task(self._polish_loop())

    async def stop(self) -> None:
        self._stopping = True
        for attr in ("_catalog_task", "_polish_task"):
            task = getattr(self, attr)
            setattr(self, attr, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        workers = list(self.workers.values())
        self.workers.clear()
        for worker in workers:
            await worker.stop()
        actors = list(self.actors.values())
        self.actors.clear()
        for actor in actors:
            await actor.stop()

    async def _catalog_loop(self) -> None:
        interval = max(60, self.settings.catalog_sync_seconds)
        try:
            await asyncio.sleep(min(30, interval))
            while not self._stopping:
                try:
                    await self.sync_catalogs()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("商品自动同步失败")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _polish_loop(self) -> None:
        try:
            await asyncio.sleep(min(60, AUTO_POLISH_INTERVAL_SECONDS))
            while not self._stopping:
                try:
                    result = await self.polish.run_auto()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("商品自动擦亮失败")
                    delay = AUTO_POLISH_INTERVAL_SECONDS
                else:
                    if result.accounts:
                        logger.info(
                            f"商品自动擦亮完成 accounts={result.accounts} "
                            f"polished={result.polished} already={result.already} "
                            f"failed={result.failed}"
                        )
                    try:
                        delay = await self.polish.next_check_seconds()
                    except Exception:
                        logger.exception("计算商品擦亮下次检查时间失败")
                        delay = AUTO_POLISH_INTERVAL_SECONDS
                await self.polish.wait_for_wake(delay)
        except asyncio.CancelledError:
            pass

    async def sync_catalogs(self) -> None:
        """为已登录账号定时同步商品：新增入库、下架标记、售出状态更新。"""
        interval = max(60, self.settings.catalog_sync_seconds)
        now = time.monotonic()
        for account in await self.accounts.list_enabled():
            if self._catalog_backoff.get(account.id, 0) > now:
                continue
            if not await self.accounts.load_cookie(account.id):
                continue
            try:
                result = await self.catalog.sync(account)
            except CatalogError as exc:
                self._catalog_backoff[account.id] = now + max(interval * 3, 1800)
                logger.warning(f"商品自动同步失败 account={account.id}: {exc}")
                continue
            except Exception:
                self._catalog_backoff[account.id] = now + max(interval * 3, 1800)
                logger.exception(f"商品自动同步异常 account={account.id}")
                continue
            self._catalog_backoff.pop(account.id, None)
            logger.info(f"商品自动同步完成 account={account.id} fetched={result.fetched}")

    async def start_account(self, account: Account) -> AccountActor:
        existing = self.actors.get(account.id)
        if existing is not None:
            return existing
        deps = ActorDeps(
            settings=self.settings,
            accounts=self.accounts,
            pipeline=self.pipeline,
            connect_semaphore=self.connect_semaphore,
        )
        actor = AccountActor(account, deps)
        self.actors[account.id] = actor
        await actor.start()
        worker = DeliveryWorker(
            account.id, self.deliveries, lambda account_id=account.id: self.sender_for(account_id)
        )
        self.workers[account.id] = worker
        await worker.start()
        logger.info(f"账号 {account.id} 已启动")
        return actor

    async def stop_account(self, account_id: int, *, disabled: bool = False) -> None:
        worker = self.workers.pop(account_id, None)
        if worker is not None:
            await worker.stop()
        actor = self.actors.pop(account_id, None)
        if actor is not None:
            await actor.stop()
        await self.accounts.set_status(account_id, "disabled" if disabled else "offline")

    async def reload(self) -> None:
        """按启用状态差异启停账号。"""
        enabled = {a.id: a for a in await self.accounts.list_enabled()}
        for account_id, account in enabled.items():
            if account_id not in self.actors:
                await self.start_account(account)
        for account_id in list(self.actors):
            if account_id not in enabled:
                await self.stop_account(account_id)
        self.polish.wake()

    def sender_for(self, account_id: int) -> AccountActor | None:
        return self.actors.get(account_id)

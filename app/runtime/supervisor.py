from __future__ import annotations

import asyncio

from loguru import logger

from app.config import Settings
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.pipeline.engine import ReplyPipeline
from app.runtime.account_actor import AccountActor, ActorDeps


class Supervisor:
    """账号编排：为启用账号维护 Actor，崩溃自动重启由 Actor 退避保证。"""

    def __init__(self, db: Database, settings: Settings, secret: str) -> None:
        self.settings = settings
        self.accounts = AccountRepo(db, secret)
        self.pipeline = ReplyPipeline(db)
        self.connect_semaphore = asyncio.Semaphore(max(1, settings.account_concurrency))
        self.actors: dict[int, AccountActor] = {}
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        for account in await self.accounts.list_enabled():
            await self.start_account(account)

    async def stop(self) -> None:
        self._stopping = True
        actors = list(self.actors.values())
        self.actors.clear()
        for actor in actors:
            await actor.stop()

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
        logger.info(f"账号 {account.id} 已启动")
        return actor

    async def stop_account(self, account_id: int, *, disabled: bool = False) -> None:
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

    def sender_for(self, account_id: int) -> AccountActor | None:
        return self.actors.get(account_id)

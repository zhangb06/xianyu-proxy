from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger

from app.delivery.service import (
    MAX_ATTEMPTS,
    DeliveryPermanent,
    DeliveryRetry,
    DeliveryService,
)
from app.pipeline.engine import OutboundSender

POLL_INTERVAL_SECONDS = 1.0
RECONCILE_INTERVAL_SECONDS = 300.0
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 300.0


class DeliveryWorker:
    """单账号 outbox 消费：订单发货、小红花请求、失败退避、周期对账。"""

    def __init__(
        self,
        account_id: int,
        deliveries: DeliveryService,
        sender: Callable[[], OutboundSender | None],
    ) -> None:
        self.account_id = account_id
        self.deliveries = deliveries
        self.sender = sender
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"delivery-{self.account_id}")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except (asyncio.CancelledError, TimeoutError):
                pass

    async def _run(self) -> None:
        last_reconcile = 0.0
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                if loop.time() - last_reconcile > RECONCILE_INTERVAL_SECONDS:
                    last_reconcile = loop.time()
                    await self.deliveries.reconcile(self.account_id)
                task = await self.deliveries.outbox.claim_due(self.account_id)
                if task is None:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
                await self._handle(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"账号 {self.account_id} 发货 worker 异常: {exc}")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _handle(self, task) -> None:  # type: ignore[no-untyped-def]
        sender = self.sender()
        if sender is None:
            await self.deliveries.outbox.retry(task.id, delay_seconds=30, error="账号未运行")
            return
        if task.task == "flower":
            await self._handle_flower(task, sender)
            return
        try:
            outcome = await self.deliveries.execute(task, sender)
        except DeliveryRetry as exc:
            if task.attempts >= MAX_ATTEMPTS:
                await self.deliveries.fail_permanently(task, f"重试 {task.attempts} 次失败：{exc}")
            else:
                delay = min(BACKOFF_BASE_SECONDS * (2 ** (task.attempts - 1)), BACKOFF_CAP_SECONDS)
                await self.deliveries.outbox.retry(task.id, delay_seconds=delay, error=str(exc))
            return
        except DeliveryPermanent as exc:
            await self.deliveries.fail_permanently(task, str(exc))
            return
        except Exception as exc:
            logger.warning(f"账号 {self.account_id} 发货任务 {task.id} 异常: {exc}")
            await self.deliveries.outbox.retry(task.id, delay_seconds=BACKOFF_BASE_SECONDS, error=str(exc))
            return
        await self.deliveries.outbox.done(task.id)
        logger.debug(f"账号 {self.account_id} 发货任务 {task.id} 完成: {outcome.status}")

    async def _handle_flower(self, task, sender) -> None:  # type: ignore[no-untyped-def]
        try:
            outcome = await self.deliveries.execute_flower(task, sender)
        except DeliveryRetry as exc:
            if task.attempts >= MAX_ATTEMPTS:
                await self.deliveries.flower_failed(task, f"重试 {task.attempts} 次失败：{exc}")
            else:
                delay = min(BACKOFF_BASE_SECONDS * (2 ** (task.attempts - 1)), BACKOFF_CAP_SECONDS)
                await self.deliveries.outbox.retry(task.id, delay_seconds=delay, error=str(exc))
            return
        except Exception as exc:
            logger.warning(f"账号 {self.account_id} 小红花任务 {task.id} 异常: {exc}")
            if task.attempts >= MAX_ATTEMPTS:
                await self.deliveries.flower_failed(task, f"重试 {task.attempts} 次失败：{exc}")
            else:
                await self.deliveries.outbox.retry(
                    task.id, delay_seconds=BACKOFF_BASE_SECONDS, error=str(exc)
                )
            return
        await self.deliveries.outbox.done(task.id)
        logger.debug(f"账号 {self.account_id} 小红花任务 {task.id} 完成: {outcome.status}")

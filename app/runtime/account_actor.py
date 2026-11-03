from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.events import log_event
from app.pipeline.engine import OutboundSender, ReplyPipeline, SendResult
from app.protocol.mtop import CookieJar
from app.protocol.ws import WSError, XianyuWS, reject_reason

if TYPE_CHECKING:
    from app.config import Settings

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 300.0


@dataclass(slots=True)
class ActorDeps:
    settings: Settings
    accounts: AccountRepo
    pipeline: ReplyPipeline
    connect_semaphore: asyncio.Semaphore


class AccountActor(OutboundSender):
    """单账号运行时：连接、心跳、消息分发、重连退避。"""

    def __init__(self, account: Account, deps: ActorDeps) -> None:
        self.account = account
        self.deps = deps
        self._task: asyncio.Task[None] | None = None
        self._ws: XianyuWS | None = None
        self._stop = asyncio.Event()

    @property
    def account_id(self) -> int:
        return self.account.id

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"actor-{self.account.id}")

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
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        ws = self._ws
        if ws is None or not ws.connected:
            return SendResult(ok=False, reason="offline")
        try:
            mid = await ws.send_text(chat_id, to_user_id, text)
            response = await ws.wait_result(mid, timeout=5.0)
        except Exception as exc:
            return SendResult(ok=False, reason=str(exc))
        if response is None:
            return SendResult(ok=False, msg_id=mid, reason="no_response")
        reason = reject_reason(response)
        if reason:
            return SendResult(ok=False, msg_id=mid, reason=reason)
        return SendResult(ok=True, msg_id=mid)

    async def _run(self) -> None:
        attempts = 0
        while not self._stop.is_set():
            attempts += 1
            status = "connecting" if attempts == 1 else "reconnecting"
            await self._set_status(status)
            try:
                async with self.deps.connect_semaphore:
                    ws = await self._build_ws()
                    self._ws = ws
                    await ws.connect()
            except WSError as exc:
                # 凭据/令牌问题重试无意义，直接转 error 等待重新扫码
                await self._set_status("error", last_error=str(exc))
                await self._event("ws.auth_failed", "error", {"error": str(exc)})
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"账号 {self.account.id} 连接失败({attempts}/{MAX_ATTEMPTS}): {exc}")
                await self._event("ws.connect_failed", "warning", {"attempt": attempts, "error": str(exc)})
                if attempts >= MAX_ATTEMPTS:
                    await self._set_status("error", last_error=f"连续 {MAX_ATTEMPTS} 次连接失败")
                    return
                await asyncio.sleep(self._backoff(attempts))
                continue

            attempts = 0
            await self._set_status("online")
            await self._event("ws.connected", "info", {})
            try:
                async for message in self._ws_messages():
                    if self._stop.is_set():
                        break
                    outcome = await self.deps.pipeline.handle_inbound(self.account, message, self)
                    if outcome not in ("duplicate", "self_echo", "ignored"):
                        logger.debug(f"账号 {self.account.id} 消息处理结果: {outcome}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"账号 {self.account.id} 消息循环异常: {exc}")
                await self._event("ws.loop_error", "warning", {"error": str(exc)})
            finally:
                ws = self._ws
                self._ws = None
                if ws is not None:
                    await ws.close()

            if self._stop.is_set():
                break
            attempts = 1
            await self._set_status("reconnecting")
            await asyncio.sleep(self._backoff(attempts))

        await self._set_status("offline")

    async def _ws_messages(self):
        ws = self._ws
        if ws is None:
            raise WSError("WebSocket 未连接")
        async for message in ws.iter_messages():
            yield message

    async def _build_ws(self) -> XianyuWS:
        settings = self.deps.settings
        account = await self.deps.accounts.get(self.account.id)
        if account is not None:
            self.account = account
        cookie = await self.deps.accounts.load_cookie(self.account.id)
        if not cookie:
            raise WSError("没有 Cookie，请重新扫码登录")
        return XianyuWS(
            CookieJar(cookie),
            myid=self.account.xianyu_id,
            device_id=self.account.device_id,
            url=settings.ws_url,
            heartbeat_interval=settings.heartbeat_interval,
            http_timeout=settings.http_timeout,
            send_min_interval=settings.send_min_interval,
            proxy=self.account.proxy_url,
        )

    async def _set_status(self, status: str, *, last_error: str = "") -> None:
        try:
            await self.deps.accounts.set_status(self.account.id, status, last_error=last_error)
        except Exception as exc:
            logger.warning(f"账号 {self.account.id} 状态写库失败: {exc}")

    async def _event(self, type: str, level: str, detail: dict) -> None:
        try:
            await log_event(
                self.deps.accounts.db, type=type, account_id=self.account.id, level=level, detail=detail
            )
        except Exception as exc:
            logger.warning(f"账号 {self.account.id} 事件写库失败: {exc}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_CAP_SECONDS)
        return delay * random.uniform(0.5, 1.5)

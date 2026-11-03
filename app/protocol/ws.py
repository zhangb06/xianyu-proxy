from __future__ import annotations

import asyncio
import base64
import inspect
import json
import random
import time
from collections.abc import AsyncIterator
from typing import Any

import websockets
from loguru import logger

from app.core.ids import generate_device_id, generate_mid, generate_uuid
from app.protocol.message import is_chat_message, is_sync_package, parse_chat_message
from app.protocol.mtop import IM_APP_KEY, CookieJar, decode_payload
from app.protocol.token import fetch_im_token

WS_URL = "wss://wss-goofish.dingtalk.com/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class WSError(RuntimeError):
    pass


def _header_kwarg() -> str:
    try:
        params = inspect.signature(websockets.connect).parameters
    except (TypeError, ValueError):
        return "additional_headers"
    return "additional_headers" if "additional_headers" in params else "extra_headers"


class XianyuWS:
    """单账号 IM 长连接，只维护连接与 mid 关联，不保存业务状态。"""

    def __init__(
        self,
        jar: CookieJar,
        *,
        myid: str | None = None,
        device_id: str | None = None,
        url: str = WS_URL,
        heartbeat_interval: float = 15.0,
        http_timeout: float = 30.0,
        send_min_interval: float = 2.0,
    ) -> None:
        self.jar = jar
        self.myid = myid or str(jar.get("unb") or "")
        if not self.myid:
            raise WSError("Cookie 缺少 unb")
        self.device_id = device_id or generate_device_id(self.myid)
        self.url = url
        self.heartbeat_interval = heartbeat_interval
        self.http_timeout = http_timeout
        self.send_min_interval = send_min_interval

        self.token: str | None = None
        self._ws: websockets.ClientConnection | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._messages: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=2000)
        self._seen: set[str] = set()
        self._send_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_send_at = 0.0

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        if not self.token:
            self.token = await fetch_im_token(self.jar, self.device_id, timeout=self.http_timeout)
        if not self.token:
            raise WSError("获取 IM Token 失败，Cookie 可能已失效")

        headers = {"Cookie": self.jar.header(), "User-Agent": _UA}
        self._ws = await websockets.connect(
            self.url,
            **{_header_kwarg(): headers},
            open_timeout=30,
            ping_interval=20,
            ping_timeout=15,
            max_size=8 * 1024 * 1024,
        )
        try:
            await self._register()
        except Exception:
            await self.close()
            raise
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"WS 已连接: myid={self.myid}")

    async def _register(self) -> None:
        await self._send_json(
            {
                "lwp": "/reg",
                "headers": {
                    "cache-header": "app-key token ua wv",
                    "app-key": IM_APP_KEY,
                    "token": self.token,
                    "ua": _UA,
                    "dt": "j",
                    "wv": "im:3,au:3,sy:6",
                    "sync": "0,0;0;0;",
                    "did": self.device_id,
                    "mid": generate_mid(),
                },
            }
        )
        await asyncio.sleep(1)
        timestamp = int(time.time() * 1000)
        await self._send_json(
            {
                "lwp": "/r/SyncStatus/ackDiff",
                "headers": {"mid": generate_mid()},
                "body": [
                    {
                        "pipeline": "sync",
                        "tooLong2Tag": "PNM,1",
                        "channel": "sync",
                        "topic": "sync",
                        "highPts": 0,
                        "pts": timestamp * 1000,
                        "seq": 0,
                        "timestamp": timestamp,
                    }
                ],
            }
        )

    async def _heartbeat_loop(self) -> None:
        failures = 0
        while self._ws is not None:
            try:
                await self._send_json({"lwp": "/!", "headers": {"mid": generate_mid()}})
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                logger.warning(f"心跳失败 {failures}/3: {exc}")
                if failures >= 3:
                    await self.close()
                    return
            await asyncio.sleep(self.heartbeat_interval)

    async def _send_json(self, frame: dict[str, Any]) -> None:
        async with self._write_lock:
            if self._ws is None:
                raise WSError("WebSocket 未连接")
            await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def send_text(self, chat_id: str, to_user_id: str, text: str) -> str:
        content = json.dumps({"contentType": 1, "text": {"text": text}}, ensure_ascii=False)
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        mid = generate_mid()
        frame = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {"mid": mid},
            "body": [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{chat_id}@goofish",
                    "conversationType": 1,
                    "content": {"contentType": 101, "custom": {"type": 1, "data": content_b64}},
                    "redPointPolicy": 0,
                    "extension": {"extJson": "{}"},
                    "ctx": {"appVersion": "1.0", "platform": "web"},
                    "mtags": {},
                    "msgReadStatusSetting": 1,
                },
                {"actualReceivers": [f"{to_user_id}@goofish", f"{self.myid}@goofish"]},
            ],
        }

        async with self._send_lock:
            await self._throttle()
            self._prune_pending()
            self._pending[mid] = asyncio.get_running_loop().create_future()
            try:
                await self._send_json(frame)
            except Exception:
                self._pending.pop(mid, None)
                raise
            self._last_send_at = time.monotonic()

        logger.info(f"已发送文本: chat_id={chat_id}, to={to_user_id}, mid={mid}")
        return mid

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_send_at
        wait = self.send_min_interval - elapsed
        if wait > 0:
            await asyncio.sleep(wait + random.uniform(0, 0.8))

    async def wait_result(self, mid: str, timeout: float = 5.0) -> dict[str, Any] | None:
        future = self._pending.get(mid)
        if future is None:
            return None
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            self._pending.pop(mid, None)

    async def iter_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            message = await self._messages.get()
            if message is None:
                return
            yield message

    async def _reader_loop(self) -> None:
        try:
            async for message in self._iter_parsed():
                self._enqueue(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"WS 读取循环退出: {exc}")
        finally:
            self._signal_end()

    def _enqueue(self, message: dict[str, Any]) -> None:
        """读循环兼管 ACK 与 mid 响应，不能被业务队列反压阻塞。"""
        try:
            self._messages.put_nowait(message)
        except asyncio.QueueFull:
            try:
                self._messages.get_nowait()
                self._messages.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            logger.warning("消息队列已满，丢弃最早一条")

    def _signal_end(self) -> None:
        while True:
            try:
                self._messages.put_nowait(None)
                return
            except asyncio.QueueFull:
                try:
                    self._messages.get_nowait()
                except asyncio.QueueEmpty:
                    return

    async def _iter_parsed(self) -> AsyncIterator[dict[str, Any]]:
        async for frame in self._iter_frames():
            if is_sync_package(frame):
                for item in frame["body"]["syncPushPackage"]["data"]:
                    message = self._decode_item(item)
                    if message is not None and is_chat_message(message):
                        parsed = parse_chat_message(message, self.myid)
                        if parsed and self._mark_seen(parsed["dedup_key"]):
                            yield parsed
            elif is_chat_message(frame):
                parsed = parse_chat_message(frame, self.myid)
                if parsed and self._mark_seen(parsed["dedup_key"]):
                    yield parsed

    async def _iter_frames(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise WSError("WebSocket 未连接")
        async for raw in self._ws:
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            if self._resolve_pending(frame):
                continue
            if self._is_heartbeat_response(frame):
                continue
            await self._send_ack(frame)
            yield frame

    def _resolve_pending(self, frame: dict[str, Any]) -> bool:
        headers = frame.get("headers")
        mid = headers.get("mid") if isinstance(headers, dict) else None
        if not mid:
            return False
        future = self._pending.get(mid)
        if future is None:
            return False
        if not future.done():
            future.set_result(frame)
        return True

    def _prune_pending(self) -> None:
        """响应可能早于 wait_result 到达，已完成项由 wait_result 消费，超量时兜底清理。"""
        if len(self._pending) < 1024:
            return
        for key in [key for key, future in self._pending.items() if future.done()]:
            self._pending.pop(key, None)

    @staticmethod
    def _is_heartbeat_response(frame: dict[str, Any]) -> bool:
        return frame.get("code") == 200 and "body" not in frame and "lwp" not in frame

    async def _send_ack(self, frame: dict[str, Any]) -> None:
        headers = frame.get("headers")
        if not isinstance(headers, dict):
            return
        ack: dict[str, Any] = {
            "code": 200,
            "headers": {
                "mid": headers.get("mid") or generate_mid(),
                "sid": headers.get("sid", ""),
            },
        }
        for key in ("app-key", "ua", "dt"):
            if key in headers:
                ack["headers"][key] = headers[key]
        try:
            await self._send_json(ack)
        except Exception as exc:
            logger.debug(f"ACK 发送失败: {exc}")

    @staticmethod
    def _decode_item(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        raw = item.get("data")
        if not isinstance(raw, str) or not raw:
            return None
        return decode_payload(raw)

    def _mark_seen(self, key: str) -> bool:
        if key in self._seen:
            return False
        if len(self._seen) > 5000:
            self._seen.clear()
        self._seen.add(key)
        return True

    async def close(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        ws = self._ws
        self._ws = None
        if ws is not None:
            await ws.close()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self._signal_end()
        logger.info("WS 已关闭")


def reject_reason(response: dict[str, Any] | None) -> str | None:
    if not isinstance(response, dict):
        return None
    body = response.get("body")
    if not isinstance(body, dict):
        return None
    reason = body.get("reason")
    if not reason:
        return None
    more = body.get("moreInfo")
    return f"{reason}（{more}）" if more else str(reason)

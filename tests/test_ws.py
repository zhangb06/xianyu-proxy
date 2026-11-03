from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from app.protocol.mtop import CookieJar
from app.protocol.ws import XianyuWS, reject_reason


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.on_send = None

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        self.sent.append(frame)
        if self.on_send is not None:
            await self.on_send(frame)

    async def close(self, *args: Any, **kwargs: Any) -> None:
        self.incoming.put_nowait(None)

    async def _frames(self) -> AsyncIterator[str]:
        while True:
            raw = await self.incoming.get()
            if raw is None:
                return
            yield raw

    def __aiter__(self) -> AsyncIterator[str]:
        return self._frames()


def _sync_push(text: str, item_id: str = "42") -> str:
    content = base64.b64encode(
        json.dumps({"contentType": 1, "text": {"text": text}}, ensure_ascii=False).encode()
    ).decode()
    message = {
        "1": {
            "1": {"1": "seller1@goofish"},
            "2": "buyer1@goofish",
            "5": 1700000000000,
            "10": {
                "senderUserId": "buyer1",
                "senderNick": "买家",
                "reminderContent": text,
                "reminderUrl": f"https://www.goofish.com/item?itemId={item_id}",
            },
            "6": {"3": {"5": content}},
        }
    }
    payload = base64.b64encode(json.dumps(message, ensure_ascii=False).encode()).decode()
    return json.dumps({"body": {"syncPushPackage": {"data": [{"data": payload}]}}})


async def _connect(monkeypatch) -> tuple[XianyuWS, FakeWS]:
    fake = FakeWS()

    async def fake_connect(*args: Any, **kwargs: Any) -> FakeWS:
        return fake

    async def no_register(self: XianyuWS) -> None:
        return None

    monkeypatch.setattr("app.protocol.ws.websockets.connect", fake_connect)
    monkeypatch.setattr(XianyuWS, "_register", no_register)
    ws = XianyuWS(CookieJar({"unb": "seller1"}), myid="seller1", heartbeat_interval=3600)
    ws.token = "token"
    await ws.connect()
    return ws, fake


async def test_reader_pump_delivers_and_dedups(monkeypatch) -> None:
    ws, fake = await _connect(monkeypatch)
    try:
        frame = _sync_push("在吗")
        fake.incoming.put_nowait(frame)
        fake.incoming.put_nowait(frame)
        fake.incoming.put_nowait(None)

        received = [message async for message in ws.iter_messages()]

        assert len(received) == 1
        assert received[0]["text"] == "在吗"
        assert received[0]["chat_id"] == "buyer1"
        assert received[0]["item_id"] == "42"
    finally:
        await ws.close()


async def test_wait_result_survives_early_response(monkeypatch) -> None:
    ws, fake = await _connect(monkeypatch)

    async def respond(frame: dict[str, Any]) -> None:
        if frame.get("lwp") != "/r/MessageSend/sendByReceiverScope":
            return
        mid = frame["headers"]["mid"]
        response = {"code": 200, "headers": {"mid": mid, "sid": "s1"}, "body": {"ok": True}}
        fake.incoming.put_nowait(json.dumps(response))

    fake.on_send = respond
    try:
        mid = await ws.send_text("buyer1", "buyer1", "你好")
        response = await ws.wait_result(mid, timeout=1.0)

        assert response is not None
        assert response["body"] == {"ok": True}
        assert mid not in ws._pending
    finally:
        await ws.close()


def test_reject_reason() -> None:
    response = {"body": {"reason": "发送过于频繁", "moreInfo": "try later"}}
    assert reject_reason(response) == "发送过于频繁（try later）"
    assert reject_reason({"body": {}}) is None
    assert reject_reason(None) is None

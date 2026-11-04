from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ai.replier import AIReply, AIReplyService, _build_messages, _enforce_floor
from app.core.timeutil import utc_now_ms
from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.ai import AIConfigRepo
from app.db.repo.messages import MessageRepo
from app.db.repo.rules import KeywordRuleRepo
from app.pipeline.engine import ReplyPipeline, SendResult


class StubSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        self.calls.append((chat_id, to_user_id, text))
        return SendResult(ok=True, msg_id="mid-ai")


class StubAI:
    def __init__(self, *, ok: bool = True, text: str = "AI 回复", error: str = "") -> None:
        self.ok = ok
        self.text = text
        self.error = error
        self.calls: list[tuple[int, str, str]] = []

    async def generate(self, account_id: int, chat_id: str, incoming_text: str) -> AIReply:
        self.calls.append((account_id, chat_id, incoming_text))
        if self.ok:
            return AIReply(ok=True, text=self.text, latency_ms=120)
        return AIReply(ok=False, error=self.error, latency_ms=50)


def _parsed(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "msg_id": "m-1",
        "dedup_key": "m-1",
        "chat_id": "buyer1",
        "sender_id": "buyer1",
        "sender_name": "买家",
        "text": "这个能便宜点吗",
        "kind": "text",
        "item_id": "42",
        "timestamp_ms": utc_now_ms(),
        "is_self": False,
    }
    base.update(overrides)
    return base


async def _setup(tmp_path: Path, ai: Any = None) -> tuple[Database, Any, ReplyPipeline]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    account = await AccountRepo(db, "secret").upsert("10001")
    return db, account, ReplyPipeline(db, ai)


async def test_ai_config_roundtrip_encrypts_key(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        await AccountRepo(db, "secret").upsert("10001")
        repo = AIConfigRepo(db, "secret")
        config = await repo.put(
            1,
            enabled=True,
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            persona="二手数码小店",
            product_info="iPhone 13 128G，九成新",
            price_floor_cents=120000,
            max_history=8,
            timeout_seconds=15,
            api_key="sk-test",
        )
        assert config.enabled is True
        assert config.has_api_key is True
        assert await repo.get_api_key(1) == "sk-test"

        row = await db.fetch_one("SELECT api_key_enc FROM ai_config WHERE account_id = 1")
        assert row is not None and "sk-test" not in row["api_key_enc"]

        again = await repo.put(
            1,
            enabled=True,
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            persona="",
            product_info="",
            price_floor_cents=0,
            max_history=8,
            timeout_seconds=15,
            api_key=None,
        )
        assert again.has_api_key is True
        assert await repo.get_api_key(1) == "sk-test"

        cleared = await repo.put(
            1,
            enabled=False,
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            persona="",
            product_info="",
            price_floor_cents=0,
            max_history=8,
            timeout_seconds=15,
            api_key="",
        )
        assert cleared.has_api_key is False
        assert await repo.get_api_key(1) == ""
    finally:
        await db.close()


def test_floor_rejects_low_price() -> None:
    class Config:
        price_floor_cents = 120000

    assert _enforce_floor(Config(), "最低 1000 元可以") == "below_floor"
    assert _enforce_floor(Config(), "给您 1200 元包邮") is None
    assert _enforce_floor(Config(), "好的，稍等") is None


def test_build_messages_maps_roles_and_appends_incoming() -> None:
    class Config:
        price_floor_cents = 0
        persona = "小店"
        product_info = "iPhone"

    class Msg:
        def __init__(self, direction: str, text: str, kind: str = "text") -> None:
            self.direction = direction
            self.text = text
            self.kind = kind

    history = [Msg("in", "在吗"), Msg("out", "在的"), Msg("in", "[图片]", kind="image")]
    messages = _build_messages(Config(), history, "多少钱")

    assert messages[0]["role"] == "system"
    assert "小店" in messages[0]["content"]
    assert [m["role"] for m in messages[1:]] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "多少钱"


async def test_pipeline_prefers_ai(tmp_path: Path) -> None:
    ai = StubAI(text="最低 1300 元")
    db, account, pipeline = await _setup(tmp_path, ai)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="便宜", reply="关键词回复")
        sender = StubSender()

        outcome = await pipeline.handle_inbound(account, _parsed(), sender)

        assert outcome == "replied"
        assert sender.calls == [("buyer1", "buyer1", "最低 1300 元")]
        assert ai.calls == [(account.id, "buyer1", "这个能便宜点吗")]
        stored = await MessageRepo(db).list_by_chat(account.id, "buyer1")
        assert any(m.direction == "out" and m.text == "最低 1300 元" for m in stored)
        events = await db.fetch_all("SELECT type FROM event_log WHERE type = 'ai.reply'")
        assert events
    finally:
        await db.close()


@pytest.mark.parametrize("error", ["timeout", "http", "no_key", "below_floor"])
async def test_pipeline_falls_back_to_keyword_when_ai_fails(tmp_path: Path, error: str) -> None:
    db, account, pipeline = await _setup(tmp_path, StubAI(ok=False, error=error))
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="便宜", reply="关键词回复")
        sender = StubSender()

        outcome = await pipeline.handle_inbound(account, _parsed(), sender)

        assert outcome == "replied"
        assert sender.calls == [("buyer1", "buyer1", "关键词回复")]
        events = await db.fetch_all("SELECT type FROM event_log WHERE type = 'ai.fallback'")
        assert events
    finally:
        await db.close()


async def test_ai_service_disabled_returns_reason(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        service = AIReplyService(db, "secret")
        result = await service.generate(1, "buyer1", "在吗")
        assert result.ok is False
        assert result.error == "disabled"
    finally:
        await db.close()

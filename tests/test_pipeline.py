from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.timeutil import utc_now_ms
from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.chats import ChatRepo
from app.db.repo.messages import MessageRepo
from app.db.repo.rules import BlockRepo, KeywordRuleRepo, ReplyConfigRepo
from app.pipeline.engine import ReplyPipeline, SendResult


class StubSender:
    def __init__(self, *, ok: bool = True, reason: str = "") -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.ok = ok
        self.reason = reason

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        self.calls.append((chat_id, to_user_id, text))
        return SendResult(ok=self.ok, msg_id="mid-1", reason=self.reason)


def _parsed(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "msg_id": "m-1",
        "dedup_key": "m-1",
        "chat_id": "buyer1",
        "sender_id": "buyer1",
        "sender_name": "买家",
        "text": "在吗",
        "kind": "text",
        "item_id": "42",
        "timestamp_ms": utc_now_ms(),
        "is_self": False,
    }
    base.update(overrides)
    return base


async def _setup(tmp_path: Path) -> tuple[Database, Any, ReplyPipeline]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001")
    return db, account, ReplyPipeline(db)


async def test_keyword_rule_replies_and_persists(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        rules = KeywordRuleRepo(db)
        await rules.create(account.id, keyword="在吗", reply="在的，亲", priority=10)
        sender = StubSender()

        outcome = await pipeline.handle_inbound(account, _parsed(), sender)

        assert outcome == "replied"
        assert sender.calls == [("buyer1", "buyer1", "在的，亲")]

        messages = MessageRepo(db)
        stored = await messages.list_by_chat(account.id, "buyer1")
        assert len(stored) == 2
        outbound = next(m for m in stored if m.direction == "out")
        assert outbound.status == "sent"
        assert outbound.msg_id == "mid-1"
        assert outbound.text == "在的，亲"

        chats = ChatRepo(db)
        chat = await chats.get(account.id, "buyer1")
        assert chat is not None and chat.last_reply_at
    finally:
        await db.close()


async def test_default_reply_when_no_keyword_match(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await ReplyConfigRepo(db).put(
            account.id, default_reply="您好，请稍等", debounce_seconds=0, reply_expiry_seconds=0
        )
        sender = StubSender()

        outcome = await pipeline.handle_inbound(account, _parsed(text="任意内容"), sender)

        assert outcome == "replied"
        assert sender.calls[0][2] == "您好，请稍等"
    finally:
        await db.close()


async def test_no_rule_configured(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        sender = StubSender()
        outcome = await pipeline.handle_inbound(account, _parsed(), sender)
        assert outcome == "no_rule"
        assert sender.calls == []
    finally:
        await db.close()


async def test_duplicate_message_not_replied_twice(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        sender = StubSender()

        assert await pipeline.handle_inbound(account, _parsed(), sender) == "replied"
        assert await pipeline.handle_inbound(account, _parsed(), sender) == "duplicate"
        assert len(sender.calls) == 1
    finally:
        await db.close()


async def test_self_echo_skips_reply(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        sender = StubSender()
        outcome = await pipeline.handle_inbound(account, _parsed(is_self=True), sender)
        assert outcome == "self_echo"
        assert sender.calls == []
    finally:
        await db.close()


async def test_blocklist_user_and_keyword(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        blocks = BlockRepo(db)
        sender = StubSender()

        await blocks.add(account.id, "user", "buyer1")
        assert await pipeline.handle_inbound(account, _parsed(), sender) == "blocked"
        await blocks.remove((await blocks.list_by_account(account.id))[0].id)

        await blocks.add(account.id, "keyword", "差评")
        flagged = _parsed(text="给差评", msg_id="m-2", dedup_key="m-2")
        assert await pipeline.handle_inbound(account, flagged, sender) == "blocked"
        assert sender.calls == []
    finally:
        await db.close()


async def test_blocked_item_not_replied(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        await BlockRepo(db).add(account.id, "item", "42")
        sender = StubSender()
        outcome = await pipeline.handle_inbound(account, _parsed(item_id="42"), sender)
        assert outcome == "blocked"
    finally:
        await db.close()


async def test_paused_chat_not_replied(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        sender = StubSender()
        outcome = await pipeline.handle_inbound(account, _parsed(), sender)
        assert outcome == "replied"

        chats = ChatRepo(db)
        chat = await chats.get(account.id, "buyer1")
        assert chat is not None
        await chats.pause(chat.id, "2999-01-01T00:00:00Z")

        second = _parsed(msg_id="m-2", dedup_key="m-2")
        assert await pipeline.handle_inbound(account, second, sender) == "chat_paused"
        assert len(sender.calls) == 1
    finally:
        await db.close()


async def test_debounce_suppresses_frequent_replies(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        await ReplyConfigRepo(db).put(
            account.id, default_reply="", debounce_seconds=60, reply_expiry_seconds=0
        )
        sender = StubSender()

        assert await pipeline.handle_inbound(account, _parsed(), sender) == "replied"
        second = _parsed(msg_id="m-2", dedup_key="m-2", text="在吗在吗")
        assert await pipeline.handle_inbound(account, second, sender) == "debounced"
        assert len(sender.calls) == 1
    finally:
        await db.close()


async def test_expired_message_skipped(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        await ReplyConfigRepo(db).put(
            account.id, default_reply="", debounce_seconds=0, reply_expiry_seconds=3600
        )
        sender = StubSender()
        old = _parsed(timestamp_ms=utc_now_ms() - 2 * 3600 * 1000)
        assert await pipeline.handle_inbound(account, old, sender) == "expired"
        assert sender.calls == []
    finally:
        await db.close()


async def test_send_failure_marks_outbound_failed(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")
        sender = StubSender(ok=False, reason="发送过于频繁")

        outcome = await pipeline.handle_inbound(account, _parsed(), sender)

        assert outcome == "send_failed"
        messages = MessageRepo(db)
        outbound = next(m for m in await messages.list_by_chat(account.id, "buyer1") if m.direction == "out")
        assert outbound.status == "failed"
    finally:
        await db.close()


async def test_manual_reply(tmp_path: Path) -> None:
    db, account, pipeline = await _setup(tmp_path)
    try:
        chats = ChatRepo(db)
        chat = await chats.ensure(account.id, "buyer1", peer_id="buyer1")
        sender = StubSender()

        result = await pipeline.send_manual(account, chat, "手动回复", sender)

        assert result.ok is True
        assert sender.calls == [("buyer1", "buyer1", "手动回复")]
        stored = await MessageRepo(db).list_by_chat(account.id, "buyer1")
        assert len(stored) == 1
        assert stored[0].direction == "out"
        assert stored[0].status == "sent"
    finally:
        await db.close()

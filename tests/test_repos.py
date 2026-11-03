from __future__ import annotations

from pathlib import Path

from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.chats import ChatRepo
from app.db.repo.messages import MessageRepo
from app.db.repo.rules import BlockRepo, KeywordRuleRepo, ReplyConfigRepo


async def _open(tmp_path: Path) -> tuple[Database, AccountRepo]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    repo = AccountRepo(db, "secret")
    return db, repo


def _parsed(**overrides) -> dict:
    base = {
        "msg_id": "m-1",
        "dedup_key": "m-1",
        "chat_id": "buyer1",
        "sender_id": "buyer1",
        "sender_name": "买家",
        "text": "在吗",
        "kind": "text",
        "item_id": "42",
        "timestamp_ms": 1700000000000,
        "is_self": False,
    }
    base.update(overrides)
    return base


async def test_chat_ensure_and_pause(tmp_path: Path) -> None:
    db, accounts = await _open(tmp_path)
    try:
        account = await accounts.upsert("10001")
        chats = ChatRepo(db)
        chat = await chats.ensure(
            account.id, "buyer1", peer_id="buyer1", peer_name="买家", item_id="42", timestamp_ms=1700000000000
        )
        assert chat.peer_name == "买家"
        assert chat.item_id == "42"

        same = await chats.ensure(account.id, "buyer1", peer_name="新昵称", item_id="43")
        assert same.id == chat.id
        assert same.peer_name == "新昵称"

        await chats.pause(chat.id, "2999-01-01T00:00:00Z")
        paused = await chats.get(account.id, "buyer1")
        assert paused is not None and paused.reply_paused is True

        await chats.unpause(chat.id)
        resumed = await chats.get(1, "buyer1")
        assert resumed is not None and resumed.reply_paused is False
    finally:
        await db.close()


async def test_message_dedup_and_status(tmp_path: Path) -> None:
    db, accounts = await _open(tmp_path)
    try:
        account = await accounts.upsert("10001")
        messages = MessageRepo(db)
        assert await messages.insert_inbound(account.id, _parsed()) is True
        assert await messages.insert_inbound(account.id, _parsed()) is False

        fallback = _parsed(msg_id=None, dedup_key="fallback-1", text="另一条")
        assert await messages.insert_inbound(account.id, fallback) is True
        assert await messages.insert_inbound(account.id, fallback) is False

        outbound_id = await messages.insert_outbound(
            account.id, chat_id="buyer1", dedup_key="out-1", kind="text", text="你好"
        )
        await messages.set_status(outbound_id, "sent", msg_id="server-mid")
        stored = await messages.list_by_chat(account.id, "buyer1")
        assert {m.direction for m in stored} == {"in", "out"}
        outbound = next(m for m in stored if m.direction == "out")
        assert outbound.status == "sent"
        assert outbound.msg_id == "server-mid"
    finally:
        await db.close()


async def test_keyword_rule_ordering_and_toggle(tmp_path: Path) -> None:
    db, accounts = await _open(tmp_path)
    try:
        account = await accounts.upsert("10001")
        rules = KeywordRuleRepo(db)
        low = await rules.create(account.id, keyword="在吗", reply="在的", priority=50)
        high = await rules.create(account.id, keyword="包邮", reply="不包邮", priority=10)

        active = await rules.list_active(account.id)
        assert [r.id for r in active] == [high.id, low.id]

        updated = await rules.update(low.id, enabled=False)
        assert updated is not None and updated.enabled is False
        assert [r.id for r in await rules.list_active(account.id)] == [high.id]

        await rules.delete(999)
        assert len(await rules.list_by_account(account.id)) == 2

        await rules.delete(high.id)
        assert await rules.list_active(account.id) == []
    finally:
        await db.close()


async def test_reply_config_upsert(tmp_path: Path) -> None:
    db, accounts = await _open(tmp_path)
    try:
        account = await accounts.upsert("10001")
        repo = ReplyConfigRepo(db)
        config = await repo.get(account.id)
        assert config.default_reply == ""

        saved = await repo.put(
            account.id, default_reply="稍等", debounce_seconds=30, reply_expiry_seconds=600
        )
        assert saved.default_reply == "稍等"
        assert saved.debounce_seconds == 30

        again = await repo.put(account.id, default_reply="在的", debounce_seconds=0, reply_expiry_seconds=0)
        assert again.default_reply == "在的"
        assert await repo.get(account.id) == again
    finally:
        await db.close()


async def test_block_entries(tmp_path: Path) -> None:
    db, accounts = await _open(tmp_path)
    try:
        account = await accounts.upsert("10001")
        blocks = BlockRepo(db)
        entry = await blocks.add(account.id, "user", "buyer1")
        assert await blocks.is_blocked(account.id, "user", "buyer1") is True
        assert await blocks.is_blocked(account.id, "user", "buyer2") is False

        duplicate = await blocks.add(account.id, "user", "buyer1")
        assert duplicate.id == entry.id
        assert len(await blocks.list_by_account(account.id)) == 1

        await blocks.add(account.id, "keyword", "差评")
        assert len(await blocks.list_by_account(account.id)) == 2

        await blocks.remove(entry.id)
        assert await blocks.is_blocked(account.id, "user", "buyer1") is False
    finally:
        await db.close()

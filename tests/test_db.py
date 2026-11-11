from __future__ import annotations

from pathlib import Path

import pytest

from app.core.secrets import resolve_secret
from app.db.engine import Database
from app.db.repo.accounts import AccountRepo


async def _open(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.connect()
    return db


async def test_migration_creates_p0_tables(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    try:
        rows = await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {row["name"] for row in rows}
        assert {
            "setting",
            "account",
            "account_credential",
            "event_log",
            "chat",
            "message",
            "keyword_rule",
            "reply_config",
            "block_entry",
            "ai_config",
            "ai_usage",
            "card",
            "card_secret",
            "card_item",
            "orders",
            "delivery_rule",
            "outbox",
            "item",
            "item_delivery",
            "account_ops",
        } <= names
        version = await db.fetch_one("PRAGMA user_version")
        assert int(version[0]) == 9
    finally:
        await db.close()


async def test_account_cookie_roundtrip(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    try:
        secret = await resolve_secret(db, "")
        assert await resolve_secret(db, "") == secret

        repo = AccountRepo(db, secret)
        account = await repo.upsert("10001")
        again = await repo.upsert("10001")
        assert again.id == account.id
        assert again.device_id == account.device_id

        await repo.save_cookie(account.id, {"unb": "10001", "cookie2": "abc"})
        assert await repo.load_cookie(account.id) == {"unb": "10001", "cookie2": "abc"}

        await repo.set_status(account.id, "online")
        loaded = await repo.get(account.id)
        assert loaded is not None
        assert loaded.status == "online"
    finally:
        await db.close()


async def test_transaction_rollback(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO setting (key, value) VALUES ('x', '1')")
                raise RuntimeError("boom")
        assert await db.fetch_one("SELECT value FROM setting WHERE key = 'x'") is None
    finally:
        await db.close()

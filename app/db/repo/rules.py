from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.timeutil import utc_now_iso
from app.db.engine import Database

BLOCK_KINDS = ("user", "item", "keyword")


@dataclass(slots=True)
class KeywordRule:
    id: int
    account_id: int
    keyword: str
    reply: str
    priority: int
    enabled: bool

    @classmethod
    def from_row(cls, row: Any) -> KeywordRule:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            keyword=str(row["keyword"]),
            reply=str(row["reply"]),
            priority=int(row["priority"]),
            enabled=bool(row["enabled"]),
        )


@dataclass(slots=True)
class ReplyConfig:
    account_id: int
    default_reply: str
    debounce_seconds: int
    reply_expiry_seconds: int

    @classmethod
    def from_row(cls, row: Any) -> ReplyConfig:
        return cls(
            account_id=int(row["account_id"]),
            default_reply=str(row["default_reply"]),
            debounce_seconds=int(row["debounce_seconds"]),
            reply_expiry_seconds=int(row["reply_expiry_seconds"]),
        )


class KeywordRuleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self, account_id: int, *, keyword: str, reply: str, priority: int = 100, enabled: bool = True
    ) -> KeywordRule:
        now = utc_now_iso()
        rule_id = await self.db.write_returning_id(
            "INSERT INTO keyword_rule (account_id, keyword, reply, priority, enabled, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, keyword, reply, priority, int(enabled), now, now),
        )
        row = await self.db.fetch_one("SELECT * FROM keyword_rule WHERE id = ?", (rule_id,))
        assert row is not None
        return KeywordRule.from_row(row)

    async def update(
        self,
        rule_id: int,
        *,
        keyword: str | None = None,
        reply: str | None = None,
        priority: int | None = None,
        enabled: bool | None = None,
    ) -> KeywordRule | None:
        row = await self.db.fetch_one("SELECT * FROM keyword_rule WHERE id = ?", (rule_id,))
        if row is None:
            return None
        values = {
            "keyword": row["keyword"] if keyword is None else keyword,
            "reply": row["reply"] if reply is None else reply,
            "priority": row["priority"] if priority is None else priority,
            "enabled": row["enabled"] if enabled is None else int(enabled),
        }
        await self.db.write(
            "UPDATE keyword_rule SET keyword = ?, reply = ?, priority = ?, enabled = ?, updated_at = ? "
            "WHERE id = ?",
            (
                values["keyword"],
                values["reply"],
                values["priority"],
                values["enabled"],
                utc_now_iso(),
                rule_id,
            ),
        )
        row = await self.db.fetch_one("SELECT * FROM keyword_rule WHERE id = ?", (rule_id,))
        assert row is not None
        return KeywordRule.from_row(row)

    async def delete(self, rule_id: int) -> bool:
        await self.db.write("DELETE FROM keyword_rule WHERE id = ?", (rule_id,))
        return True

    async def list_by_account(self, account_id: int) -> list[KeywordRule]:
        rows = await self.db.fetch_all(
            "SELECT * FROM keyword_rule WHERE account_id = ? ORDER BY priority, id", (account_id,)
        )
        return [KeywordRule.from_row(row) for row in rows]

    async def list_active(self, account_id: int) -> list[KeywordRule]:
        rows = await self.db.fetch_all(
            "SELECT * FROM keyword_rule WHERE account_id = ? AND enabled = 1 ORDER BY priority, id",
            (account_id,),
        )
        return [KeywordRule.from_row(row) for row in rows]


class ReplyConfigRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, account_id: int) -> ReplyConfig:
        row = await self.db.fetch_one("SELECT * FROM reply_config WHERE account_id = ?", (account_id,))
        if row is None:
            return ReplyConfig(
                account_id=account_id,
                default_reply="",
                debounce_seconds=0,
                reply_expiry_seconds=0,
            )
        return ReplyConfig.from_row(row)

    async def put(
        self,
        account_id: int,
        *,
        default_reply: str,
        debounce_seconds: int,
        reply_expiry_seconds: int,
    ) -> ReplyConfig:
        await self.db.write(
            "INSERT INTO reply_config (account_id, default_reply, debounce_seconds, "
            "reply_expiry_seconds, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id) DO UPDATE SET default_reply = excluded.default_reply, "
            "debounce_seconds = excluded.debounce_seconds, "
            "reply_expiry_seconds = excluded.reply_expiry_seconds, "
            "updated_at = excluded.updated_at",
            (account_id, default_reply, debounce_seconds, reply_expiry_seconds, utc_now_iso()),
        )
        return await self.get(account_id)


@dataclass(slots=True)
class BlockEntry:
    id: int
    account_id: int
    kind: str
    value: str

    @classmethod
    def from_row(cls, row: Any) -> BlockEntry:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            kind=str(row["kind"]),
            value=str(row["value"]),
        )


class BlockRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, account_id: int, kind: str, value: str) -> BlockEntry:
        entry_id = await self.db.write_returning_id(
            "INSERT OR IGNORE INTO block_entry (account_id, kind, value) VALUES (?, ?, ?)",
            (account_id, kind, value),
        )
        row = await self.db.fetch_one("SELECT * FROM block_entry WHERE id = ?", (entry_id,))
        assert row is not None
        return BlockEntry.from_row(row)

    async def remove(self, entry_id: int) -> None:
        await self.db.write("DELETE FROM block_entry WHERE id = ?", (entry_id,))

    async def list_by_account(self, account_id: int) -> list[BlockEntry]:
        rows = await self.db.fetch_all(
            "SELECT * FROM block_entry WHERE account_id = ? ORDER BY kind, id", (account_id,)
        )
        return [BlockEntry.from_row(row) for row in rows]

    async def is_blocked(self, account_id: int, kind: str, value: str) -> bool:
        row = await self.db.fetch_one(
            "SELECT id FROM block_entry WHERE account_id = ? AND kind = ? AND value = ?",
            (account_id, kind, value),
        )
        return row is not None

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.timeutil import utc_now_iso
from app.db.engine import Database

_BLOCK_KINDS = ("text", "image")


@dataclass(slots=True)
class Chat:
    id: int
    account_id: int
    chat_id: str
    peer_id: str
    peer_name: str
    item_id: str
    paused_until: str
    last_message_at: str
    last_reply_at: str

    @classmethod
    def from_row(cls, row: Any) -> Chat:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            chat_id=str(row["chat_id"]),
            peer_id=str(row["peer_id"]),
            peer_name=str(row["peer_name"]),
            item_id=str(row["item_id"]),
            paused_until=str(row["paused_until"] or ""),
            last_message_at=str(row["last_message_at"]),
            last_reply_at=str(row["last_reply_at"] or ""),
        )

    @property
    def reply_paused(self) -> bool:
        return bool(self.paused_until) and self.paused_until > utc_now_iso()


class ChatRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure(
        self,
        account_id: int,
        chat_id: str,
        *,
        peer_id: str = "",
        peer_name: str = "",
        item_id: str = "",
        timestamp_ms: int = 0,
    ) -> Chat:
        row = await self.db.fetch_one(
            "SELECT * FROM chat WHERE account_id = ? AND chat_id = ?", (account_id, chat_id)
        )
        now = utc_now_iso()
        if row is not None:
            updates: list[str] = []
            params: list[Any] = []
            if peer_name and peer_name != row["peer_name"]:
                updates.append("peer_name = ?")
                params.append(peer_name)
            if item_id and item_id != row["item_id"]:
                updates.append("item_id = ?")
                params.append(item_id)
            if peer_id and peer_id != row["peer_id"]:
                updates.append("peer_id = ?")
                params.append(peer_id)
            if updates:
                updates.append("updated_at = ?")
                params.extend([now, row["id"]])
                await self.db.write(f"UPDATE chat SET {', '.join(updates)} WHERE id = ?", tuple(params))
                row = await self.db.fetch_one("SELECT * FROM chat WHERE id = ?", (row["id"],))
            return Chat.from_row(row)

        if timestamp_ms:
            last = _ms_to_iso(timestamp_ms)
        else:
            last = now
        await self.db.write(
            "INSERT OR IGNORE INTO chat (account_id, chat_id, peer_id, peer_name, item_id, last_message_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, chat_id, peer_id, peer_name, item_id, last),
        )
        row = await self.db.fetch_one(
            "SELECT * FROM chat WHERE account_id = ? AND chat_id = ?", (account_id, chat_id)
        )
        assert row is not None
        return Chat.from_row(row)

    async def touch_message(self, chat_id_row_id: int, timestamp_ms: int) -> None:
        await self.db.write(
            "UPDATE chat SET last_message_at = ?, updated_at = ? WHERE id = ?",
            (_ms_to_iso(timestamp_ms), utc_now_iso(), chat_id_row_id),
        )

    async def get(self, account_id: int, chat_id: str) -> Chat | None:
        row = await self.db.fetch_one(
            "SELECT * FROM chat WHERE account_id = ? AND chat_id = ?", (account_id, chat_id)
        )
        return Chat.from_row(row) if row else None

    async def get_by_id(self, chat_row_id: int) -> Chat | None:
        row = await self.db.fetch_one("SELECT * FROM chat WHERE id = ?", (chat_row_id,))
        return Chat.from_row(row) if row else None

    async def list_by_account(self, account_id: int, *, limit: int = 200) -> list[Chat]:
        rows = await self.db.fetch_all(
            "SELECT * FROM chat WHERE account_id = ? ORDER BY last_message_at DESC LIMIT ?",
            (account_id, limit),
        )
        return [Chat.from_row(row) for row in rows]

    async def pause(self, chat_row_id: int, until_iso: str) -> None:
        await self.db.write(
            "UPDATE chat SET paused_until = ?, updated_at = ? WHERE id = ?",
            (until_iso, utc_now_iso(), chat_row_id),
        )

    async def unpause(self, chat_row_id: int) -> None:
        await self.db.write(
            "UPDATE chat SET paused_until = NULL, updated_at = ? WHERE id = ?",
            (utc_now_iso(), chat_row_id),
        )

    async def mark_replied(self, chat_row_id: int, timestamp_ms: int) -> None:
        await self.db.write(
            "UPDATE chat SET last_reply_at = ?, updated_at = ? WHERE id = ?",
            (_ms_to_iso(timestamp_ms), utc_now_iso(), chat_row_id),
        )


def _ms_to_iso(timestamp_ms: int) -> str:
    from datetime import UTC, datetime

    stamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")

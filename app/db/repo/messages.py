from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.core.timeutil import utc_now_iso
from app.db.engine import Database


@dataclass(slots=True)
class StoredMessage:
    id: int
    account_id: int
    chat_id: str
    msg_id: str
    dedup_key: str
    direction: str
    kind: str
    sender_id: str
    text: str
    item_id: str
    timestamp_ms: int
    status: str

    @classmethod
    def from_row(cls, row: Any) -> StoredMessage:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            chat_id=str(row["chat_id"]),
            msg_id=str(row["msg_id"] or ""),
            dedup_key=str(row["dedup_key"]),
            direction=str(row["direction"]),
            kind=str(row["kind"]),
            sender_id=str(row["sender_id"]),
            text=str(row["text"]),
            item_id=str(row["item_id"]),
            timestamp_ms=int(row["timestamp_ms"]),
            status=str(row["status"]),
        )


class MessageRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def insert_inbound(self, account_id: int, parsed: dict[str, Any]) -> bool:
        """按 dedup_key 幂等落库，重复消息返回 False。"""
        row = await self.db.fetch_one(
            "SELECT id FROM message WHERE account_id = ? AND dedup_key = ?",
            (account_id, parsed["dedup_key"]),
        )
        if row is not None:
            return False
        try:
            await self.db.write(
                "INSERT INTO message (account_id, chat_id, msg_id, dedup_key, direction, kind, "
                "sender_id, text, item_id, timestamp_ms, raw_json, status) "
                "VALUES (?, ?, ?, ?, 'in', ?, ?, ?, ?, ?, ?, 'received')",
                (
                    account_id,
                    parsed["chat_id"],
                    parsed["msg_id"],
                    parsed["dedup_key"],
                    parsed["kind"],
                    parsed["sender_id"],
                    parsed["text"],
                    parsed["item_id"],
                    int(parsed["timestamp_ms"]),
                    _raw_json(parsed.get("raw")),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    async def insert_outbound(
        self,
        account_id: int,
        *,
        chat_id: str,
        dedup_key: str,
        kind: str,
        text: str,
        item_id: str = "",
        msg_id: str = "",
        status: str = "pending",
    ) -> int:
        # msg_id 为空时存 NULL：UNIQUE(account_id, msg_id) 不把多个 NULL 视为重复，
        # 否则同一账号的第二条待发送消息会撞唯一约束。
        stored_msg_id = msg_id or None
        values = (
            account_id,
            chat_id,
            stored_msg_id,
            dedup_key,
            kind,
            text,
            item_id,
            _now_ms(),
            status,
        )
        try:
            return await self.db.write_returning_id(
                "INSERT INTO message (account_id, chat_id, msg_id, dedup_key, direction, kind, "
                "sender_id, text, item_id, timestamp_ms, status) "
                "VALUES (?, ?, ?, ?, 'out', ?, '', ?, ?, ?, ?)",
                values,
            )
        except sqlite3.IntegrityError:
            # 同 dedup_key 重试：复用原消息行，避免重复发送记录
            row = await self.db.fetch_one(
                "SELECT id FROM message WHERE account_id = ? AND dedup_key = ?",
                (account_id, dedup_key),
            )
            if row is None:
                raise
            await self.db.write(
                "UPDATE message SET text = ?, status = ?, msg_id = ? WHERE id = ?",
                (text, status, stored_msg_id, int(row["id"])),
            )
            return int(row["id"])

    async def set_status(self, message_id: int, status: str, msg_id: str = "") -> None:
        if msg_id:
            await self.db.write(
                "UPDATE message SET status = ?, msg_id = ? WHERE id = ?", (status, msg_id, message_id)
            )
        else:
            await self.db.write("UPDATE message SET status = ? WHERE id = ?", (status, message_id))

    async def list_by_chat(
        self, account_id: int, chat_id: str, *, limit: int = 100, before_id: int = 0
    ) -> list[StoredMessage]:
        if before_id:
            rows = await self.db.fetch_all(
                "SELECT * FROM message WHERE account_id = ? AND chat_id = ? AND id < ? "
                "ORDER BY id DESC LIMIT ?",
                (account_id, chat_id, before_id, limit),
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT * FROM message WHERE account_id = ? AND chat_id = ? ORDER BY id DESC LIMIT ?",
                (account_id, chat_id, limit),
            )
        return [StoredMessage.from_row(row) for row in rows]

    async def count_today(self, account_id: int | None = None) -> dict[str, int]:
        today = utc_now_iso()[:10]
        if account_id is None:
            row = await self.db.fetch_one(
                "SELECT "
                "SUM(CASE WHEN direction = 'in' THEN 1 ELSE 0 END) AS inbound, "
                "SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END) AS outbound "
                "FROM message WHERE created_at >= ?",
                (today,),
            )
        else:
            row = await self.db.fetch_one(
                "SELECT "
                "SUM(CASE WHEN direction = 'in' THEN 1 ELSE 0 END) AS inbound, "
                "SUM(CASE WHEN direction = 'out' THEN 1 ELSE 0 END) AS outbound "
                "FROM message WHERE account_id = ? AND created_at >= ?",
                (account_id, today),
            )
        if row is None:
            return {"inbound": 0, "outbound": 0}
        return {"inbound": int(row["inbound"] or 0), "outbound": int(row["outbound"] or 0)}


def _now_ms() -> int:
    from datetime import UTC, datetime

    return int(datetime.now(UTC).timestamp() * 1000)


def _raw_json(raw: Any) -> str:
    if not raw:
        return ""
    try:
        return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))[:8000]
    except (TypeError, ValueError):
        return ""

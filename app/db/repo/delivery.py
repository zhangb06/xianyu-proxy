from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.timeutil import utc_now_iso
from app.db.engine import Database

ORDER_STATUSES = ("paid", "delivering", "delivered", "failed", "manual")
SECRET_STATUSES = ("available", "claimed", "used", "manual")


def iso_after(seconds: float) -> str:
    moment = datetime.fromtimestamp(datetime.now(UTC).timestamp() + seconds, UTC)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return secret[:2] + "***"
    return f"{secret[:4]}***{secret[-4:]}"


@dataclass(slots=True)
class Card:
    id: int
    account_id: int
    name: str
    note: str
    enabled: bool
    available: int = 0
    used: int = 0
    total: int = 0

    @classmethod
    def from_row(cls, row: Any) -> Card:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            name=str(row["name"]),
            note=str(row["note"]),
            enabled=bool(row["enabled"]),
            available=int(row["available"] or 0),
            used=int(row["used"] or 0),
            total=int(row["total"] or 0),
        )


@dataclass(slots=True)
class CardSecret:
    id: int
    card_id: int
    secret: str
    status: str
    order_no: str
    claimed_at: str
    used_at: str

    @classmethod
    def from_row(cls, row: Any) -> CardSecret:
        return cls(
            id=int(row["id"]),
            card_id=int(row["card_id"]),
            secret=str(row["secret"]),
            status=str(row["status"]),
            order_no=str(row["order_no"]),
            claimed_at=str(row["claimed_at"] or ""),
            used_at=str(row["used_at"] or ""),
        )


@dataclass(slots=True)
class Order:
    id: int
    account_id: int
    order_no: str
    chat_id: str
    buyer_id: str
    item_id: str
    title: str
    amount_cents: int
    status: str
    paid_at: str
    delivered_at: str
    card_secret_id: int | None
    detail: str
    created_at: str
    flower_status: str = ""
    flower_at: str = ""
    flower_error: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Order:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            order_no=str(row["order_no"]),
            chat_id=str(row["chat_id"]),
            buyer_id=str(row["buyer_id"]),
            item_id=str(row["item_id"]),
            title=str(row["title"]),
            amount_cents=int(row["amount_cents"]),
            status=str(row["status"]),
            paid_at=str(row["paid_at"] or ""),
            delivered_at=str(row["delivered_at"] or ""),
            card_secret_id=row["card_secret_id"],
            detail=str(row["detail"]),
            created_at=str(row["created_at"]),
            flower_status=str(row["flower_status"] or ""),
            flower_at=str(row["flower_at"] or ""),
            flower_error=str(row["flower_error"] or ""),
        )


@dataclass(slots=True)
class DeliveryRule:
    id: int
    account_id: int
    code: str
    name: str
    item_id: str
    template: str
    priority: int
    enabled: bool

    @classmethod
    def from_row(cls, row: Any) -> DeliveryRule:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            code=str(row["code"]),
            name=str(row["name"]),
            item_id=str(row["item_id"]),
            template=str(row["template"]),
            priority=int(row["priority"]),
            enabled=bool(row["enabled"]),
        )


@dataclass(slots=True)
class OutboxTask:
    id: int
    account_id: int
    dedup_key: str
    task: str
    payload: dict[str, Any]
    attempts: int

    @classmethod
    def from_row(cls, row: Any) -> OutboxTask:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            dedup_key=str(row["dedup_key"]),
            task=str(row["task"]),
            payload=payload if isinstance(payload, dict) else {},
            attempts=int(row["attempts"]),
        )


_CARD_STOCK_SQL = (
    "SELECT c.*, "
    "(SELECT COUNT(*) FROM card_secret s WHERE s.card_id = c.id AND s.status = 'available') AS available, "
    "(SELECT COUNT(*) FROM card_secret s WHERE s.card_id = c.id AND s.status = 'used') AS used, "
    "(SELECT COUNT(*) FROM card_secret s WHERE s.card_id = c.id) AS total "
    "FROM card c"
)


class CardRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, account_id: int, *, name: str, note: str = "") -> Card:
        now = utc_now_iso()
        card_id = await self.db.write_returning_id(
            "INSERT INTO card (account_id, name, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (account_id, name, note, now, now),
        )
        card = await self.get(card_id)
        assert card is not None
        return card

    async def get(self, card_id: int) -> Card | None:
        row = await self.db.fetch_one(f"{_CARD_STOCK_SQL} WHERE c.id = ?", (card_id,))
        return Card.from_row(row) if row is not None else None

    async def list_by_account(self, account_id: int) -> list[Card]:
        rows = await self.db.fetch_all(
            f"{_CARD_STOCK_SQL} WHERE c.account_id = ? ORDER BY c.id", (account_id,)
        )
        return [Card.from_row(row) for row in rows]

    async def delete(self, card_id: int) -> None:
        await self.db.write("DELETE FROM card WHERE id = ?", (card_id,))

    async def update(
        self,
        card_id: int,
        *,
        name: str | None = None,
        note: str | None = None,
        enabled: bool | None = None,
    ) -> Card | None:
        row = await self.db.fetch_one("SELECT * FROM card WHERE id = ?", (card_id,))
        if row is None:
            return None
        await self.db.write(
            "UPDATE card SET name = ?, note = ?, enabled = ?, updated_at = ? WHERE id = ?",
            (
                row["name"] if name is None else name,
                row["note"] if note is None else note,
                row["enabled"] if enabled is None else int(enabled),
                utc_now_iso(),
                card_id,
            ),
        )
        return await self.get(card_id)

    async def add_secrets(self, card_id: int, secrets: list[str]) -> int:
        inserted = 0
        now = utc_now_iso()
        async with self.db.transaction() as conn:
            for secret in secrets:
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO card_secret (card_id, secret, created_at) "
                    "VALUES (?, ?, ?)",
                    (card_id, secret, now),
                )
                inserted += cursor.rowcount
        return inserted

    async def list_secrets(self, card_id: int, *, limit: int = 200) -> list[CardSecret]:
        rows = await self.db.fetch_all(
            "SELECT * FROM card_secret WHERE card_id = ? ORDER BY id DESC LIMIT ?",
            (card_id, limit),
        )
        return [CardSecret.from_row(row) for row in rows]

    async def bind_item(self, account_id: int, card_id: int, item_id: str) -> None:
        await self.db.write(
            "INSERT OR IGNORE INTO card_item (account_id, card_id, item_id) VALUES (?, ?, ?)",
            (account_id, card_id, item_id),
        )

    async def unbind_item(self, card_id: int, item_id: str) -> None:
        await self.db.write(
            "DELETE FROM card_item WHERE card_id = ? AND item_id = ?", (card_id, item_id)
        )

    async def items_of(self, card_id: int) -> list[str]:
        rows = await self.db.fetch_all(
            "SELECT item_id FROM card_item WHERE card_id = ? ORDER BY item_id", (card_id,)
        )
        return [str(row["item_id"]) for row in rows]

    async def card_for_item(self, account_id: int, item_id: str) -> int | None:
        row = await self.db.fetch_one(
            "SELECT ci.card_id FROM card_item ci JOIN card c ON c.id = ci.card_id "
            "WHERE ci.account_id = ? AND ci.item_id = ? AND c.enabled = 1 "
            "ORDER BY (SELECT COUNT(*) FROM card_secret s WHERE s.card_id = ci.card_id "
            "AND s.status = 'available') DESC, ci.card_id LIMIT 1",
            (account_id, item_id),
        )
        return int(row["card_id"]) if row is not None else None


class CardSecretRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def claim(self, card_id: int, order_no: str) -> CardSecret | None:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM card_secret WHERE card_id = ? AND status = 'available' "
                "ORDER BY id LIMIT 1",
                (card_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE card_secret SET status = 'claimed', order_no = ?, claimed_at = ? "
                "WHERE id = ? AND status = 'available'",
                (order_no, utc_now_iso(), row["id"]),
            )
            row = await (await conn.execute(
                "SELECT * FROM card_secret WHERE id = ?", (row["id"],)
            )).fetchone()
        return CardSecret.from_row(row) if row is not None else None

    async def release(self, secret_id: int) -> None:
        await self.db.write(
            "UPDATE card_secret SET status = 'available', order_no = '', claimed_at = NULL "
            "WHERE id = ? AND status = 'claimed'",
            (secret_id,),
        )

    async def mark_used(self, secret_id: int) -> None:
        await self.db.write(
            "UPDATE card_secret SET status = 'used', used_at = ? WHERE id = ?",
            (utc_now_iso(), secret_id),
        )


class OrderRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_if_absent(
        self,
        account_id: int,
        *,
        order_no: str,
        chat_id: str = "",
        buyer_id: str = "",
        item_id: str = "",
        title: str = "",
        amount_cents: int = 0,
        paid_at: str = "",
    ) -> Order | None:
        """已存在返回 None，避免重复发货。"""
        now = utc_now_iso()
        try:
            order_id = await self.db.write_returning_id(
                "INSERT INTO orders (account_id, order_no, chat_id, buyer_id, item_id, title, "
                "amount_cents, status, paid_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'paid', ?, ?, ?)",
                (
                    account_id,
                    order_no,
                    chat_id,
                    buyer_id,
                    item_id,
                    title,
                    amount_cents,
                    paid_at or now,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return await self.get_by_id(order_id)

    async def get_by_id(self, order_id: int) -> Order | None:
        row = await self.db.fetch_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        return Order.from_row(row) if row is not None else None

    async def get_by_no(self, account_id: int, order_no: str) -> Order | None:
        row = await self.db.fetch_one(
            "SELECT * FROM orders WHERE account_id = ? AND order_no = ?", (account_id, order_no)
        )
        return Order.from_row(row) if row is not None else None

    async def latest_for_chat(
        self, account_id: int, chat_id: str, *, item_id: str = ""
    ) -> Order | None:
        sql = "SELECT * FROM orders WHERE account_id = ? AND chat_id = ?"
        params: list[Any] = [account_id, chat_id]
        if item_id:
            sql += " AND item_id = ?"
            params.append(item_id)
        row = await self.db.fetch_one(sql + " ORDER BY id DESC LIMIT 1", tuple(params))
        return Order.from_row(row) if row is not None else None

    async def list(
        self, account_id: int | None = None, *, status: str = "", limit: int = 200
    ) -> list[Order]:
        conditions: list[str] = []
        params: list[Any] = []
        if account_id is not None:
            conditions.append("account_id = ?")
            params.append(account_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        rows = await self.db.fetch_all(
            f"SELECT * FROM orders {where}ORDER BY id DESC LIMIT ?", (*params, limit)
        )
        return [Order.from_row(row) for row in rows]

    async def set_status(
        self,
        order_id: int,
        status: str,
        *,
        detail: str = "",
        card_secret_id: int | None = None,
        delivered: bool = False,
    ) -> None:
        now = utc_now_iso()
        await self.db.write(
            "UPDATE orders SET status = ?, detail = ?, updated_at = ?, "
            "card_secret_id = COALESCE(?, card_secret_id), "
            "delivered_at = CASE WHEN ? = 1 THEN ? ELSE delivered_at END WHERE id = ?",
            (status, detail, now, card_secret_id, int(delivered), now, order_id),
        )

    async def set_title(self, order_id: int, title: str) -> None:
        if not title:
            return
        await self.db.write(
            "UPDATE orders SET title = ?, updated_at = ? WHERE id = ?",
            (title, utc_now_iso(), order_id),
        )

    async def set_flower(
        self, order_id: int, status: str, *, error: str = "", sent: bool = False
    ) -> None:
        now = utc_now_iso()
        await self.db.write(
            "UPDATE orders SET flower_status = ?, flower_error = ?, updated_at = ?, "
            "flower_at = CASE WHEN ? = 1 THEN ? ELSE flower_at END WHERE id = ?",
            (status, error[:300], now, int(sent), now, order_id),
        )

    async def start_delivery(self, order_id: int) -> bool:
        """仅 paid → delivering 的原子迁移成功方允许发货。"""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE orders SET status = 'delivering', updated_at = ? "
                "WHERE id = ? AND status = 'paid'",
                (utc_now_iso(), order_id),
            )
            return cursor.rowcount == 1


class DeliveryRuleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        account_id: int,
        *,
        code: str,
        name: str,
        template: str,
        item_id: str = "",
        priority: int = 100,
        enabled: bool = True,
    ) -> DeliveryRule:
        now = utc_now_iso()
        rule_id = await self.db.write_returning_id(
            "INSERT INTO delivery_rule (account_id, code, name, item_id, template, priority, "
            "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, code, name, item_id, template, priority, int(enabled), now, now),
        )
        row = await self.db.fetch_one("SELECT * FROM delivery_rule WHERE id = ?", (rule_id,))
        assert row is not None
        return DeliveryRule.from_row(row)

    async def update(
        self,
        rule_id: int,
        *,
        name: str | None = None,
        item_id: str | None = None,
        template: str | None = None,
        priority: int | None = None,
        enabled: bool | None = None,
    ) -> DeliveryRule | None:
        row = await self.db.fetch_one("SELECT * FROM delivery_rule WHERE id = ?", (rule_id,))
        if row is None:
            return None
        await self.db.write(
            "UPDATE delivery_rule SET name = ?, item_id = ?, template = ?, priority = ?, "
            "enabled = ?, updated_at = ? WHERE id = ?",
            (
                row["name"] if name is None else name,
                row["item_id"] if item_id is None else item_id,
                row["template"] if template is None else template,
                row["priority"] if priority is None else priority,
                row["enabled"] if enabled is None else int(enabled),
                utc_now_iso(),
                rule_id,
            ),
        )
        row = await self.db.fetch_one("SELECT * FROM delivery_rule WHERE id = ?", (rule_id,))
        assert row is not None
        return DeliveryRule.from_row(row)

    async def delete(self, rule_id: int) -> None:
        await self.db.write("DELETE FROM delivery_rule WHERE id = ?", (rule_id,))

    async def list_by_account(self, account_id: int) -> list[DeliveryRule]:
        rows = await self.db.fetch_all(
            "SELECT * FROM delivery_rule WHERE account_id = ? ORDER BY priority, id", (account_id,)
        )
        return [DeliveryRule.from_row(row) for row in rows]

    async def template_for(self, account_id: int, item_id: str) -> DeliveryRule | None:
        row = await self.db.fetch_one(
            "SELECT * FROM delivery_rule WHERE account_id = ? AND enabled = 1 "
            "AND (item_id = ? OR item_id = '') ORDER BY (item_id = ?) DESC, priority, id LIMIT 1",
            (account_id, item_id, item_id),
        )
        return DeliveryRule.from_row(row) if row is not None else None


class OutboxRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def enqueue(
        self,
        account_id: int,
        *,
        dedup_key: str,
        task: str,
        payload: dict[str, Any],
        delay_seconds: float = 0,
    ) -> bool:
        now = utc_now_iso()
        try:
            await self.db.write(
                "INSERT INTO outbox (account_id, dedup_key, task, payload, status, run_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
                (
                    account_id,
                    dedup_key,
                    task,
                    json.dumps(payload, ensure_ascii=False),
                    iso_after(delay_seconds),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    async def claim_due(self, account_id: int) -> OutboxTask | None:
        now = utc_now_iso()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT * FROM outbox WHERE account_id = ? AND status = 'pending' "
                "AND run_at <= ? ORDER BY run_at, id LIMIT 1",
                (account_id, now),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE outbox SET status = 'sending', attempts = attempts + 1, updated_at = ? "
                "WHERE id = ?",
                (now, row["id"]),
            )
            row = await (await conn.execute(
                "SELECT * FROM outbox WHERE id = ?", (row["id"],)
            )).fetchone()
        return OutboxTask.from_row(row) if row is not None else None

    async def done(self, task_id: int) -> None:
        await self.db.write(
            "UPDATE outbox SET status = 'sent', updated_at = ? WHERE id = ?",
            (utc_now_iso(), task_id),
        )

    async def retry(self, task_id: int, *, delay_seconds: float, error: str) -> None:
        await self.db.write(
            "UPDATE outbox SET status = 'pending', run_at = ?, last_error = ?, updated_at = ? "
            "WHERE id = ?",
            (iso_after(delay_seconds), error[:300], utc_now_iso(), task_id),
        )

    async def dead(self, task_id: int, error: str) -> None:
        await self.db.write(
            "UPDATE outbox SET status = 'dead', last_error = ?, updated_at = ? WHERE id = ?",
            (error[:300], utc_now_iso(), task_id),
        )

    async def requeue(self, dedup_key: str) -> bool:
        row = await self.db.fetch_one("SELECT id FROM outbox WHERE dedup_key = ?", (dedup_key,))
        if row is None:
            return False
        await self.db.write(
            "UPDATE outbox SET status = 'pending', attempts = 0, run_at = ?, last_error = '', "
            "updated_at = ? WHERE dedup_key = ?",
            (utc_now_iso(), utc_now_iso(), dedup_key),
        )
        return True

    async def reset_sending(self) -> None:
        await self.db.write(
            "UPDATE outbox SET status = 'pending', updated_at = ? WHERE status = 'sending'",
            (utc_now_iso(),),
        )

    async def list_recent(self, account_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM outbox WHERE account_id = ? ORDER BY id DESC LIMIT ?",
            (account_id, limit),
        )
        return [dict(row) for row in rows]

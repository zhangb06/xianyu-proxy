from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.timeutil import utc_now_iso
from app.db.engine import Database
from app.protocol.listing import STATUS_OFFLINE, STATUS_UNKNOWN, ListedItem

DELIVERY_MODES = ("card", "fixed", "manual")


@dataclass(slots=True)
class Item:
    id: int
    account_id: int
    item_id: str
    title: str
    price_cents: int
    status: str
    cover_url: str
    synced_at: str
    polished_at: str = ""
    polish_error: str = ""

    @classmethod
    def from_row(cls, row: Any) -> Item:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            item_id=str(row["item_id"]),
            title=str(row["title"]),
            price_cents=int(row["price_cents"]),
            status=str(row["status"]),
            cover_url=str(row["cover_url"]),
            synced_at=str(row["synced_at"]),
            polished_at=str(row["polished_at"] or ""),
            polish_error=str(row["polish_error"] or ""),
        )


@dataclass(slots=True)
class ItemDelivery:
    id: int
    account_id: int
    item_id: str
    enabled: bool
    mode: str
    card_id: int | None
    template: str
    content: str
    auto_consign: bool
    remark: str

    @classmethod
    def from_row(cls, row: Any) -> ItemDelivery:
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            item_id=str(row["item_id"]),
            enabled=bool(row["enabled"]),
            mode=str(row["mode"]),
            card_id=int(row["card_id"]) if row["card_id"] is not None else None,
            template=str(row["template"]),
            content=str(row["content"]),
            auto_consign=bool(row["auto_consign"]),
            remark=str(row["remark"]),
        )


class ItemRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert_many(
        self, account_id: int, items: list[ListedItem], *, synced_at: str = ""
    ) -> int:
        if not items:
            return 0
        now = synced_at or utc_now_iso()
        async with self.db.transaction() as conn:
            for item in items:
                await conn.execute(
                    "INSERT INTO item (account_id, item_id, title, price_cents, status, "
                    "cover_url, synced_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (account_id, item_id) DO UPDATE SET "
                    "title = excluded.title, price_cents = excluded.price_cents, "
                    "status = excluded.status, cover_url = excluded.cover_url, "
                    "synced_at = excluded.synced_at, updated_at = excluded.updated_at",
                    (
                        account_id,
                        item.item_id,
                        item.title,
                        item.price_cents,
                        item.status,
                        item.cover_url,
                        now,
                        now,
                        now,
                    ),
                )
        return len(items)

    async def ensure(
        self,
        account_id: int,
        item_id: str,
        *,
        title: str = "",
        price_cents: int = 0,
        status: str = STATUS_UNKNOWN,
    ) -> None:
        """订单里出现但列表尚未同步的商品，先落一条占位记录。"""
        if not item_id:
            return
        now = utc_now_iso()
        await self.db.write(
            "INSERT INTO item (account_id, item_id, title, price_cents, status, cover_url, "
            "synced_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?) "
            "ON CONFLICT (account_id, item_id) DO UPDATE SET "
            "title = CASE WHEN item.title = '' THEN excluded.title ELSE item.title END, "
            "price_cents = CASE WHEN item.price_cents = 0 THEN excluded.price_cents "
            "ELSE item.price_cents END, "
            "status = CASE WHEN item.status = ? THEN excluded.status ELSE item.status END, "
            "updated_at = excluded.updated_at",
            (account_id, item_id, title, price_cents, status, now, now, now, STATUS_UNKNOWN),
        )

    async def mark_missing_offline(self, account_id: int, keep_ids: set[str]) -> int:
        now = utc_now_iso()
        async with self.db.transaction() as conn:
            if not keep_ids:
                cursor = await conn.execute(
                    "UPDATE item SET status = ?, synced_at = ?, updated_at = ? "
                    "WHERE account_id = ? AND status != ?",
                    (STATUS_OFFLINE, now, now, account_id, STATUS_OFFLINE),
                )
                return cursor.rowcount
            placeholders = ",".join("?" for _ in keep_ids)
            cursor = await conn.execute(
                f"UPDATE item SET status = ?, synced_at = ?, updated_at = ? "
                f"WHERE account_id = ? AND status != ? AND item_id NOT IN ({placeholders})",
                (STATUS_OFFLINE, now, now, account_id, STATUS_OFFLINE, *keep_ids),
            )
            return cursor.rowcount

    async def get(self, account_id: int, item_id: str) -> Item | None:
        row = await self.db.fetch_one(
            "SELECT * FROM item WHERE account_id = ? AND item_id = ?", (account_id, item_id)
        )
        return Item.from_row(row) if row is not None else None

    async def list(
        self,
        account_id: int,
        *,
        status: str = "",
        query: str = "",
        limit: int = 500,
    ) -> list[Item]:
        conditions = ["account_id = ?"]
        params: list[Any] = [account_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if query:
            conditions.append("(title LIKE ? OR item_id LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        rows = await self.db.fetch_all(
            "SELECT * FROM item WHERE "
            + " AND ".join(conditions)
            + " ORDER BY CASE status WHEN '在售' THEN 0 WHEN '已售出' THEN 1 ELSE 2 END, id DESC "
            "LIMIT ?",
            (*params, limit),
        )
        return [Item.from_row(row) for row in rows]

    async def counts(self, account_id: int) -> dict[str, int]:
        rows = await self.db.fetch_all(
            "SELECT status, COUNT(*) AS total FROM item WHERE account_id = ? GROUP BY status",
            (account_id,),
        )
        return {str(row["status"]): int(row["total"]) for row in rows}

    async def last_synced_at(self, account_id: int) -> str:
        row = await self.db.fetch_one(
            "SELECT MAX(synced_at) AS synced_at FROM item WHERE account_id = ?", (account_id,)
        )
        return str(row["synced_at"] or "") if row is not None else ""

    async def set_polish(
        self, account_id: int, item_id: str, *, polished_at: str = "", error: str = ""
    ) -> None:
        await self.db.write(
            "UPDATE item SET polished_at = ?, polish_error = ?, updated_at = ? "
            "WHERE account_id = ? AND item_id = ?",
            (polished_at, error[:300], utc_now_iso(), account_id, item_id),
        )


class ItemDeliveryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, account_id: int, item_id: str) -> ItemDelivery | None:
        row = await self.db.fetch_one(
            "SELECT * FROM item_delivery WHERE account_id = ? AND item_id = ?",
            (account_id, item_id),
        )
        return ItemDelivery.from_row(row) if row is not None else None

    async def list_by_account(self, account_id: int) -> list[ItemDelivery]:
        rows = await self.db.fetch_all(
            "SELECT * FROM item_delivery WHERE account_id = ? ORDER BY id", (account_id,)
        )
        return [ItemDelivery.from_row(row) for row in rows]

    async def upsert(
        self,
        account_id: int,
        item_id: str,
        *,
        enabled: bool = False,
        mode: str = "card",
        card_id: int | None = None,
        template: str = "",
        content: str = "",
        auto_consign: bool = True,
        remark: str = "",
    ) -> ItemDelivery:
        if mode not in DELIVERY_MODES:
            raise ValueError(f"未知发货方式: {mode}")
        now = utc_now_iso()
        await self.db.write(
            "INSERT INTO item_delivery (account_id, item_id, enabled, mode, card_id, template, "
            "content, auto_consign, remark, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id, item_id) DO UPDATE SET "
            "enabled = excluded.enabled, mode = excluded.mode, card_id = excluded.card_id, "
            "template = excluded.template, content = excluded.content, "
            "auto_consign = excluded.auto_consign, remark = excluded.remark, "
            "updated_at = excluded.updated_at",
            (
                account_id,
                item_id,
                int(enabled),
                mode,
                card_id,
                template,
                content,
                int(auto_consign),
                remark,
                now,
                now,
            ),
        )
        saved = await self.get(account_id, item_id)
        assert saved is not None
        return saved

    async def delete(self, account_id: int, item_id: str) -> None:
        await self.db.write(
            "DELETE FROM item_delivery WHERE account_id = ? AND item_id = ?",
            (account_id, item_id),
        )

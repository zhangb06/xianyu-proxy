from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.timeutil import utc_now_iso
from app.db.engine import Database


@dataclass(slots=True)
class AccountOps:
    account_id: int
    auto_polish: bool
    flower_enabled: bool

    @classmethod
    def from_row(cls, row: Any) -> AccountOps:
        return cls(
            account_id=int(row["account_id"]),
            auto_polish=bool(row["auto_polish"]),
            flower_enabled=bool(row["flower_enabled"]),
        )


class AccountOpsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, account_id: int) -> AccountOps:
        row = await self.db.fetch_one(
            "SELECT * FROM account_ops WHERE account_id = ?", (account_id,)
        )
        if row is None:
            return AccountOps(account_id=account_id, auto_polish=False, flower_enabled=False)
        return AccountOps.from_row(row)

    async def set(
        self,
        account_id: int,
        *,
        auto_polish: bool | None = None,
        flower_enabled: bool | None = None,
    ) -> AccountOps:
        current = await self.get(account_id)
        polish = current.auto_polish if auto_polish is None else auto_polish
        flower = current.flower_enabled if flower_enabled is None else flower_enabled
        await self.db.write(
            "INSERT INTO account_ops (account_id, auto_polish, flower_enabled, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (account_id) DO UPDATE SET "
            "auto_polish = excluded.auto_polish, flower_enabled = excluded.flower_enabled, "
            "updated_at = excluded.updated_at",
            (account_id, int(polish), int(flower), utc_now_iso()),
        )
        return await self.get(account_id)

    async def auto_polish_account_ids(self) -> list[int]:
        rows = await self.db.fetch_all(
            "SELECT o.account_id FROM account_ops o "
            "JOIN account a ON a.id = o.account_id "
            "WHERE o.auto_polish = 1 AND a.enabled = 1 ORDER BY o.account_id"
        )
        return [int(row["account_id"]) for row in rows]
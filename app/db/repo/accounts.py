from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.crypto import decrypt, encrypt
from app.core.ids import generate_device_id
from app.core.timeutil import utc_now_iso
from app.db.engine import Database


@dataclass(slots=True)
class Account:
    id: int
    xianyu_id: str
    remark: str
    enabled: bool
    status: str
    device_id: str
    proxy_url: str
    verification_mode: str
    last_error: str

    @classmethod
    def from_row(cls, row: Any) -> Account:
        return cls(
            id=int(row["id"]),
            xianyu_id=str(row["xianyu_id"]),
            remark=str(row["remark"]),
            enabled=bool(row["enabled"]),
            status=str(row["status"]),
            device_id=str(row["device_id"]),
            proxy_url=str(row["proxy_url"]),
            verification_mode=str(row["verification_mode"]),
            last_error=str(row["last_error"]),
        )


class AccountRepo:
    def __init__(self, db: Database, secret: str) -> None:
        self.db = db
        self.secret = secret

    async def upsert(self, xianyu_id: str, *, remark: str = "") -> Account:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("SELECT * FROM account WHERE xianyu_id = ?", (xianyu_id,))
            row = await cursor.fetchone()
            if row is not None:
                return Account.from_row(row)

            now = utc_now_iso()
            await conn.execute(
                "INSERT INTO account (xianyu_id, remark, device_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (xianyu_id, remark, generate_device_id(xianyu_id), now, now),
            )
            cursor = await conn.execute("SELECT * FROM account WHERE xianyu_id = ?", (xianyu_id,))
            created = await cursor.fetchone()
            assert created is not None
            return Account.from_row(created)

    async def get(self, account_id: int) -> Account | None:
        row = await self.db.fetch_one("SELECT * FROM account WHERE id = ?", (account_id,))
        return Account.from_row(row) if row else None

    async def get_by_xianyu_id(self, xianyu_id: str) -> Account | None:
        row = await self.db.fetch_one("SELECT * FROM account WHERE xianyu_id = ?", (xianyu_id,))
        return Account.from_row(row) if row else None

    async def list_all(self) -> list[Account]:
        rows = await self.db.fetch_all("SELECT * FROM account ORDER BY id")
        return [Account.from_row(row) for row in rows]

    async def list_enabled(self) -> list[Account]:
        rows = await self.db.fetch_all("SELECT * FROM account WHERE enabled = 1 ORDER BY id")
        return [Account.from_row(row) for row in rows]

    async def latest(self) -> Account | None:
        row = await self.db.fetch_one("SELECT * FROM account ORDER BY id DESC LIMIT 1")
        return Account.from_row(row) if row else None

    async def set_status(self, account_id: int, status: str, *, last_error: str = "") -> None:
        await self.db.write(
            "UPDATE account SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (status, last_error, utc_now_iso(), account_id),
        )

    async def save_cookie(self, account_id: int, cookies: dict[str, str]) -> None:
        plaintext = json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
        await self.db.write(
            "INSERT INTO account_credential (account_id, kind, value_enc, updated_at) "
            "VALUES (?, 'cookie', ?, ?) "
            "ON CONFLICT (account_id, kind) DO UPDATE "
            "SET value_enc = excluded.value_enc, updated_at = excluded.updated_at",
            (account_id, encrypt(plaintext, self.secret), utc_now_iso()),
        )

    async def load_cookie(self, account_id: int) -> dict[str, str] | None:
        row = await self.db.fetch_one(
            "SELECT value_enc FROM account_credential WHERE account_id = ? AND kind = 'cookie'",
            (account_id,),
        )
        if row is None:
            return None
        return json.loads(decrypt(str(row["value_enc"]), self.secret))

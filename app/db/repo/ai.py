from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.crypto import decrypt, encrypt
from app.core.timeutil import utc_now_iso
from app.db.engine import Database

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


@dataclass(slots=True)
class AIConfig:
    account_id: int
    enabled: bool
    base_url: str
    model: str
    persona: str
    product_info: str
    listing_price_cents: int
    price_floor_cents: int
    max_history: int
    timeout_seconds: int
    has_api_key: bool

    @classmethod
    def from_row(cls, row: Any) -> AIConfig:
        return cls(
            account_id=int(row["account_id"]),
            enabled=bool(row["enabled"]),
            base_url=str(row["base_url"]),
            model=str(row["model"]),
            persona=str(row["persona"]),
            product_info=str(row["product_info"]),
            listing_price_cents=int(row["listing_price_cents"]),
            price_floor_cents=int(row["price_floor_cents"]),
            max_history=int(row["max_history"]),
            timeout_seconds=int(row["timeout_seconds"]),
            has_api_key=bool(row["api_key_enc"]),
        )


class AIConfigRepo:
    def __init__(self, db: Database, secret: str) -> None:
        self.db = db
        self.secret = secret

    async def get(self, account_id: int) -> AIConfig:
        row = await self.db.fetch_one("SELECT * FROM ai_config WHERE account_id = ?", (account_id,))
        if row is None:
            return AIConfig(
                account_id=account_id,
                enabled=False,
                base_url=DEFAULT_BASE_URL,
                model=DEFAULT_MODEL,
                persona="",
                product_info="",
                listing_price_cents=0,
                price_floor_cents=0,
                max_history=10,
                timeout_seconds=20,
                has_api_key=False,
            )
        return AIConfig.from_row(row)

    async def get_api_key(self, account_id: int) -> str:
        row = await self.db.fetch_one(
            "SELECT api_key_enc FROM ai_config WHERE account_id = ?", (account_id,)
        )
        if row is None or not row["api_key_enc"]:
            return ""
        return decrypt(str(row["api_key_enc"]), self.secret)

    async def put(
        self,
        account_id: int,
        *,
        enabled: bool,
        base_url: str,
        model: str,
        persona: str,
        product_info: str,
        listing_price_cents: int,
        price_floor_cents: int,
        max_history: int,
        timeout_seconds: int,
        api_key: str | None = None,
    ) -> AIConfig:
        existing = await self.db.fetch_one(
            "SELECT api_key_enc FROM ai_config WHERE account_id = ?", (account_id,)
        )
        if api_key is None:
            key_enc = str(existing["api_key_enc"]) if existing else ""
        else:
            key_enc = encrypt(api_key, self.secret) if api_key else ""
        await self.db.write(
            "INSERT INTO ai_config (account_id, enabled, base_url, model, api_key_enc, persona, "
            "product_info, listing_price_cents, price_floor_cents, max_history, "
            "timeout_seconds, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id) DO UPDATE SET enabled = excluded.enabled, "
            "base_url = excluded.base_url, model = excluded.model, "
            "api_key_enc = excluded.api_key_enc, persona = excluded.persona, "
            "product_info = excluded.product_info, "
            "listing_price_cents = excluded.listing_price_cents, "
            "price_floor_cents = excluded.price_floor_cents, "
            "max_history = excluded.max_history, "
            "timeout_seconds = excluded.timeout_seconds, updated_at = excluded.updated_at",
            (
                account_id,
                int(enabled),
                base_url,
                model,
                key_enc,
                persona,
                product_info,
                listing_price_cents,
                price_floor_cents,
                max_history,
                timeout_seconds,
                utc_now_iso(),
            ),
        )
        return await self.get(account_id)


class AIUsageRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(
        self,
        account_id: int,
        *,
        chat_id: str,
        status: str,
        latency_ms: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        detail: str = "",
    ) -> None:
        await self.db.write(
            "INSERT INTO ai_usage (account_id, chat_id, status, latency_ms, "
            "prompt_tokens, completion_tokens, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, chat_id, status, latency_ms, prompt_tokens, completion_tokens, detail),
        )

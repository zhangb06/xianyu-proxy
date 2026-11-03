from __future__ import annotations

import secrets

from app.db.engine import Database


async def resolve_secret(db: Database, configured: str) -> str:
    """返回加密密钥：显式配置优先，否则生成一次并持久化。"""
    if configured:
        return configured

    cursor = await db.conn.execute("SELECT value FROM setting WHERE key = 'secret_key'")
    row = await cursor.fetchone()
    if row is not None and row["value"]:
        return str(row["value"])

    value = secrets.token_urlsafe(32)
    await db.conn.execute(
        "INSERT INTO setting (key, value) VALUES ('secret_key', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (value,),
    )
    return value

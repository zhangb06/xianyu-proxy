from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

_MIGRATIONS = Path(__file__).parent / "migrations"


class Database:
    """单进程 SQLite 访问层。

    写操作通过进程内锁串行化；需要原子性的多语句使用 transaction()，
    事务内禁止网络等待。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def _migrate(self) -> None:
        cursor = await self.conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        version = int(row[0]) if row else 0
        for file in sorted(_MIGRATIONS.glob("*.sql")):
            number = int(file.name.split("_", 1)[0])
            if number <= version:
                continue
            await self.conn.executescript(file.read_text(encoding="utf-8"))
            await self.conn.execute(f"PRAGMA user_version = {number}")

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        cursor = await self.conn.execute(sql, params)
        return list(await cursor.fetchall())

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        cursor = await self.conn.execute(sql, params)
        return await cursor.fetchone()

    async def write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._write_lock:
            await self.conn.execute(sql, params)

    async def write_returning_id(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except Exception:
                await self.conn.execute("ROLLBACK")
                raise
            else:
                await self.conn.execute("COMMIT")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await self._conn.close()
            self._conn = None

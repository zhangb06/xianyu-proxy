from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from loguru import logger

from app.db.repo.accounts import AccountRepo
from app.protocol.items import ItemInfo, fetch_item_info
from app.protocol.mtop import CookieJar

ItemFetcher = Callable[..., Awaitable[ItemInfo | None]]

CACHE_TTL_SECONDS = 600.0
FAILURE_TTL_SECONDS = 60.0


class ItemInfoService:
    """按 (账号, 商品) 缓存商品详情，避免每条消息都请求闲鱼。"""

    def __init__(
        self,
        accounts: AccountRepo,
        *,
        ttl: float = CACHE_TTL_SECONDS,
        fetcher: ItemFetcher = fetch_item_info,
    ) -> None:
        self.accounts = accounts
        self.ttl = ttl
        self._fetcher = fetcher
        self._cache: dict[tuple[int, str], tuple[float, ItemInfo | None]] = {}
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    async def get(self, account_id: int, item_id: str) -> ItemInfo | None:
        if not item_id:
            return None
        key = (account_id, item_id)
        cached = self._cached(key)
        if cached is not _MISS:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cached(key)
            if cached is not _MISS:
                return cached
            info = await self._load(account_id, item_id)
            self._cache[key] = (time.monotonic(), info)
            return info

    def _cached(self, key: tuple[int, str]) -> ItemInfo | None | object:
        entry = self._cache.get(key)
        if entry is None:
            return _MISS
        fetched_at, info = entry
        ttl = self.ttl if info is not None else FAILURE_TTL_SECONDS
        return info if time.monotonic() - fetched_at < ttl else _MISS

    async def _load(self, account_id: int, item_id: str) -> ItemInfo | None:
        cookie = await self.accounts.load_cookie(account_id)
        if not cookie:
            return None
        account = await self.accounts.get(account_id)
        proxy = account.proxy_url if account is not None else ""
        try:
            return await self._fetcher(CookieJar(cookie), item_id, proxy=proxy)
        except Exception as exc:
            logger.warning(f"账号 {account_id} 获取商品 {item_id} 详情失败: {exc}")
            return None


_MISS = object()

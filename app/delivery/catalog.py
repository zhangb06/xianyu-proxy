from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.timeutil import utc_now_iso
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.items import ItemRepo
from app.protocol.listing import MAX_SYNC_PAGES, ListedItem, ListingPage, fetch_listing_page
from app.protocol.mtop import CookieJar, MtopError

ListingFetcher = Callable[..., Awaitable[ListingPage]]


class CatalogError(RuntimeError):
    pass


@dataclass(slots=True)
class SyncResult:
    fetched: int
    pages: int
    synced_at: str


class ItemCatalogService:
    """从闲鱼同步当前账号已发布的商品。"""

    def __init__(
        self,
        db: Database,
        accounts: AccountRepo,
        *,
        fetcher: ListingFetcher = fetch_listing_page,
    ) -> None:
        self.accounts = accounts
        self.repo = ItemRepo(db)
        self._fetcher = fetcher

    async def sync(self, account: Account) -> SyncResult:
        cookie = await self.accounts.load_cookie(account.id)
        if not cookie:
            raise CatalogError("账号尚未登录，无法同步商品")
        jar = CookieJar(cookie)
        collected: dict[str, ListedItem] = {}
        pages = 0
        complete = False
        while pages < MAX_SYNC_PAGES:
            pages += 1
            try:
                page: ListingPage = await self._fetcher(
                    jar,
                    account.xianyu_id,
                    pages,
                    proxy=account.proxy_url,
                )
            except MtopError as exc:
                raise CatalogError(str(exc) or "商品列表获取失败") from exc
            for item in page.items:
                collected[item.item_id] = item
            if not page.next_page or not page.items:
                complete = True
                break

        synced_at = utc_now_iso()
        await self.repo.upsert_many(account.id, list(collected.values()), synced_at=synced_at)
        if complete:
            await self.repo.mark_missing_offline(account.id, set(collected))
        return SyncResult(fetched=len(collected), pages=pages, synced_at=synced_at)

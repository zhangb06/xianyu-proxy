from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.chats import ChatRepo
from app.db.repo.delivery import OrderRepo, OutboxRepo
from app.db.repo.items import ItemRepo
from app.delivery.service import delivery_key
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.trade import SoldOrder, fetch_sold_orders_page

PAGE_SIZE = 30
MAX_PAGES = 10
CURSOR_OVERLAP_SECONDS = 300
DELIVERY_GRACE_SECONDS = 600


class OrderSyncError(RuntimeError):
    """订单同步无法执行或拉取失败。"""


@dataclass(slots=True)
class OrderSyncResult:
    account_id: int
    fetched: int = 0
    inserted: int = 0
    delivered: int = 0
    queued: int = 0
    manual: int = 0
    skipped: int = 0
    last_synced_at: str = ""


class OrderSyncService:
    """从闲鱼卖家订单列表补录上次同步之后的订单。"""

    def __init__(self, db: Database, accounts: AccountRepo, *, fetcher=fetch_sold_orders_page) -> None:
        self.accounts = accounts
        self.orders = OrderRepo(db)
        self.chats = ChatRepo(db)
        self.items = ItemRepo(db)
        self.outbox = OutboxRepo(db)
        self._fetcher = fetcher
        self._locks: dict[int, asyncio.Lock] = {}

    async def sync(self, account_id: int, *, now: datetime | None = None) -> OrderSyncResult:
        account = await self.accounts.get(account_id)
        if account is None:
            raise OrderSyncError("账号不存在")
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            return await self._sync(account, now or datetime.now(UTC))

    async def _sync(self, account: Account, moment: datetime) -> OrderSyncResult:
        cookie = await self.accounts.load_cookie(account.id)
        if not cookie:
            raise OrderSyncError("账号尚未登录（缺少 Cookie）")
        jar = CookieJar(dict(cookie))
        cursor = _parse_iso(account.orders_synced_at)
        newest = cursor
        result = OrderSyncResult(account_id=account.id)
        for page in range(1, MAX_PAGES + 1):
            try:
                data = await self._fetcher(
                    jar, page=page, rows_per_page=PAGE_SIZE, proxy=account.proxy_url
                )
            except MtopError as exc:
                raise OrderSyncError(f"获取闲鱼订单失败: {exc}") from exc
            if not data.orders:
                break
            reached_old = False
            for order in data.orders:
                result.fetched += 1
                created = _parse_iso(order.created_at)
                if created is not None and (newest is None or created > newest):
                    newest = created
                if (
                    cursor is not None
                    and created is not None
                    and created <= cursor - timedelta(seconds=CURSOR_OVERLAP_SECONDS)
                ):
                    reached_old = True
                    continue
                await self._apply(account, order, created, moment, result)
            if reached_old or not data.next_page:
                break
        updated = jar.as_dict()
        if updated != cookie:
            await self.accounts.save_cookie(account.id, updated)
        if newest is not None:
            result.last_synced_at = newest.isoformat(timespec="seconds").replace("+00:00", "Z")
            await self.accounts.set_orders_synced_at(account.id, result.last_synced_at)
        else:
            result.last_synced_at = account.orders_synced_at
        logger.info(
            f"订单同步完成 account={account.id} fetched={result.fetched} "
            f"inserted={result.inserted} queued={result.queued} manual={result.manual} "
            f"delivered={result.delivered} skipped={result.skipped}"
        )
        return result

    async def _apply(
        self,
        account: Account,
        order: SoldOrder,
        paid_at: datetime | None,
        moment: datetime,
        result: OrderSyncResult,
    ) -> None:
        if order.state == "skip":
            result.skipped += 1
            return
        existing = await self.orders.get_by_no(account.id, order.order_no)
        if existing is not None:
            if order.state == "delivered" and existing.status != "delivered":
                await self.orders.set_status(
                    existing.id, "delivered", detail="订单同步：闲鱼已发货", delivered=True
                )
                result.delivered += 1
            else:
                result.skipped += 1
            return
        chat = await self.chats.latest_for_peer(account.id, order.buyer_id)
        item = await self.items.get(account.id, order.item_id)
        created = await self.orders.create_if_absent(
            account.id,
            order_no=order.order_no,
            chat_id=chat.chat_id if chat is not None else "",
            buyer_id=order.buyer_id,
            item_id=order.item_id,
            title=item.title if item is not None else "",
            amount_cents=order.amount_cents,
            paid_at=order.created_at,
        )
        if created is None:
            result.skipped += 1
            return
        result.inserted += 1
        if order.state == "delivered":
            await self.orders.set_status(
                created.id, "delivered", detail="订单同步：闲鱼已发货", delivered=True
            )
            result.delivered += 1
            return
        age = None if paid_at is None else (moment - paid_at).total_seconds()
        if chat is not None and age is not None and age <= DELIVERY_GRACE_SECONDS:
            queued = await self.outbox.enqueue(
                account.id,
                dedup_key=delivery_key(account.id, order.order_no),
                task="delivery",
                payload={"order_id": created.id},
            )
            if queued:
                result.queued += 1
            else:
                result.skipped += 1
            return
        reason = (
            "订单同步：缺少会话，需人工"
            if chat is None
            else "订单同步：付款已超过 10 分钟，需人工确认"
        )
        await self.orders.set_status(created.id, "manual", detail=reason)
        result.manual += 1


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.chats import ChatRepo
from app.db.repo.delivery import OrderRepo, OutboxRepo
from app.db.repo.items import ItemRepo
from app.delivery.order_sync import (
    DELIVERY_GRACE_SECONDS,
    OrderSyncError,
    OrderSyncService,
    delivery_key,
)
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.trade import (
    SOLD_API,
    SoldOrder,
    SoldOrderPage,
    fetch_sold_orders_page,
    parse_sold_order,
)


class StubFetcher:
    def __init__(self, pages: list[SoldOrderPage] | None = None, *, error: str = "") -> None:
        self.pages = pages or []
        self.error = error
        self.calls: list[int] = []

    async def __call__(
        self, jar, *, page: int = 1, rows_per_page: int = 30, proxy: str = ""
    ) -> SoldOrderPage:
        self.calls.append(page)
        if self.error:
            raise MtopError(self.error)
        index = page - 1
        return self.pages[index] if index < len(self.pages) else SoldOrderPage()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _paid(order_no: str, buyer_id: str, *, minutes_ago: float = 1, item_id: str = "42") -> SoldOrder:
    return SoldOrder(
        order_no=order_no,
        item_id=item_id,
        buyer_id=buyer_id,
        buyer_nick="买家",
        amount_cents=990,
        created_at=_iso(datetime.now(UTC) - timedelta(minutes=minutes_ago)),
        state="paid",
    )


async def _setup(tmp_path: Path) -> tuple[Database, Account, AccountRepo]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001")
    await accounts.set_enabled(account.id, True)
    await accounts.save_cookie(account.id, {"unb": "10001", "cookie2": "abc"})
    return db, account, accounts


async def test_sync_inserts_orders_and_queues_only_recent_with_chat(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await ItemRepo(db).ensure(account.id, "42", title="电子书", status="在售")
        await ChatRepo(db).ensure(account.id, "chat-buyer1", peer_id="buyer1")
        await ChatRepo(db).ensure(account.id, "chat-buyer3", peer_id="buyer3")
        delivered_old = SoldOrder(
            order_no="d1",
            item_id="42",
            buyer_id="buyer9",
            buyer_nick="",
            amount_cents=990,
            created_at=_iso(datetime.now(UTC) - timedelta(days=2)),
            state="delivered",
        )
        skipped = SoldOrder(
            order_no="s1",
            item_id="42",
            buyer_id="buyer8",
            buyer_nick="",
            amount_cents=990,
            created_at=_iso(datetime.now(UTC)),
            state="skip",
        )
        orders = [
            _paid("o1", "buyer1"),
            _paid("o2", "buyer2"),
            _paid("o3", "buyer3", minutes_ago=30),
            delivered_old,
            skipped,
        ]
        fetcher = StubFetcher([SoldOrderPage(orders=orders, next_page=True)])
        service = OrderSyncService(db, accounts, fetcher=fetcher)

        result = await service.sync(account.id)

        assert (result.fetched, result.inserted) == (5, 4)
        assert result.queued == 1
        assert result.manual == 2
        assert result.delivered == 1
        assert result.skipped == 1

        repo = OrderRepo(db)
        queued = await repo.get_by_no(account.id, "o1")
        assert queued is not None and queued.status == "paid"
        assert queued.chat_id == "chat-buyer1" and queued.amount_cents == 990
        no_chat = await repo.get_by_no(account.id, "o2")
        assert no_chat is not None and no_chat.status == "manual"
        assert "缺少会话" in no_chat.detail
        stale = await repo.get_by_no(account.id, "o3")
        assert stale is not None and stale.status == "manual"
        assert "超过 10 分钟" in stale.detail
        delivered = await repo.get_by_no(account.id, "d1")
        assert delivered is not None and delivered.status == "delivered"
        assert await repo.get_by_no(account.id, "s1") is None

        tasks = await OutboxRepo(db).list_recent(account.id)
        assert any(task["dedup_key"] == delivery_key(account.id, "o1") for task in tasks)
        stored = await accounts.get(account.id)
        assert stored is not None and stored.orders_synced_at == result.last_synced_at
    finally:
        await db.close()


async def test_sync_stops_at_cursor_and_skips_seen_orders(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await accounts.set_orders_synced_at(
            account.id, _iso(datetime.now(UTC) - timedelta(minutes=20))
        )
        old = _paid("old", "buyer1", minutes_ago=60)
        fresh = _paid("fresh", "buyer1")
        fetcher = StubFetcher([SoldOrderPage(orders=[fresh, old], next_page=True)])
        service = OrderSyncService(db, accounts, fetcher=fetcher)

        first = await service.sync(account.id)
        assert first.inserted == 1
        assert fetcher.calls == [1]

        second_fetcher = StubFetcher([SoldOrderPage(orders=[fresh, old], next_page=True)])
        service._fetcher = second_fetcher
        second = await service.sync(account.id)
        assert second.inserted == 0
        assert second.skipped == 1
        assert second_fetcher.calls == [1]
        orders = await OrderRepo(db).list(account.id)
        assert [order.order_no for order in orders] == ["fresh"]
    finally:
        await db.close()


async def test_sync_marks_existing_order_delivered(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        order = await OrderRepo(db).create_if_absent(account.id, order_no="o9", chat_id="c1")
        assert order is not None
        delivered = SoldOrder(
            order_no="o9",
            item_id="42",
            buyer_id="buyer1",
            buyer_nick="",
            amount_cents=990,
            created_at=_iso(datetime.now(UTC)),
            state="delivered",
        )
        service = OrderSyncService(
            db, accounts, fetcher=StubFetcher([SoldOrderPage(orders=[delivered])])
        )

        result = await service.sync(account.id)

        assert result.delivered == 1
        stored = await OrderRepo(db).get_by_no(account.id, "o9")
        assert stored is not None and stored.status == "delivered"
    finally:
        await db.close()


async def test_sync_requires_cookie(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await db.write("DELETE FROM account_credential WHERE account_id = ?", (account.id,))
        service = OrderSyncService(db, accounts, fetcher=StubFetcher())
        with pytest.raises(OrderSyncError) as info:
            await service.sync(account.id)
        assert "尚未登录" in str(info.value)
    finally:
        await db.close()


async def test_sync_reports_fetch_failure(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        service = OrderSyncService(db, accounts, fetcher=StubFetcher(error="FAIL_SYS_TOKEN_EXPIRED"))
        with pytest.raises(OrderSyncError) as info:
            await service.sync(account.id)
        assert "FAIL_SYS_TOKEN_EXPIRED" in str(info.value)
    finally:
        await db.close()


def test_grace_is_ten_minutes() -> None:
    assert DELIVERY_GRACE_SECONDS == 600

def _sold_item(**common: str) -> dict:
    return {
        "commonData": {
            "orderId": "o1",
            "orderStatus": "待发货",
            "itemId": "42",
            "createTime": "2026-11-11 09:30:00",
            **common,
        },
        "buyerInfoVO": {"buyerId": "b1", "userNick": "小明"},
        "priceVO": {"totalPrice": "9.9"},
    }


def test_parse_sold_order_maps_status_time_and_amount() -> None:
    order = parse_sold_order(_sold_item())
    assert order is not None
    assert order.order_no == "o1" and order.state == "paid"
    assert order.buyer_id == "b1" and order.amount_cents == 990
    assert order.created_at == "2026-11-11T01:30:00Z"

    assert parse_sold_order(_sold_item(orderStatus="已发货")).state == "delivered"  # type: ignore[union-attr]
    assert parse_sold_order(_sold_item(orderStatus="待付款")).state == "skip"  # type: ignore[union-attr]
    assert parse_sold_order(_sold_item(inRefund="true")).state == "skip"  # type: ignore[union-attr]
    assert parse_sold_order({"commonData": {}}) is None


async def test_fetch_sold_orders_page_parses_module(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.protocol import trade

    async def fake_token(jar, *, timeout: float = 30.0, proxy: str = "") -> None:
        return None

    async def fake_call(jar, api, version, data, **kwargs):
        assert api == SOLD_API and data["pageNumber"] == 1
        return {
            "ret": ["SUCCESS::调用成功"],
            "data": {"module": {"items": [_sold_item()], "nextPage": "true", "totalCount": "31"}},
        }

    monkeypatch.setattr(trade, "get_m_h5_tk", fake_token)
    monkeypatch.setattr(trade, "mtop_call", fake_call)
    page = await fetch_sold_orders_page(CookieJar({}))
    assert page.next_page is True and page.total_count == 31
    assert [order.order_no for order in page.orders] == ["o1"]


async def test_fetch_sold_orders_page_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.protocol import trade

    async def fake_token(jar, *, timeout: float = 30.0, proxy: str = "") -> None:
        return None

    async def fake_call(jar, api, version, data, **kwargs):
        return {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]}

    monkeypatch.setattr(trade, "get_m_h5_tk", fake_token)
    monkeypatch.setattr(trade, "mtop_call", fake_call)
    with pytest.raises(MtopError) as info:
        await fetch_sold_orders_page(CookieJar({}))
    assert "FAIL_SYS_SESSION_EXPIRED" in str(info.value)

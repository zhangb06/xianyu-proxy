from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.items import ItemDeliveryRepo, ItemRepo
from app.delivery.catalog import CatalogError, ItemCatalogService, SyncResult
from app.protocol.items import parse_item_info
from app.protocol.listing import ListedItem, ListingPage, parse_listing
from app.runtime.supervisor import Supervisor


def test_parse_item_info_normalizes_payload() -> None:
    info = parse_item_info(
        {
            "itemId": "1096185526203",
            "title": "【最新小说合集】",
            "description": "拍下自动发货解压，\n随时拍下自动发货",
            "reservePriceMoney": {"cent": 10, "amount": 0.1},
            "online": True,
        }
    )
    assert info is not None
    assert info.title == "【最新小说合集】"
    assert info.description == "拍下自动发货解压， 随时拍下自动发货"
    assert info.price_cents == 10
    assert info.status == "在售"


def test_parse_item_info_price_fallback_and_offline() -> None:
    info = parse_item_info(
        {
            "itemId": "1",
            "title": "九成新相机",
            "reservePrice": "1500.50",
            "online": False,
            "pullOff": True,
        }
    )
    assert info is not None
    assert info.price_cents == 150050
    assert info.status == "已下架"

    fallback = parse_item_info(
        {"itemId": "2", "title": "耳机", "attributeMap": {"firstPrice": "0.10"}, "online": True}
    )
    assert fallback is not None
    assert fallback.price_cents == 10


def test_parse_item_info_requires_id_and_title() -> None:
    assert parse_item_info({"title": "无 ID"}) is None
    assert parse_item_info({"itemId": "1"}) is None


def test_parse_listing_payload() -> None:
    page = parse_listing(
        {
            "cardList": [
                {
                    "cardData": {
                        "id": "1096185526203",
                        "title": "【小说合集】",
                        "itemStatus": 0,
                        "priceInfo": {"price": "0.10"},
                        "picInfo": {"picUrl": "http://img.alicdn.com/a.jpg"},
                    },
                    "cardType": 1003,
                },
                {
                    "cardData": {
                        "detailParams": {
                            "itemId": "710100715884",
                            "title": "WebGIS开发",
                            "soldPrice": "249.90",
                            "picUrl": "https://img.alicdn.com/b.heic",
                        },
                        "itemStatus": 1,
                    }
                },
                {"cardData": {"id": "1096185526203", "title": "重复"}},
                {"cardData": {"id": "no-title"}},
            ],
            "nextPage": True,
        }
    )
    assert [item.item_id for item in page.items] == ["1096185526203", "710100715884"]
    assert page.items[0].price_cents == 10
    assert page.items[0].status == "在售"
    assert page.items[0].cover_url == "https://img.alicdn.com/a.jpg"
    assert page.items[1].price_cents == 24990
    assert page.items[1].status == "已售出"
    assert page.next_page is True


async def test_sync_upserts_and_marks_missing_items(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        account = await accounts.upsert("10001")
        service = ItemCatalogService(db, accounts)
        with pytest.raises(CatalogError):
            await service.sync(account)

        await accounts.save_cookie(account.id, {"unb": "10001"})
        pages = {
            1: ListingPage(
                [
                    ListedItem("1", "商品一", 100, "在售", "https://img/1.jpg"),
                    ListedItem("2", "商品二", 200, "在售", ""),
                ],
                next_page=True,
            ),
            2: ListingPage([ListedItem("3", "商品三", 300, "已售出", "")], next_page=False),
        }
        calls: list[tuple[str, int]] = []

        async def fetcher(jar, user_id, page_number, *, proxy=""):  # type: ignore[no-untyped-def]
            calls.append((user_id, page_number))
            return pages[page_number]

        first = await ItemCatalogService(db, accounts, fetcher=fetcher).sync(account)
        assert first.fetched == 3 and first.pages == 2
        assert calls == [("10001", 1), ("10001", 2)]
        repo = ItemRepo(db)
        assert (await repo.get(account.id, "1")).price_cents == 100  # type: ignore[union-attr]

        pages = {
            1: ListingPage(
                [ListedItem("1", "商品一（新）", 150, "在售", "")], next_page=False
            )
        }
        second = await ItemCatalogService(db, accounts, fetcher=fetcher).sync(account)
        assert second.fetched == 1
        updated = await repo.get(account.id, "1")
        assert updated is not None and updated.title == "商品一（新）" and updated.price_cents == 150
        assert (await repo.get(account.id, "2")).status == "已下架"  # type: ignore[union-attr]
        assert (await repo.get(account.id, "3")).status == "已下架"  # type: ignore[union-attr]
        assert await repo.last_synced_at(account.id) == second.synced_at
        assert await repo.counts(account.id) == {"在售": 1, "已下架": 2}

        pages = {1: ListingPage([], next_page=False)}
        third = await ItemCatalogService(db, accounts, fetcher=fetcher).sync(account)
        assert third.fetched == 0
        assert await repo.counts(account.id) == {"已下架": 3}
    finally:
        await db.close()


async def test_supervisor_syncs_catalogs_for_logged_in_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        first = await accounts.upsert("10001")
        await accounts.save_cookie(first.id, {"unb": "10001"})
        await accounts.upsert("10002")
        disabled = await accounts.upsert("10003")
        await accounts.save_cookie(disabled.id, {"unb": "10003"})
        await accounts.set_enabled(disabled.id, False)

        supervisor = Supervisor(db, Settings(catalog_sync_seconds=60), "secret")
        calls: list[int] = []

        async def fake_sync(account):  # type: ignore[no-untyped-def]
            calls.append(account.id)
            return SyncResult(fetched=0, pages=1, synced_at="")

        monkeypatch.setattr(supervisor.catalog, "sync", fake_sync)
        await supervisor.sync_catalogs()
        assert calls == [first.id]

        async def failing_sync(account):  # type: ignore[no-untyped-def]
            calls.append(account.id)
            raise CatalogError("cookie 失效")

        monkeypatch.setattr(supervisor.catalog, "sync", failing_sync)
        calls.clear()
        await supervisor.sync_catalogs()
        await supervisor.sync_catalogs()
        assert calls == [first.id]
    finally:
        await db.close()


async def test_item_delivery_config_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        account = await accounts.upsert("10001")
        repo = ItemDeliveryRepo(db)
        assert await repo.get(account.id, "42") is None

        saved = await repo.upsert(
            account.id, "42", mode="fixed", content="链接 {item_title}", auto_consign=False
        )
        assert saved.mode == "fixed" and saved.auto_consign is False
        assert (await repo.get(account.id, "42")).content == "链接 {item_title}"  # type: ignore[union-attr]

        updated = await repo.upsert(account.id, "42", mode="manual", enabled=False)
        assert updated.mode == "manual" and updated.enabled is False
        with pytest.raises(ValueError):
            await repo.upsert(account.id, "42", mode="bogus")

        await repo.delete(account.id, "42")
        assert await repo.get(account.id, "42") is None
    finally:
        await db.close()

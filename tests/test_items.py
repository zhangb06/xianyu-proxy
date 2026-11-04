from __future__ import annotations

from app.protocol.items import parse_item_info


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

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.chats import ChatRepo
from app.db.repo.delivery import CardRepo, DeliveryRuleRepo, OrderRepo
from app.db.repo.items import ItemDeliveryRepo, ItemRepo
from app.db.repo.messages import MessageRepo
from app.delivery.service import (
    DeliveryPermanent,
    DeliveryRetry,
    DeliveryService,
    detect_order_event,
    extract_order_no,
    is_bracketed_notice,
    is_system_notice,
)
from app.pipeline.engine import ReplyPipeline, SendResult


class StubSender:
    def __init__(self, *, ok: bool = True, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.calls: list[tuple[str, str, str]] = []

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        self.calls.append((chat_id, to_user_id, text))
        if self.ok:
            return SendResult(ok=True, msg_id="mid-deliver")
        return SendResult(ok=False, reason=self.reason)


async def _setup(tmp_path: Path) -> tuple[Database, Any, DeliveryService]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001")
    return db, account, DeliveryService(db, accounts)


async def _card_with_secrets(db: Database, account_id: int, count: int) -> Any:
    cards = CardRepo(db)
    card = await cards.create(account_id, name="电子书")
    await cards.add_secrets(card.id, [f"CODE-{index}" for index in range(count)])
    await cards.bind_item(account_id, card.id, "42")
    await DeliveryRuleRepo(db).create(
        account_id,
        code=f"legacy-{card.id}",
        name="旧版兜底",
        template="卡密 {secret}",
        item_id="42",
        priority=999,
    )
    return card


def test_detect_markers() -> None:
    assert detect_order_event("我已付款，等待你发货") == "paid"
    assert detect_order_event("买家已拍下，待付款") == "pending"
    assert detect_order_event("你已发货，查看发货凭证") == "shipped"
    assert detect_order_event("在吗") == ""
    assert is_system_notice("[订单通知] 买家已付款")
    assert is_system_notice("订单即将自动确认收货")
    assert not is_system_notice("这个能便宜点吗")
    assert is_bracketed_notice("[我已付款，等待你发货]")
    assert not is_bracketed_notice("我已付款，等待你发货")
    raw = {"a": {"orderId": "2026110500012345"}}
    assert extract_order_no(raw) == "2026110500012345"


async def test_observe_dedups_duplicate_paid_notice(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        chat = await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        parsed = {
            "text": "我已付款，等待你发货",
            "item_id": "42",
            "timestamp_ms": 1762300000000,
            "raw": {"10": {"orderId": "2026110500012345"}},
        }
        assert await service.observe(account, chat, parsed) == "paid"
        assert await service.observe(account, chat, parsed) == "paid"

        orders = await OrderRepo(db).list(account.id)
        assert len(orders) == 1
        assert orders[0].order_no == "2026110500012345"
        tasks = await service.outbox.list_recent(account.id)
        assert len(tasks) == 1
        assert tasks[0]["dedup_key"] == f"deliver:{account.id}:2026110500012345"
    finally:
        await db.close()


async def test_observe_ignores_untrusted_payment_text(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        chat = await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        parsed = {
            "text": "我已付款，等待你发货",
            "item_id": "42",
            "timestamp_ms": 1762300000100,
            "raw": {"10": {"reminderContent": "我已付款，等待你发货"}},
        }

        assert await service.observe(account, chat, parsed) == ""
        assert await OrderRepo(db).list(account.id) == []
        assert await service.outbox.list_recent(account.id) == []
        events = await db.fetch_all(
            "SELECT type, level FROM event_log WHERE type = 'order.untrusted_notice'"
        )
        assert len(events) == 1 and events[0]["level"] == "warning"
    finally:
        await db.close()


async def test_observe_accepts_bracketed_notice_without_order_no(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        chat = await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        parsed = {
            "text": "[我已付款，等待你发货]",
            "item_id": "42",
            "timestamp_ms": 1762300000200,
            "raw": {},
        }

        assert await service.observe(account, chat, parsed) == "paid"
        order = await service.orders.get_by_no(account.id, "buyer1-1762300000200")
        assert order is not None and order.status == "paid"
        tasks = await service.outbox.list_recent(account.id)
        assert [task["dedup_key"] for task in tasks] == [
            f"deliver:{account.id}:buyer1-1762300000200"
        ]
    finally:
        await db.close()


async def test_observe_dedups_payment_and_reminder_notices(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        chats = ChatRepo(db)
        chat_a = await chats.ensure(account.id, "buyerA", peer_id="buyerA", item_id="42")
        chat_b = await chats.ensure(account.id, "buyerB", peer_id="buyerB", item_id="42")

        def notice(text: str, timestamp_ms: int) -> dict:
            return {"text": text, "item_id": "42", "timestamp_ms": timestamp_ms, "raw": {}}

        assert (
            await service.observe(
                account, chat_a, notice("[我已付款，等待你发货]", 1762300000800)
            )
            == "paid"
        )
        assert (
            await service.observe(account, chat_a, notice("[记得及时发货]", 1762300000824))
            == "paid"
        )
        assert (
            await service.observe(account, chat_b, notice("[记得及时发货]", 1762300000900))
            == "paid"
        )
        assert (
            await service.observe(
                account, chat_b, notice("[我已付款，等待你发货]", 1762300000924)
            )
            == "paid"
        )

        orders = await OrderRepo(db).list(account.id)
        assert sorted(order.chat_id for order in orders) == ["buyerA", "buyerB"]
        assert len(await service.outbox.list_recent(account.id)) == 2
    finally:
        await db.close()


async def test_observe_creates_new_order_after_dedup_window(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        chat = await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        first = {
            "text": "[我已付款，等待你发货]",
            "item_id": "42",
            "timestamp_ms": 1762300001100,
            "raw": {},
        }
        second = {
            "text": "[我已付款，等待你发货]",
            "item_id": "42",
            "timestamp_ms": 1762300001100 + 600_000,
            "raw": {},
        }

        assert await service.observe(account, chat, first) == "paid"
        assert await service.observe(account, chat, second) == "paid"

        orders = await OrderRepo(db).list(account.id)
        assert len(orders) == 2
    finally:
        await db.close()


async def test_reconcile_dedups_payment_and_reminder_notices(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        messages = MessageRepo(db)
        for index, (text, timestamp_ms) in enumerate(
            [
                ("[我已付款，等待你发货]", 1762300001200),
                ("[记得及时发货]", 1762300001224),
            ],
            start=1,
        ):
            await messages.insert_inbound(
                account.id,
                {
                    "dedup_key": f"m-pair-{index}",
                    "msg_id": f"m-pair-{index}",
                    "chat_id": "buyer1",
                    "sender_id": "buyer1",
                    "kind": "text",
                    "text": text,
                    "item_id": "42",
                    "timestamp_ms": timestamp_ms,
                    "raw": {},
                },
            )

        created = await service.reconcile(account.id)

        assert created == 1
        orders = await OrderRepo(db).list(account.id)
        assert len(orders) == 1
        assert len(await service.outbox.list_recent(account.id)) == 1
    finally:
        await db.close()


async def test_observe_ignores_untrusted_shipped_text(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        chat = await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        order = await service.orders.create_if_absent(
            account.id, order_no="2026110500012345", chat_id="buyer1", item_id="42"
        )
        assert order is not None

        untrusted = {
            "text": "你已发货，查看发货凭证",
            "item_id": "42",
            "timestamp_ms": 1762300000300,
            "raw": {},
        }
        assert await service.observe(account, chat, untrusted) == ""
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "paid"

        trusted = {
            "text": "[你已发货]",
            "item_id": "42",
            "timestamp_ms": 1762300000400,
            "raw": {},
        }
        assert await service.observe(account, chat, trusted) == "shipped"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "delivered"
    finally:
        await db.close()


async def test_reconcile_skips_untrusted_payment_text(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        messages = MessageRepo(db)
        spoofed = {
            "dedup_key": "m-spoofed",
            "msg_id": "m-spoofed",
            "chat_id": "buyer1",
            "sender_id": "buyer1",
            "kind": "text",
            "text": "我已付款，等待你发货",
            "item_id": "42",
            "timestamp_ms": 1762300000500,
            "raw": {},
        }
        await messages.insert_inbound(account.id, spoofed)
        notice = {
            "dedup_key": "m-notice",
            "msg_id": "m-notice",
            "chat_id": "buyer2",
            "sender_id": "buyer2",
            "kind": "text",
            "text": "[我已付款，等待你发货]",
            "item_id": "42",
            "timestamp_ms": 1762300000600,
            "raw": {},
        }
        await messages.insert_inbound(account.id, notice)

        created = await service.reconcile(account.id)

        assert created == 1
        assert await service.orders.get_by_no(account.id, "buyer1-1762300000500") is None
        trusted = await service.orders.get_by_no(account.id, "buyer2-1762300000600")
        assert trusted is not None and trusted.status == "paid"
    finally:
        await db.close()


async def test_execute_claims_secret_and_marks_delivered(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        card = await _card_with_secrets(db, account.id, 2)
        order = await service.orders.create_if_absent(
            account.id, order_no=f"buyer1-{1762300000000}", chat_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.outbox.enqueue(
            account.id,
            dedup_key=f"deliver:{order.order_no}",
            task="delivery",
            payload={"order_id": order.id},
        )
        task = await service.outbox.claim_due(account.id)
        assert task is not None
        sender = StubSender()

        outcome = await service.execute(task, sender)

        assert outcome.ok and outcome.status == "delivered"
        assert sender.calls and sender.calls[0][0] == "buyer1"
        assert "CODE-" in sender.calls[0][2]
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "delivered" and stored.delivered_at
        secrets = await CardRepo(db).list_secrets(card.id)
        assert [s.status for s in secrets].count("used") == 1
        messages = await MessageRepo(db).list_by_chat(account.id, "buyer1")
        assert any(m.direction == "out" and "CODE-" in m.text for m in messages)

        again = await service.execute(task, sender)
        assert again.status == "delivered"
        assert len(sender.calls) == 1
    finally:
        await db.close()


async def test_execute_retry_releases_secret(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        card = await _card_with_secrets(db, account.id, 1)
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000001", chat_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.outbox.enqueue(
            account.id,
            dedup_key=f"deliver:{order.order_no}",
            task="delivery",
            payload={"order_id": order.id},
        )
        task = await service.outbox.claim_due(account.id)
        assert task is not None

        with pytest.raises(DeliveryRetry):
            await service.execute(task, StubSender(ok=False, reason="offline"))

        secrets = await CardRepo(db).list_secrets(card.id)
        assert secrets[0].status == "available"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "paid"

        await service.outbox.retry(task.id, delay_seconds=0, error="offline")
        again = await service.outbox.claim_due(account.id)
        assert again is not None
        outcome = await service.execute(again, StubSender())
        assert outcome.status == "delivered"
        assert (await CardRepo(db).list_secrets(card.id))[0].status == "used"
    finally:
        await db.close()


async def test_claim_is_atomic_and_never_over_issues(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        card = await _card_with_secrets(db, account.id, 3)
        results = await asyncio.gather(
            *(service.secrets.claim(card.id, f"order-{index}") for index in range(6))
        )
        claimed = [secret for secret in results if secret is not None]
        assert len(claimed) == 3
        assert len({secret.id for secret in claimed}) == 3
        stats = await CardRepo(db).get(card.id)
        assert stats is not None and stats.available == 0 and stats.total == 3
    finally:
        await db.close()


async def test_execute_marks_manual_without_stock(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        await _card_with_secrets(db, account.id, 0)
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000002", chat_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.outbox.enqueue(
            account.id,
            dedup_key=f"deliver:{order.order_no}",
            task="delivery",
            payload={"order_id": order.id},
        )
        task = await service.outbox.claim_due(account.id)
        assert task is not None

        with pytest.raises(Exception):
            await service.execute(task, StubSender())

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
    finally:
        await db.close()


async def test_reset_requeues_interrupted_delivery(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        card = await _card_with_secrets(db, account.id, 1)
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000004", chat_id="buyer1", item_id="42"
        )
        assert order is not None
        secret = await service.secrets.claim(card.id, order.order_no)
        assert secret is not None
        await service.orders.set_status(order.id, "delivering")
        await service.outbox.enqueue(
            account.id,
            dedup_key=f"deliver:{account.id}:{order.order_no}",
            task="delivery",
            payload={"order_id": order.id},
        )
        task = await service.outbox.claim_due(account.id)
        assert task is not None

        await service.reset()

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "paid"
        secrets = await CardRepo(db).list_secrets(card.id)
        assert secrets[0].status == "available"
        again = await service.outbox.claim_due(account.id)
        assert again is not None and again.id == task.id
    finally:
        await db.close()


async def test_template_override_used_for_item(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        await _card_with_secrets(db, account.id, 1)
        await DeliveryRuleRepo(db).create(
            account.id, code="book", name="电子书", template="提取码 {secret}", item_id="42"
        )
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000003", chat_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.outbox.enqueue(
            account.id,
            dedup_key=f"deliver:{order.order_no}",
            task="delivery",
            payload={"order_id": order.id},
        )
        task = await service.outbox.claim_due(account.id)
        assert task is not None
        sender = StubSender()
        await service.execute(task, sender)
        assert sender.calls[0][2] == "提取码 CODE-0"
    finally:
        await db.close()


class StubAI:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, account_id: int, chat_id: str, text: str, item_id: str = ""):  # type: ignore[no-untyped-def]
        self.calls.append(text)
        raise AssertionError("系统通知不应调用 AI")


async def test_pipeline_routes_system_notice_to_delivery(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        ai = StubAI()
        pipeline = ReplyPipeline(db, ai, service)  # type: ignore[arg-type]
        parsed = {
            "msg_id": "m-order-1",
            "dedup_key": "m-order-1",
            "chat_id": "buyer1",
            "sender_id": "buyer1",
            "sender_name": "买家",
            "text": "我已付款，等待你发货",
            "kind": "text",
            "item_id": "42",
            "timestamp_ms": 1762300000000,
            "is_self": False,
            "raw": {"orderId": "2026110500012345"},
        }

        outcome = await pipeline.handle_inbound(account, parsed, StubSender())

        assert outcome == "system_notice"
        assert ai.calls == []
        orders = await service.orders.list(account.id)
        assert len(orders) == 1 and orders[0].order_no == "2026110500012345"
    finally:
        await db.close()


async def test_reconcile_backfills_and_flags_stale(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        messages = MessageRepo(db)
        await messages.insert_inbound(
            account.id,
            {
                "dedup_key": "m-fresh",
                "msg_id": "m-fresh",
                "chat_id": "buyer1",
                "sender_id": "buyer1",
                "kind": "text",
                "text": "我已付款，等待你发货",
                "item_id": "42",
                "timestamp_ms": 1762300000000,
                "raw": {"orderId": "2026110500011111"},
            },
        )
        await messages.insert_inbound(
            account.id,
            {
                "dedup_key": "m-old",
                "msg_id": "m-old",
                "chat_id": "buyer2",
                "sender_id": "buyer2",
                "kind": "text",
                "text": "我已付款，等待你发货",
                "item_id": "42",
                "timestamp_ms": 1762100000000,
                "raw": {"orderId": "2026110300022222"},
            },
        )
        await db.write(
            "UPDATE message SET created_at = '2026-01-01T00:00:00Z' WHERE dedup_key = 'm-old'"
        )

        created = await service.reconcile(account.id)

        assert created == 2
        fresh = await service.orders.get_by_no(account.id, "2026110500011111")
        stale = await service.orders.get_by_no(account.id, "2026110300022222")
        assert fresh is not None and fresh.status == "paid"
        assert stale is not None and stale.status == "manual"
        tasks = await service.outbox.list_recent(account.id)
        assert [task["dedup_key"] for task in tasks] == [f"deliver:{account.id}:2026110500011111"]
    finally:
        await db.close()


async def _order_task(
    service: DeliveryService,
    account_id: int,
    order_no: str,
    *,
    item_id: str = "42",
    chat_id: str = "buyer1",
    title: str = "",
):  # type: ignore[no-untyped-def]
    order = await service.orders.create_if_absent(
        account_id, order_no=order_no, chat_id=chat_id, item_id=item_id, title=title
    )
    assert order is not None
    await service.outbox.enqueue(
        account_id,
        dedup_key=f"deliver:{account_id}:{order_no}",
        task="delivery",
        payload={"order_id": order.id},
    )
    task = await service.outbox.claim_due(account_id)
    assert task is not None
    return order, task


async def test_item_fixed_mode_sends_content_without_stock(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        await ItemRepo(db).ensure(account.id, "42", title="电子书", price_cents=10)
        await ItemDeliveryRepo(db).upsert(
            account.id,
            "42",
            enabled=True,
            mode="fixed",
            content="下载链接：https://example.com",
            auto_consign=False,
        )
        order, task = await _order_task(
            service, account.id, "buyer1-1762300000010", title="电子书"
        )
        sender = StubSender()

        outcome = await service.execute(task, sender)

        assert outcome.status == "delivered"
        assert sender.calls[0][2] == "下载链接：https://example.com"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "delivered"
        assert stored.card_secret_id is None
    finally:
        await db.close()


async def test_item_card_mode_uses_selected_pool(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        card_a = await cards.create(account.id, name="A")
        await cards.add_secrets(card_a.id, ["A-1"])
        card_b = await cards.create(account.id, name="B")
        await cards.add_secrets(card_b.id, ["B-1"])
        await ItemDeliveryRepo(db).upsert(
            account.id, "42", enabled=True, mode="card", card_id=card_b.id, template="卡密 {secret}"
        )
        _, task = await _order_task(service, account.id, "buyer1-1762300000011")
        sender = StubSender()

        await service.execute(task, sender)

        assert sender.calls[0][2] == "卡密 B-1"
        assert [s.status for s in await cards.list_secrets(card_a.id)] == ["available"]
        assert [s.status for s in await cards.list_secrets(card_b.id)] == ["used"]
    finally:
        await db.close()


async def test_item_config_defaults_to_disabled(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        config = await ItemDeliveryRepo(db).upsert(
            account.id, "42", mode="fixed", content="下载链接：https://example.com"
        )
        assert config.enabled is False

        order, task = await _order_task(service, account.id, "buyer1-1762300000016")
        sender = StubSender()
        with pytest.raises(DeliveryPermanent):
            await service.execute(task, sender)

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert stored.detail == "商品已关闭自动发货"
        assert sender.calls == []
    finally:
        await db.close()


async def test_legacy_binding_without_rule_stays_manual(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        card = await cards.create(account.id, name="电子书")
        await cards.add_secrets(card.id, ["CODE-1"])
        await cards.bind_item(account.id, card.id, "42")

        order, task = await _order_task(service, account.id, "buyer1-1762300000017")
        sender = StubSender()
        with pytest.raises(DeliveryPermanent):
            await service.execute(task, sender)

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert stored.detail == "商品未配置自动发货"
        assert sender.calls == []
        assert [s.status for s in await cards.list_secrets(card.id)] == ["available"]
    finally:
        await db.close()


async def test_item_manual_and_disabled_modes_never_send(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        card = await _card_with_secrets(db, account.id, 1)
        configs = ItemDeliveryRepo(db)
        await configs.upsert(account.id, "42", mode="manual")

        order, task = await _order_task(service, account.id, "buyer1-1762300000012")
        sender = StubSender()
        with pytest.raises(DeliveryPermanent):
            await service.execute(task, sender)
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert sender.calls == []

        await configs.upsert(account.id, "42", mode="card", card_id=card.id, enabled=False)
        order2, task2 = await _order_task(service, account.id, "buyer1-1762300000013")
        with pytest.raises(DeliveryPermanent):
            await service.execute(task2, sender)
        stored2 = await service.orders.get_by_id(order2.id)
        assert stored2 is not None and stored2.status == "manual"
        assert sender.calls == []
        assert [s.status for s in await CardRepo(db).list_secrets(card.id)] == ["available"]
    finally:
        await db.close()


async def test_item_card_mode_without_pool_marks_manual(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        await ItemDeliveryRepo(db).upsert(account.id, "42", enabled=True, mode="card")
        order, task = await _order_task(service, account.id, "buyer1-1762300000014")

        with pytest.raises(DeliveryPermanent):
            await service.execute(task, StubSender())

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert stored.detail == "商品未选择卡密池"
    finally:
        await db.close()


async def test_item_config_overrides_legacy_binding(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        legacy = await _card_with_secrets(db, account.id, 1)
        await DeliveryRuleRepo(db).create(
            account.id, code="legacy", name="旧规则", template="旧 {secret}", item_id="42"
        )
        chosen = await CardRepo(db).create(account.id, name="新卡池")
        await CardRepo(db).add_secrets(chosen.id, ["NEW-1"])
        await ItemDeliveryRepo(db).upsert(
            account.id, "42", enabled=True, mode="card", card_id=chosen.id, template="新 {secret}"
        )
        _, task = await _order_task(service, account.id, "buyer1-1762300000015")
        sender = StubSender()

        await service.execute(task, sender)

        assert sender.calls[0][2] == "新 NEW-1"
        assert [s.status for s in await CardRepo(db).list_secrets(legacy.id)] == ["available"]
    finally:
        await db.close()

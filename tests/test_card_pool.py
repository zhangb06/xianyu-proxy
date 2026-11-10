from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.delivery import CardRepo, CardSecretRepo, DeliveryRuleRepo, mask_secret
from app.db.repo.items import ItemDeliveryRepo
from app.delivery.service import DeliveryPermanent, DeliveryService
from app.pipeline.engine import SendResult


class StubSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        self.calls.append((chat_id, to_user_id, text))
        return SendResult(ok=True, msg_id="mid-card-pool")


async def _setup(tmp_path: Path) -> tuple[Database, Any, DeliveryService]:
    db = Database(tmp_path / "cards.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("20001")
    return db, account, DeliveryService(db, accounts)


async def _order_task(
    service: DeliveryService,
    account_id: int,
    order_no: str,
    *,
    title: str = "",
    buyer_id: str = "buyer1",
    amount_cents: int = 0,
) -> Any:
    order = await service.orders.create_if_absent(
        account_id,
        order_no=order_no,
        chat_id="buyer1",
        buyer_id=buyer_id,
        item_id="42",
        title=title,
        amount_cents=amount_cents,
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


async def _pool(cards: CardRepo, account_id: int, name: str, secrets: list[str]) -> Any:
    card = await cards.create(account_id, name=name)
    await cards.add_secrets(card.id, secrets)
    return card


def test_mask_secret_boundaries() -> None:
    assert mask_secret("AB") == "AB***"
    assert mask_secret("ABCDEFGH") == "AB***"
    assert mask_secret("ABCDEFGHI") == "ABCD***FGHI"


async def test_import_dedupes_across_batches_and_tracks_stock(tmp_path: Path) -> None:
    db, account, _ = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        card = await cards.create(account.id, name="电子书", note="网盘链接")

        assert await cards.add_secrets(card.id, ["A", "B", "A"]) == 2
        assert await cards.add_secrets(card.id, ["B", "C"]) == 1

        stored = await cards.get(card.id)
        assert stored is not None
        assert (stored.available, stored.used, stored.total) == (3, 0, 3)
        assert [secret.secret for secret in await cards.list_secrets(card.id)] == ["C", "B", "A"]
    finally:
        await db.close()


async def test_claim_release_and_used_reflected_in_stock(tmp_path: Path) -> None:
    db, account, _ = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        secrets = CardSecretRepo(db)
        card = await _pool(cards, account.id, "电子书", ["A", "B"])

        first = await secrets.claim(card.id, "order-1")
        assert first is not None and first.secret == "A"
        stored = await cards.get(card.id)
        assert stored is not None and stored.available == 1

        await secrets.release(first.id)
        stored = await cards.get(card.id)
        assert stored is not None and stored.available == 2

        again = await secrets.claim(card.id, "order-2")
        assert again is not None
        await secrets.mark_used(again.id)
        stored = await cards.get(card.id)
        assert stored is not None
        assert (stored.available, stored.used) == (1, 1)
    finally:
        await db.close()


async def test_deleting_pool_cascades_and_clears_item_config(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        card = await _pool(cards, account.id, "电子书", ["A"])
        await cards.bind_item(account.id, card.id, "42")
        await ItemDeliveryRepo(db).upsert(
            account.id,
            "42",
            enabled=True,
            mode="card",
            card_id=card.id,
            template="卡密 {secret}",
        )

        await cards.delete(card.id)

        assert await cards.get(card.id) is None
        assert await cards.items_of(card.id) == []
        row = await db.fetch_one("SELECT COUNT(*) AS total FROM card_secret")
        assert row is not None and int(row["total"]) == 0
        config = await ItemDeliveryRepo(db).get(account.id, "42")
        assert config is not None
        assert config.card_id is None
        assert config.enabled is True

        order, task = await _order_task(service, account.id, "buyer1-1762400000001")
        sender = StubSender()
        with pytest.raises(DeliveryPermanent):
            await service.execute(task, sender)
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert stored.detail == "商品未选择卡密池"
        assert sender.calls == []
    finally:
        await db.close()


async def test_card_for_item_prefers_enabled_pool_with_most_stock(tmp_path: Path) -> None:
    db, account, _ = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        small = await _pool(cards, account.id, "小池", ["A"])
        large = await _pool(cards, account.id, "大池", ["B", "C", "D"])
        await cards.bind_item(account.id, small.id, "42")
        await cards.bind_item(account.id, large.id, "42")

        assert await cards.card_for_item(account.id, "42") == large.id

        await cards.update(large.id, enabled=False)
        assert await cards.card_for_item(account.id, "42") == small.id

        await cards.update(small.id, enabled=False)
        assert await cards.card_for_item(account.id, "42") is None
    finally:
        await db.close()


async def test_item_card_mode_with_disabled_pool_stays_manual(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        cards = CardRepo(db)
        card = await _pool(cards, account.id, "暂停的池", ["A"])
        await cards.update(card.id, enabled=False)
        await ItemDeliveryRepo(db).upsert(
            account.id,
            "42",
            enabled=True,
            mode="card",
            card_id=card.id,
            template="卡密 {secret}",
        )

        order, task = await _order_task(service, account.id, "buyer1-1762400000002")
        sender = StubSender()
        with pytest.raises(DeliveryPermanent):
            await service.execute(task, sender)

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert stored.detail == "卡密池已停用"
        assert sender.calls == []
        assert [s.status for s in await cards.list_secrets(card.id)] == ["available"]
    finally:
        await db.close()


async def test_fixed_mode_blank_content_marks_manual(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        await ItemDeliveryRepo(db).upsert(
            account.id, "42", enabled=True, mode="fixed", content="   "
        )
        order, task = await _order_task(service, account.id, "buyer1-1762400000004")
        sender = StubSender()
        with pytest.raises(DeliveryPermanent):
            await service.execute(task, sender)

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "manual"
        assert stored.detail == "固定发货内容为空"
        assert sender.calls == []
    finally:
        await db.close()


async def test_template_renders_order_variables(tmp_path: Path) -> None:
    db, account, service = await _setup(tmp_path)
    try:
        await ItemDeliveryRepo(db).upsert(
            account.id,
            "42",
            enabled=True,
            mode="fixed",
            content="{item_title}|{order_no}|{buyer}|{chat_id}|{price}",
            auto_consign=False,
        )
        _, task = await _order_task(
            service,
            account.id,
            "buyer1-1762400000005",
            title="电子书",
            buyer_id="buyer9",
            amount_cents=1234,
        )
        sender = StubSender()

        await service.execute(task, sender)

        assert sender.calls[0][2] == "电子书|buyer1-1762400000005|buyer9|buyer1|¥12.34"
    finally:
        await db.close()


async def test_rule_lookup_prefers_item_specific_over_priority(tmp_path: Path) -> None:
    db, account, _ = await _setup(tmp_path)
    try:
        rules = DeliveryRuleRepo(db)
        await rules.create(
            account.id, code="global", name="全局", template="全局 {secret}", priority=1
        )
        specific = await rules.create(
            account.id,
            code="book",
            name="电子书",
            template="专属 {secret}",
            item_id="42",
            priority=999,
        )

        found = await rules.template_for(account.id, "42")
        assert found is not None and found.template == "专属 {secret}"

        await rules.update(specific.id, enabled=False)
        found = await rules.template_for(account.id, "42")
        assert found is not None and found.template == "全局 {secret}"
    finally:
        await db.close()

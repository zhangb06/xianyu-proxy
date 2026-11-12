from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.timeutil import beijing_date, beijing_today, utc_now_iso
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.chats import ChatRepo
from app.db.repo.delivery import OutboxTask, iso_after
from app.db.repo.items import ItemDeliveryRepo, ItemRepo
from app.db.repo.ops import AccountOpsRepo
from app.db.repo.rules import BlockRepo
from app.delivery.polish import (
    AUTO_POLISH_WINDOW,
    PolishError,
    PolishService,
    auto_polish_target,
)
from app.delivery.service import (
    FLOWER_DELAY_SECONDS,
    FLOWER_TEXT,
    MAX_ATTEMPTS,
    DeliveryRetry,
    DeliveryService,
    flower_key,
)
from app.delivery.worker import DeliveryWorker
from app.pipeline.engine import SendResult
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.polish import PolishResult, parse_polish_response


class StubPolisher:
    def __init__(
        self,
        result: PolishResult | None = None,
        *,
        error: Exception | None = None,
        on_call: Any = None,
    ) -> None:
        self.result = result or PolishResult(ok=True, message="擦亮成功")
        self.error = error
        self.on_call = on_call
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def __call__(self, jar: CookieJar, item_id: str, *, proxy: str = "") -> PolishResult:
        self.calls.append((item_id, proxy))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.on_call is not None:
                self.on_call(jar)
            if self.error is not None:
                raise self.error
            return self.result
        finally:
            self.active -= 1


class StubSender:
    _seq = 0

    def __init__(self, *, ok: bool = True, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.calls: list[tuple[str, str, str]] = []

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        self.calls.append((chat_id, to_user_id, text))
        if self.ok:
            StubSender._seq += 1
            return SendResult(ok=True, msg_id=f"mid-flower-{StubSender._seq}")
        return SendResult(ok=False, reason=self.reason)


async def _setup(tmp_path: Path) -> tuple[Database, Account, AccountRepo]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001")
    await accounts.set_enabled(account.id, True)
    await accounts.save_cookie(account.id, {"unb": "10001", "cookie2": "abc"})
    return db, account, accounts


async def _polishable_item(db: Database, account_id: int, item_id: str = "42") -> None:
    await ItemRepo(db).ensure(account_id, item_id, title="电子书", price_cents=100, status="在售")


def test_parse_polish_response_markers() -> None:
    assert parse_polish_response({"ret": ["SUCCESS::调用成功"]}).ok is True
    already = parse_polish_response({"ret": ["FAIL_BIZ_IDLEITEM_POLISH_AGAIN::已擦亮"]})
    assert already.ok is True and already.already is True
    assert parse_polish_response({"ret": ["宝贝已经擦亮过了"]}).already is True
    assert parse_polish_response({"ret": ["一天只能擦亮一次"]}).already is True
    offline = parse_polish_response(
        {"ret": ["FAIL_BIZ_UNSUPPORTED_ITEM_STATUS::已下架商品不支持该操作"]}
    )
    assert offline.ok is False and offline.offline is True
    failed = parse_polish_response({"ret": ["FAIL_BIZ_SYSTEM_ERROR::系统繁忙"]})
    assert failed.ok is False and failed.offline is False
    assert "系统繁忙" in failed.message


def test_beijing_calendar_day_boundary() -> None:
    assert beijing_date("2026-11-08T15:59:59Z") == "2026-11-08"
    assert beijing_date("2026-11-08T16:00:00Z") == "2026-11-09"
    assert beijing_date("2026-11-08T16:00:00+00:00") == "2026-11-09"
    assert beijing_date("") == ""
    assert beijing_date("not-a-date") == ""


async def test_polish_requires_item_login_and_on_sale(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        stub = StubPolisher()
        service = PolishService(db, accounts, polisher=stub)
        with pytest.raises(PolishError, match="商品不存在"):
            await service.polish(account, "missing")

        await ItemRepo(db).ensure(account.id, "sold", status="已售出")
        with pytest.raises(PolishError, match="在售"):
            await service.polish(account, "sold")

        await _polishable_item(db, account.id, "42")
        await accounts.save_cookie(account.id, {})
        with pytest.raises(PolishError, match="Cookie"):
            await service.polish(account, "42")
        assert stub.calls == []
    finally:
        await db.close()


async def test_polish_success_sets_time_and_writes_back_cookie(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id)
        stub = StubPolisher(on_call=lambda jar: jar.update({"refreshed": "1"}))
        service = PolishService(db, accounts, polisher=stub)

        outcome = await service.polish(account, "42")

        assert outcome.ok and not outcome.already
        item = await ItemRepo(db).get(account.id, "42")
        assert item is not None and item.polished_at and item.polish_error == ""
        cookie = await accounts.load_cookie(account.id)
        assert cookie is not None and cookie["refreshed"] == "1"
        assert stub.calls == [("42", account.proxy_url)]
    finally:
        await db.close()


async def test_polish_short_circuits_when_already_done_today(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id)
        await ItemRepo(db).set_polish(account.id, "42", polished_at=utc_now_iso())
        stub = StubPolisher()
        service = PolishService(db, accounts, polisher=stub)

        outcome = await service.polish(account, "42")

        assert outcome.ok and outcome.already and outcome.message == "今天已擦亮"
        assert stub.calls == []
    finally:
        await db.close()


async def test_polish_allows_next_beijing_day(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id)
        await ItemRepo(db).set_polish(account.id, "42", polished_at="2026-01-01T00:00:00Z")
        stub = StubPolisher()
        service = PolishService(db, accounts, polisher=stub)

        outcome = await service.polish(account, "42")

        assert outcome.ok and not outcome.already
        assert stub.calls == [("42", "")]
        item = await ItemRepo(db).get(account.id, "42")
        assert item is not None and beijing_date(item.polished_at) == beijing_today()
    finally:
        await db.close()


async def test_polish_failure_keeps_previous_time_and_records_error(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id)
        previous = "2026-01-01T00:00:00Z"
        await ItemRepo(db).set_polish(account.id, "42", polished_at=previous)
        stub = StubPolisher(PolishResult(ok=False, message="FAIL_BIZ_SYSTEM_ERROR::系统繁忙"))
        service = PolishService(db, accounts, polisher=stub)

        outcome = await service.polish(account, "42")

        assert not outcome.ok and outcome.offline is False
        item = await ItemRepo(db).get(account.id, "42")
        assert item is not None and item.polished_at == previous
        assert "系统繁忙" in item.polish_error
    finally:
        await db.close()


async def test_polish_auth_error_is_flagged(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id)
        stub = StubPolisher(error=MtopError("FAIL_SYS_TOKEN_EXPIRED::令牌过期"))
        service = PolishService(db, accounts, polisher=stub)

        with pytest.raises(PolishError) as info:
            await service.polish(account, "42")

        assert info.value.auth is True
        item = await ItemRepo(db).get(account.id, "42")
        assert item is not None and "FAIL_SYS_TOKEN_EXPIRED" in item.polish_error
    finally:
        await db.close()


async def test_polish_session_expired_response_is_treated_as_auth(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id)
        stub = StubPolisher(
            result=PolishResult(ok=False, message="FAIL_SYS_SESSION_EXPIRED::Session过期")
        )
        service = PolishService(db, accounts, polisher=stub)

        with pytest.raises(PolishError) as info:
            await service.polish(account, "42")

        assert info.value.auth is True
        item = await ItemRepo(db).get(account.id, "42")
        assert item is not None and "FAIL_SYS_SESSION_EXPIRED" in item.polish_error
        assert item.polished_at == ""
    finally:
        await db.close()


async def test_polish_uses_shared_per_account_lock(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id, "1")
        await _polishable_item(db, account.id, "2")
        stub = StubPolisher()
        service = PolishService(db, accounts, polisher=stub)

        await asyncio.gather(service.polish(account, "1"), service.polish(account, "2"))

        assert stub.max_active == 1
        assert sorted(item_id for item_id, _ in stub.calls) == ["1", "2"]
    finally:
        await db.close()


def test_auto_polish_target_stable_within_window() -> None:
    first = auto_polish_target(1, "2026-11-10")
    assert first == auto_polish_target(1, "2026-11-10")
    assert first.tzinfo is not None
    assert AUTO_POLISH_WINDOW[0] <= first.hour < AUTO_POLISH_WINDOW[1]
    assert first != auto_polish_target(2, "2026-11-10")


async def test_run_auto_waits_for_daily_random_target_then_runs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.delivery.polish.POLISH_SPACING_RANGE", (0.0, 0.0))
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id, "1")
        await _polishable_item(db, account.id, "2")
        await ItemRepo(db).set_polish(account.id, "2", polished_at=utc_now_iso())
        await ItemRepo(db).ensure(account.id, "3", status="已售出")
        stub = StubPolisher()
        service = PolishService(db, accounts, polisher=stub)
        today = beijing_today()
        target = auto_polish_target(account.id, today)

        off = await service.run_auto(now=target)
        assert off.accounts == 0 and stub.calls == []

        await AccountOpsRepo(db).set(account.id, auto_polish=True)
        early = await service.run_auto(now=target - timedelta(seconds=30))
        assert early.accounts == 0 and stub.calls == []

        result = await service.run_auto(now=target + timedelta(seconds=1))
        assert result.accounts == 1 and result.polished == 1
        assert [item_id for item_id, _ in stub.calls] == ["1"]

        again = await service.run_auto(now=target + timedelta(hours=2))
        assert again.accounts == 0 and len(stub.calls) == 1
        ops = await AccountOpsRepo(db).get(account.id)
        assert ops.polish_ran_date == today

        await ItemRepo(db).set_polish(account.id, "1", polished_at="")
        tomorrow_day = (target + timedelta(days=1)).date().isoformat()
        next_target = auto_polish_target(account.id, tomorrow_day)
        tomorrow = await service.run_auto(now=next_target + timedelta(seconds=1))
        assert tomorrow.accounts == 1
        assert len(stub.calls) == 2
    finally:
        await db.close()


async def test_run_auto_auth_error_keeps_same_day_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.delivery.polish.POLISH_SPACING_RANGE", (0.0, 0.0))
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id, "1")
        await _polishable_item(db, account.id, "2")
        await AccountOpsRepo(db).set(account.id, auto_polish=True)
        stub = StubPolisher(error=MtopError("FAIL_SYS_TOKEN_EXPIRED::令牌过期"))
        service = PolishService(db, accounts, polisher=stub)
        target = auto_polish_target(account.id, beijing_today()) + timedelta(seconds=1)

        first = await service.run_auto(now=target)

        assert first.accounts == 1 and first.failed == 0
        assert len(stub.calls) == 1
        ops = await AccountOpsRepo(db).get(account.id)
        assert ops.polish_ran_date == ""
        blocked = await service.run_auto(now=target)
        assert blocked.accounts == 0
        assert len(stub.calls) == 1
    finally:
        await db.close()



async def test_next_check_sleeps_until_next_window_after_daily_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.delivery.polish.POLISH_SPACING_RANGE", (0.0, 0.0))
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id, "1")
        await AccountOpsRepo(db).set(account.id, auto_polish=True)
        service = PolishService(db, accounts, polisher=StubPolisher())
        target = auto_polish_target(account.id, beijing_today())

        before = await service.next_check_seconds(now=target - timedelta(hours=1))
        assert 30 < before <= 3600

        await service.run_auto(now=target + timedelta(seconds=1))

        after = await service.next_check_seconds(now=target + timedelta(seconds=1))
        assert after > 8 * 3600
    finally:
        await db.close()


async def test_next_check_is_hourly_without_enabled_auto_polish(tmp_path: Path) -> None:
    db, _account, accounts = await _setup(tmp_path)
    try:
        service = PolishService(db, accounts, polisher=StubPolisher())
        assert await service.next_check_seconds() == 3600.0
    finally:
        await db.close()


async def test_reset_backoff_allows_retry_after_relogin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.delivery.polish.POLISH_SPACING_RANGE", (0.0, 0.0))
    db, account, accounts = await _setup(tmp_path)
    try:
        await _polishable_item(db, account.id, "1")
        await AccountOpsRepo(db).set(account.id, auto_polish=True)
        stub = StubPolisher(error=MtopError("FAIL_SYS_TOKEN_EXPIRED::令牌过期"))
        service = PolishService(db, accounts, polisher=stub)
        target = auto_polish_target(account.id, beijing_today()) + timedelta(seconds=1)

        await service.run_auto(now=target)
        assert len(stub.calls) == 1

        service.reset_backoff(account.id)
        await service.run_auto(now=target)
        assert len(stub.calls) == 2
    finally:
        await db.close()


async def _deliver_fixed(db: Database, account: Account, service: DeliveryService, order_no: str) -> Any:
    await ItemRepo(db).ensure(account.id, "42", title="电子书", status="在售")
    await ItemDeliveryRepo(db).upsert(
        account.id, "42", enabled=True, mode="fixed", content="下载链接", auto_consign=False
    )
    order = await service.orders.create_if_absent(
        account.id, order_no=order_no, chat_id="buyer1", buyer_id="buyer1", item_id="42"
    )
    assert order is not None
    await service.outbox.enqueue(
        account.id,
        dedup_key=f"deliver:{account.id}:{order_no}",
        task="delivery",
        payload={"order_id": order.id},
    )
    task = await service.outbox.claim_due(account.id)
    assert task is not None
    return order, task


async def test_flower_enqueued_after_successful_send_with_delay(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        order, task = await _deliver_fixed(db, account, service, "buyer1-1762300000100")
        sender = StubSender()

        await service.execute(task, sender)

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "delivered"
        assert stored.flower_status == "pending"
        tasks = await service.outbox.list_recent(account.id)
        flower = [row for row in tasks if row["task"] == "flower"]
        assert len(flower) == 1
        assert flower[0]["dedup_key"] == flower_key(account.id, "buyer1-1762300000100")
        queued = OutboxTask.from_row(flower[0])
        assert queued.payload == {"order_id": order.id}
    finally:
        await db.close()


async def test_flower_delay_is_thirty_minutes(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        order, task = await _deliver_fixed(db, account, service, "buyer1-1762300000101")
        await service.execute(task, StubSender())

        row = await db.fetch_one(
            "SELECT run_at, created_at FROM outbox WHERE task = 'flower' AND dedup_key = ?",
            (flower_key(account.id, "buyer1-1762300000101"),),
        )
        assert row is not None
        from datetime import datetime

        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        run_at = datetime.fromisoformat(str(row["run_at"]).replace("Z", "+00:00"))
        assert (run_at - created).total_seconds() == FLOWER_DELAY_SECONDS
    finally:
        await db.close()


async def test_flower_not_enqueued_when_disabled(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        order, task = await _deliver_fixed(db, account, service, "buyer1-1762300000102")
        await service.execute(task, StubSender())

        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_status == ""
        tasks = await service.outbox.list_recent(account.id)
        assert [row for row in tasks if row["task"] == "flower"] == []
    finally:
        await db.close()


async def test_flower_execution_rechecks_and_sends_once(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        order, task = await _deliver_fixed(db, account, service, "buyer1-1762300000103")
        await service.execute(task, StubSender())
        row = await db.fetch_one(
            "SELECT * FROM outbox WHERE task = 'flower' AND dedup_key = ?",
            (flower_key(account.id, "buyer1-1762300000103"),),
        )
        assert row is not None
        flower_task = OutboxTask.from_row(dict(row))
        sender = StubSender()

        outcome = await service.execute_flower(flower_task, sender)

        assert outcome.status == "sent"
        assert sender.calls == [("buyer1", "buyer1", FLOWER_TEXT)]
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_status == "sent" and stored.flower_at
        messages = await db.fetch_all(
            "SELECT status, dedup_key FROM message WHERE dedup_key = ?",
            (flower_key(account.id, "buyer1-1762300000103"),),
        )
        assert len(messages) == 1 and messages[0]["status"] == "sent"

        again = await service.execute_flower(flower_task, sender)
        assert again.status == "skipped"
        assert len(sender.calls) == 1
    finally:
        await db.close()


async def test_flower_skipped_when_ops_disabled_or_not_delivered(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000104", chat_id="buyer1", buyer_id="buyer1", item_id="42"
        )
        assert order is not None
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        task = OutboxTask(
            id=1,
            account_id=account.id,
            dedup_key="flower:manual",
            task="flower",
            payload={"order_id": order.id},
            attempts=1,
        )

        sender = StubSender()
        outcome = await service.execute_flower(task, sender)
        assert outcome.status == "skipped"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_status == "skipped"
        assert stored.flower_error == "订单未发货"

        await AccountOpsRepo(db).set(account.id, flower_enabled=False)
        await service.orders.set_status(order.id, "delivered", delivered=True)
        outcome = await service.execute_flower(task, sender)
        assert outcome.status == "skipped"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_error == "功能已关闭"
        assert sender.calls == []
    finally:
        await db.close()


async def test_flower_skipped_when_chat_paused_or_buyer_blocked(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        chat = await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        await ChatRepo(db).pause(chat.id, iso_after(3600))
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000105", chat_id="buyer1", buyer_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.orders.set_status(order.id, "delivered", delivered=True)
        task = OutboxTask(
            id=2,
            account_id=account.id,
            dedup_key="flower:paused",
            task="flower",
            payload={"order_id": order.id},
            attempts=1,
        )
        sender = StubSender()

        await service.execute_flower(task, sender)
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_error == "会话已暂停"

        await ChatRepo(db).unpause(chat.id)
        await BlockRepo(db).add(account.id, "user", "buyer1")
        await service.execute_flower(task, sender)
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_error == "买家已拉黑"
        assert sender.calls == []
    finally:
        await db.close()


async def test_flower_send_failure_raises_retry_then_succeeds(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000106", chat_id="buyer1", buyer_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.orders.set_status(order.id, "delivered", delivered=True)
        await service.orders.set_flower(order.id, "pending")
        task = OutboxTask(
            id=3,
            account_id=account.id,
            dedup_key=flower_key(account.id, "buyer1-1762300000106"),
            task="flower",
            payload={"order_id": order.id},
            attempts=1,
        )

        with pytest.raises(DeliveryRetry):
            await service.execute_flower(task, StubSender(ok=False, reason="offline"))
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_status == "pending"
        assert stored.status == "delivered"

        outcome = await service.execute_flower(task, StubSender())
        assert outcome.status == "sent"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.flower_status == "sent"
    finally:
        await db.close()


async def test_worker_flower_failure_does_not_touch_delivery_state(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    service = DeliveryService(db, accounts)
    try:
        await AccountOpsRepo(db).set(account.id, flower_enabled=True)
        await ChatRepo(db).ensure(account.id, "buyer1", peer_id="buyer1", item_id="42")
        order = await service.orders.create_if_absent(
            account.id, order_no="buyer1-1762300000107", chat_id="buyer1", buyer_id="buyer1", item_id="42"
        )
        assert order is not None
        await service.orders.set_status(order.id, "delivered", delivered=True)
        await service.orders.set_flower(order.id, "pending")
        await service.outbox.enqueue(
            account.id,
            dedup_key=flower_key(account.id, "buyer1-1762300000107"),
            task="flower",
            payload={"order_id": order.id},
        )
        row = await db.fetch_one(
            "SELECT * FROM outbox WHERE dedup_key = ?",
            (flower_key(account.id, "buyer1-1762300000107"),),
        )
        assert row is not None
        await db.write("UPDATE outbox SET attempts = ? WHERE id = ?", (MAX_ATTEMPTS, row["id"]))
        task = await service.outbox.claim_due(account.id)
        assert task is not None and task.task == "flower"

        worker = DeliveryWorker(account.id, service, lambda: StubSender(ok=False, reason="offline"))
        await worker._handle(task)

        outbox_row = await db.fetch_one("SELECT status FROM outbox WHERE id = ?", (task.id,))
        assert outbox_row is not None and outbox_row["status"] == "dead"
        stored = await service.orders.get_by_id(order.id)
        assert stored is not None and stored.status == "delivered"
        assert stored.flower_status == "failed"
        assert stored.card_secret_id is None
    finally:
        await db.close()

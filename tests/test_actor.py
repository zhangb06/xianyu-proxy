from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.timeutil import utc_now_ms
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.rules import KeywordRuleRepo
from app.pipeline.engine import ReplyPipeline
from app.runtime.account_actor import AccountActor, ActorDeps
from app.runtime.supervisor import Supervisor


class FakeXianyuWS:
    instances: list[FakeXianyuWS] = []
    fail_next_connects = 0

    def __init__(self, jar: Any, **kwargs: Any) -> None:
        self.myid = kwargs["myid"]
        self.kwargs = kwargs
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.sent: list[tuple[str, str, str]] = []
        self._connected = False
        FakeXianyuWS.instances.append(self)

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if FakeXianyuWS.fail_next_connects > 0:
            FakeXianyuWS.fail_next_connects -= 1
            raise RuntimeError("connect failed")
        self._connected = True

    async def send_text(self, chat_id: str, to_user_id: str, text: str) -> str:
        self.sent.append((chat_id, to_user_id, text))
        return "mid-1"

    async def wait_result(self, mid: str, timeout: float = 5.0) -> dict[str, Any] | None:
        return {"code": 200, "headers": {"mid": mid}, "body": {}}

    async def iter_messages(self):
        while True:
            message = await self.queue.get()
            if message is None:
                return
            yield message

    async def close(self) -> None:
        self._connected = False


def _parsed(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "msg_id": "m-1",
        "dedup_key": "m-1",
        "chat_id": "buyer1",
        "sender_id": "buyer1",
        "sender_name": "买家",
        "text": "在吗",
        "kind": "text",
        "item_id": "42",
        "timestamp_ms": utc_now_ms(),
        "is_self": False,
    }
    base.update(overrides)
    return base


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        for _ in range(500):
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition not met in time")

    await asyncio.wait_for(_poll(), timeout=timeout)


def _make_deps(db: Database, accounts: AccountRepo) -> ActorDeps:
    return ActorDeps(
        settings=Settings(),
        accounts=accounts,
        pipeline=ReplyPipeline(db),
        connect_semaphore=asyncio.Semaphore(10),
    )


async def _make_account(db: Database, tmp_path: Path, monkeypatch) -> Account:
    FakeXianyuWS.instances = []
    FakeXianyuWS.fail_next_connects = 0
    monkeypatch.setattr("app.runtime.account_actor.XianyuWS", FakeXianyuWS)
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001")
    await accounts.save_cookie(account.id, {"unb": "10001"})
    return account


async def test_actor_replies_through_pipeline(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        account = await _make_account(db, tmp_path, monkeypatch)
        await KeywordRuleRepo(db).create(account.id, keyword="在吗", reply="在的")

        actor = AccountActor(account, _make_deps(db, accounts))
        await actor.start()
        try:
            await _wait_for(lambda: bool(FakeXianyuWS.instances))
            fake = FakeXianyuWS.instances[-1]
            await _wait_for(lambda: fake.connected)
            await fake.queue.put(_parsed())

            await _wait_for(lambda: bool(fake.sent))
            assert fake.sent == [("buyer1", "buyer1", "在的")]

            loaded = await accounts.get(account.id)
            assert loaded is not None and loaded.status == "online"
        finally:
            await actor.stop()
    finally:
        await db.close()


async def test_actor_reconnects_after_connect_failure(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        account = await _make_account(db, tmp_path, monkeypatch)
        FakeXianyuWS.fail_next_connects = 1
        monkeypatch.setattr(AccountActor, "_backoff", staticmethod(lambda attempt: 0.0))

        actor = AccountActor(account, _make_deps(db, accounts))
        await actor.start()
        try:
            await _wait_for(lambda: len(FakeXianyuWS.instances) >= 2)
            await _wait_for(lambda: FakeXianyuWS.instances[-1].connected)
            loaded = await accounts.get(account.id)
            assert loaded is not None and loaded.status == "online"
        finally:
            await actor.stop()
    finally:
        await db.close()


async def test_supervisor_reload_restarts_finished_actor(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        account = await _make_account(db, tmp_path, monkeypatch)

        supervisor = Supervisor(db, Settings(), "secret")
        await supervisor.start()
        try:
            first = supervisor.actors[account.id]
            await first.stop()
            assert not first.running

            await supervisor.reload()

            second = supervisor.actors[account.id]
            assert second is not first
            assert second.running
            await _wait_for(lambda: any(fake.connected for fake in FakeXianyuWS.instances))
            loaded = await accounts.get(account.id)
            assert loaded is not None and loaded.status == "online"
        finally:
            await supervisor.stop()
    finally:
        await db.close()


async def test_supervisor_starts_enabled_accounts(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "test.db")
    await db.connect()
    try:
        accounts = AccountRepo(db, "secret")
        first = await _make_account(db, tmp_path, monkeypatch)
        second = await accounts.upsert("10002")
        await accounts.save_cookie(second.id, {"unb": "10002"})

        supervisor = Supervisor(db, Settings(), "secret")
        await supervisor.start()
        try:
            assert set(supervisor.actors) == {first.id, second.id}
            await _wait_for(
                lambda: all(fake.connected for fake in FakeXianyuWS.instances)
            )
        finally:
            await supervisor.stop()
        assert supervisor.actors == {}
    finally:
        await db.close()

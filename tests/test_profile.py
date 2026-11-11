from __future__ import annotations

from pathlib import Path

from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.chats import ChatRepo
from app.protocol.mtop import MtopError
from app.runtime.profile import fill_account_nickname


class StubFetcher:
    def __init__(self, nickname: str = "", *, error: Exception | None = None) -> None:
        self.nickname = nickname
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, jar, chat_id, *, proxy: str = "") -> str:
        self.calls.append(chat_id)
        if self.error is not None:
            raise self.error
        return self.nickname


async def _setup(tmp_path: Path, *, with_chat: bool = True, remark: str = ""):
    db = Database(tmp_path / "test.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001", remark=remark)
    await accounts.save_cookie(account.id, {"unb": "10001"})
    if with_chat:
        await ChatRepo(db).ensure(account.id, "c1", peer_id="b1")
    return db, account, accounts


async def test_fill_nickname_writes_empty_remark(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        stub = StubFetcher("18号当铺")
        nickname = await fill_account_nickname(db, accounts, account.id, fetch=stub)

        assert nickname == "18号当铺"
        assert stub.calls == ["c1"]
        stored = await accounts.get(account.id)
        assert stored is not None and stored.remark == "18号当铺"

        again = await fill_account_nickname(db, accounts, account.id, fetch=stub)
        assert again == ""
        assert stub.calls == ["c1"]
    finally:
        await db.close()


async def test_fill_nickname_keeps_user_remark(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path, remark="主号")
    try:
        stub = StubFetcher("18号当铺")
        assert await fill_account_nickname(db, accounts, account.id, fetch=stub) == ""
        assert stub.calls == []
    finally:
        await db.close()


async def test_fill_nickname_skips_without_chat_or_cookie(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path, with_chat=False)
    try:
        stub = StubFetcher("18号当铺")
        assert await fill_account_nickname(db, accounts, account.id, fetch=stub) == ""
        assert stub.calls == []

        await ChatRepo(db).ensure(account.id, "c1", peer_id="b1")
        await db.write("DELETE FROM account_credential WHERE account_id = ?", (account.id,))
        assert await fill_account_nickname(db, accounts, account.id, fetch=stub) == ""
        assert stub.calls == []
    finally:
        await db.close()


async def test_fill_nickname_swallows_fetch_errors(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        stub = StubFetcher(error=MtopError("FAIL_BIZ_BAD_REQUEST"))
        assert await fill_account_nickname(db, accounts, account.id, fetch=stub) == ""
        stored = await accounts.get(account.id)
        assert stored is not None and stored.remark == ""

        boom = StubFetcher(error=RuntimeError("网络异常"))
        assert await fill_account_nickname(db, accounts, account.id, fetch=boom) == ""
    finally:
        await db.close()
from __future__ import annotations

from pathlib import Path

from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.protocol.mtop import MtopError
from app.protocol.session import SessionState, parse_login_session
from app.runtime.session_keepalive import SESSION_EXPIRED_MESSAGE, SessionKeeper


class StubProbe:
    def __init__(
        self, state: SessionState, *, cookie: dict | None = None, error: Exception | None = None
    ) -> None:
        self.state = state
        self.cookie = cookie
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, jar, *, proxy: str = "") -> SessionState:
        self.calls.append("probe")
        if self.error is not None:
            raise self.error
        if self.cookie:
            jar.update(self.cookie)
        return self.state


async def _setup(tmp_path: Path, *, enabled: bool = True, cookie: bool = True):
    db = Database(tmp_path / "test.db")
    await db.connect()
    accounts = AccountRepo(db, "secret")
    account = await accounts.upsert("10001")
    if enabled:
        await accounts.set_enabled(account.id, True)
    else:
        await accounts.set_enabled(account.id, False)
    if cookie:
        await accounts.save_cookie(account.id, {"unb": "10001", "cookie2": "old"})
    return db, account, accounts


def test_parse_login_session_states() -> None:
    assert parse_login_session({"ret": ["SUCCESS::调用成功"]}).ok is True
    expired = parse_login_session({"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]})
    assert expired.expired is True and expired.ok is False
    failed = parse_login_session({"ret": ["FAIL_SYS_UNKNOWN::未知"]})
    assert failed.expired is False and failed.ok is False


async def test_session_ok_saves_refreshed_cookie(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        probe = StubProbe(SessionState(ok=True), cookie={"cookie2": "new"})
        keeper = SessionKeeper(db, accounts, probe=probe)

        result = await keeper.check_all()

        assert result.checked == 1 and result.expired == 0
        stored = await accounts.load_cookie(account.id)
        assert stored == {"unb": "10001", "cookie2": "new"}
        loaded = await accounts.get(account.id)
        assert loaded is not None and loaded.status == "new"
    finally:
        await db.close()


async def test_session_expired_marks_account(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        probe = StubProbe(SessionState(ok=False, expired=True, message="Session过期"))
        keeper = SessionKeeper(db, accounts, probe=probe)

        result = await keeper.check_all()

        assert result.expired == 1
        loaded = await accounts.get(account.id)
        assert loaded is not None and loaded.status == "error"
        assert loaded.last_error == SESSION_EXPIRED_MESSAGE
        row = await db.fetch_one(
            "SELECT type FROM event_log WHERE account_id = ? ORDER BY id DESC LIMIT 1",
            (account.id,),
        )
        assert row is not None and row["type"] == "login.session_expired"
    finally:
        await db.close()


async def test_session_recovers_after_relogin(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        await accounts.set_status(account.id, "error", last_error=SESSION_EXPIRED_MESSAGE)
        keeper = SessionKeeper(db, accounts, probe=StubProbe(SessionState(ok=True)))

        result = await keeper.check_all()

        assert result.recovered == 1
        loaded = await accounts.get(account.id)
        assert loaded is not None and loaded.status == "new" and loaded.last_error == ""
    finally:
        await db.close()


async def test_session_skips_missing_cookie_and_disabled(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path, cookie=False)
    try:
        probe = StubProbe(SessionState(ok=True))
        keeper = SessionKeeper(db, accounts, probe=probe)
        assert (await keeper.check_all()).checked == 0
        assert probe.calls == []
    finally:
        await db.close()

    (tmp_path / "second").mkdir()
    db, account, accounts = await _setup(tmp_path / "second")
    try:
        await accounts.set_enabled(account.id, False)
        probe = StubProbe(SessionState(ok=True))
        keeper = SessionKeeper(db, accounts, probe=probe)
        assert (await keeper.check_all()).checked == 0
        assert probe.calls == []
    finally:
        await db.close()


async def test_session_probe_error_is_treated_as_expired(tmp_path: Path) -> None:
    db, account, accounts = await _setup(tmp_path)
    try:
        probe = StubProbe(
            SessionState(ok=True), error=MtopError("FAIL_SYS_SESSION_EXPIRED::Session过期")
        )
        keeper = SessionKeeper(db, accounts, probe=probe)

        result = await keeper.check_all()

        assert result.expired == 1
        loaded = await accounts.get(account.id)
        assert loaded is not None and loaded.status == "error"
    finally:
        await db.close()
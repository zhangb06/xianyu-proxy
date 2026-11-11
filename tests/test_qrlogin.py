from __future__ import annotations

from pathlib import Path

import pytest

from app.core.secrets import resolve_secret
from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.protocol.mtop import CookieJar
from app.protocol.qrlogin import QRSession
from app.runtime import qrlogin_manager as module
from app.runtime.qrlogin_manager import QRLoginManager


class _FakeQRLogin:
    def __init__(self, unb: str) -> None:
        self.unb = unb

    def client(self, timeout: float = 30.0) -> _FakeQRLogin:
        return self

    async def generate(self) -> QRSession:
        return QRSession(
            session_id="s1",
            jar=CookieJar({"unb": self.unb, "cookie2": "token"}),
            png=b"",
        )

    async def poll_once(self, session: QRSession) -> None:
        session.status = "success"
        session.unb = self.unb


async def _manager(tmp_path: Path, unb: str) -> tuple[QRLoginManager, AccountRepo, Database]:
    db = Database(tmp_path / "test.db")
    await db.connect()
    secret = await resolve_secret(db, "")
    accounts = AccountRepo(db, secret)
    return QRLoginManager(accounts), accounts, db


@pytest.fixture
def fake_qr(monkeypatch: pytest.MonkeyPatch):
    def install(unb: str) -> None:
        fake = _FakeQRLogin(unb)
        monkeypatch.setattr(module, "QRLogin", fake.client)

    return install


async def test_scan_without_target_creates_account(tmp_path: Path, fake_qr) -> None:
    manager, accounts, db = await _manager(tmp_path, "10001")
    fake_qr("10001")
    try:
        started = await manager.start()
        result = await manager.status(started["session_id"])
        assert result["state"] == "success"
        assert result["account"]["xianyu_id"] == "10001"
        rows = await accounts.list_all()
        assert [row.xianyu_id for row in rows] == ["10001"]
        assert await accounts.load_cookie(rows[0].id) == {"unb": "10001", "cookie2": "token"}
    finally:
        await db.close()


async def test_rescan_same_account_reuses_row_and_refreshes_cookie(tmp_path: Path, fake_qr) -> None:
    manager, accounts, db = await _manager(tmp_path, "10001")
    try:
        account = await accounts.upsert("10001", remark="主号")
        await accounts.save_cookie(account.id, {"cookie2": "old"})
        await accounts.set_status(account.id, "offline", last_error="令牌过期")

        fake_qr("10001")
        started = await manager.start(account_id=account.id)
        result = await manager.status(started["session_id"])

        assert result["state"] == "success"
        assert result["account"]["id"] == account.id
        rows = await accounts.list_all()
        assert len(rows) == 1
        assert rows[0].remark == "主号"
        assert rows[0].status == "new"
        assert await accounts.load_cookie(account.id) == {"unb": "10001", "cookie2": "token"}
    finally:
        await db.close()


async def test_rescan_other_account_is_rejected(tmp_path: Path, fake_qr) -> None:
    manager, accounts, db = await _manager(tmp_path, "20002")
    try:
        account = await accounts.upsert("10001")
        await accounts.save_cookie(account.id, {"cookie2": "old"})

        fake_qr("20002")
        started = await manager.start(account_id=account.id)
        result = await manager.status(started["session_id"])

        assert result["state"] == "error"
        assert "20002" in result["message"]
        assert "10001" in result["message"]
        assert "请用同一账号扫码" in result["message"]
        assert await accounts.load_cookie(account.id) == {"cookie2": "old"}
        assert [row.xianyu_id for row in await accounts.list_all()] == ["10001"]
    finally:
        await db.close()


async def test_rescan_deleted_account_reports_error(tmp_path: Path, fake_qr) -> None:
    manager, accounts, db = await _manager(tmp_path, "10001")
    fake_qr("10001")
    try:
        started = await manager.start(account_id=999)
        result = await manager.status(started["session_id"])
        assert result == {"state": "error", "message": "要重新登录的账号已不存在"}
        assert await accounts.list_all() == []
    finally:
        await db.close()
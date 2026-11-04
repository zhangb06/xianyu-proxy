from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.passwords import verify_password
from app.db.repo.chats import ChatRepo
from app.db.repo.events import log_event
from app.main import create_app
from app.protocol.items import ItemInfo

PASSWORD = "abcdefgh"


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(Settings(data_dir=str(tmp_path)))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authed(client: TestClient) -> tuple[TestClient, str]:
    response = client.post("/api/auth/setup", json={"password": PASSWORD})
    assert response.status_code == 200
    csrf = response.json()["csrf"]
    return client, csrf


def _call(client: TestClient, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return client.portal.call(lambda: fn(*args, **kwargs))


def _make_account(client: TestClient, xianyu_id: str = "10001") -> int:
    account = _call(client, client.app.state.supervisor.accounts.upsert, xianyu_id)
    return account.id


def test_first_run_setup_then_protected_routes(client: TestClient) -> None:
    status = client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json() == {"password_set": False, "authenticated": False, "csrf": ""}

    assert client.get("/api/accounts").status_code == 401

    short = client.post("/api/auth/setup", json={"password": "short"})
    assert short.status_code == 422

    response = client.post("/api/auth/setup", json={"password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["csrf"]

    assert client.post("/api/auth/setup", json={"password": "ijklmnop"}).status_code == 409
    assert client.get("/api/accounts").status_code == 200


def test_login_logout_and_csrf(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed

    wrong = client.post("/api/auth/login", json={"password": "zzzzzzzz"})
    assert wrong.status_code == 401

    response = client.post(
        "/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "http://testclient"}
    )
    assert response.status_code == 200
    csrf = response.json()["csrf"]

    assert client.post("/api/accounts/1/disable").status_code == 403
    assert client.post("/api/accounts/1/disable", headers={"X-CSRF-Token": csrf}).status_code == 404
    denied = client.post(
        "/api/accounts/1/disable",
        headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
    )
    assert denied.status_code == 403

    ok = client.post("/api/accounts/1/disable", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 404

    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/accounts").status_code == 401


def test_password_storage(client: TestClient) -> None:
    client.post("/api/auth/setup", json={"password": PASSWORD})
    row = _call(
        client,
        client.app.state.db.fetch_one,
        "SELECT value FROM setting WHERE key = 'admin_password'",
    )
    assert row is not None
    stored = row["value"]
    assert stored.startswith("pbkdf2$600000$")
    assert stored != PASSWORD
    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrongpass", stored) is False


def test_accounts_crud(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    response = client.patch(
        f"/api/accounts/{account_id}",
        json={"remark": "主号", "proxy_url": "http://127.0.0.1:7890", "verification_mode": "manual"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["remark"] == "主号"
    assert body["proxy_url"] == "http://127.0.0.1:7890"
    assert body["verification_mode"] == "manual"

    disabled = client.post(f"/api/accounts/{account_id}/disable", headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["status"] == "disabled"

    enabled = client.post(f"/api/accounts/{account_id}/enable", headers=headers)
    assert enabled.json()["enabled"] is True

    listing = client.get("/api/accounts")
    assert len(listing.json()) == 1

    assert client.delete(f"/api/accounts/{account_id}", headers=headers).json() == {"ok": True}
    assert client.get("/api/accounts").json() == []


def test_rules_and_blocks_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    created = client.post(
        f"/api/accounts/{account_id}/rules",
        json={"keyword": "在吗", "reply": "在的", "priority": 10},
        headers=headers,
    )
    assert created.status_code == 200
    rule_id = created.json()["id"]

    config = client.put(
        f"/api/accounts/{account_id}/reply-config",
        json={"default_reply": "您好", "debounce_seconds": 30, "reply_expiry_seconds": 600},
        headers=headers,
    )
    assert config.status_code == 200

    block = client.post(
        f"/api/accounts/{account_id}/blocks", json={"kind": "user", "value": "buyer1"}, headers=headers
    )
    assert block.status_code == 200
    block_id = block.json()["id"]

    bundle = client.get(f"/api/accounts/{account_id}/rules").json()
    assert bundle["rules"][0]["keyword"] == "在吗"
    assert bundle["config"]["default_reply"] == "您好"
    assert bundle["blocks"][0]["value"] == "buyer1"

    patched = client.patch(f"/api/rules/{rule_id}", json={"enabled": False}, headers=headers)
    assert patched.json()["enabled"] is False

    assert client.delete(f"/api/blocks/{block_id}", headers=headers).json() == {"ok": True}
    assert client.delete(f"/api/rules/{rule_id}", headers=headers).json() == {"ok": True}


def test_ai_config_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    initial = client.get(f"/api/accounts/{account_id}/ai").json()
    assert initial["enabled"] is False
    assert initial["has_api_key"] is False
    assert initial["base_url"] == "https://api.deepseek.com"

    saved = client.put(
        f"/api/accounts/{account_id}/ai",
        json={
            "enabled": True,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "persona": "二手数码小店",
            "product_info": "iPhone 13 128G",
            "price_floor_cents": 120000,
            "max_history": 8,
            "timeout_seconds": 15,
            "api_key": "sk-test",
        },
        headers=headers,
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["enabled"] is True
    assert body["has_api_key"] is True
    assert body["price_floor_cents"] == 120000
    assert "api_key" not in body

    cleared = client.put(
        f"/api/accounts/{account_id}/ai",
        json={
            "enabled": False,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "persona": "",
            "product_info": "",
            "price_floor_cents": 0,
            "max_history": 10,
            "timeout_seconds": 20,
            "api_key": "",
        },
        headers=headers,
    )
    assert cleared.json()["has_api_key"] is False

    tested = client.post(
        f"/api/accounts/{account_id}/ai/test", json={"text": "能便宜点吗"}, headers=headers
    )
    assert tested.status_code == 200
    assert tested.json()["ok"] is False
    assert tested.json()["error"] == "no_key"


def test_item_info_api(
    client: TestClient, authed: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _csrf = authed
    account_id = _make_account(client)

    async def fake_get(account_id: int, item_id: str) -> ItemInfo:
        return ItemInfo(
            item_id=item_id,
            title="【最新小说合集】",
            description="拍下自动发货",
            price_cents=10,
            status="在售",
        )

    monkeypatch.setattr(client.app.state.supervisor.items, "get", fake_get)
    response = client.get(f"/api/accounts/{account_id}/items/1096185526203")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "【最新小说合集】"
    assert body["price_cents"] == 10

    async def missing(account_id: int, item_id: str) -> None:
        return None

    monkeypatch.setattr(client.app.state.supervisor.items, "get", missing)
    assert client.get(f"/api/accounts/{account_id}/items/1").status_code == 404


def test_chats_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)
    chats = ChatRepo(client.app.state.db)
    chat = _call(
        client,
        chats.ensure,
        account_id,
        "buyer1",
        peer_id="buyer1",
        peer_name="买家",
        item_id="42",
    )

    listing = client.get("/api/chats").json()
    assert len(listing) == 1
    assert listing[0]["peer_name"] == "买家"

    paused = client.post(f"/api/chats/{chat.id}/pause", json={"minutes": 30}, headers=headers)
    assert paused.status_code == 200
    assert client.get("/api/chats").json()[0]["paused"] is True

    assert client.post(f"/api/chats/{chat.id}/unpause", headers=headers).json() == {"ok": True}
    assert client.get("/api/chats").json()[0]["paused"] is False

    messages = client.get(f"/api/chats/{chat.id}/messages")
    assert messages.status_code == 200
    assert messages.json() == []

    reply = client.post(f"/api/chats/{chat.id}/reply", json={"text": "手动"}, headers=headers)
    assert reply.status_code == 409


def test_overview_and_events(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, _csrf = authed
    account_id = _make_account(client)
    _call(
        client,
        log_event,
        client.app.state.db,
        type="ws.loop_error",
        account_id=account_id,
        level="warning",
        detail={"x": 1},
    )

    overview = client.get("/api/overview").json()
    assert overview["accounts"]["total"] == 1
    assert overview["today"] == {"inbound": 0, "outbound": 0}
    assert overview["issues"][0]["type"] == "ws.loop_error"

    events = client.get("/api/events", params={"level": "warning"}).json()
    assert len(events) == 1


def test_health_open(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

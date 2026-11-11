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
from app.db.repo.items import ItemRepo
from app.delivery.order_sync import OrderSyncError, OrderSyncResult
from app.main import create_app
from app.pipeline.engine import SendResult
from app.protocol.items import ItemInfo
from app.protocol.listing import ListedItem, ListingPage
from app.protocol.polish import PolishResult

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
    status = client.get("/api/auth/status").json()
    assert status["password_set"] is True
    assert status["authenticated"] is True
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


def test_delivery_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    card = client.post(
        f"/api/accounts/{account_id}/cards", json={"name": "电子书", "note": "网盘链接"}, headers=headers
    )
    assert card.status_code == 200
    card_id = card.json()["id"]
    assert card.json()["available"] == 0

    imported = client.post(
        f"/api/cards/{card_id}/secrets",
        json={"text": "SECRET-0001\nSECRET-0002\nSECRET-0001\n\n"},
        headers=headers,
    )
    assert imported.status_code == 200
    assert imported.json()["inserted"] == 2
    assert imported.json()["duplicates"] == 1

    bound = client.post(f"/api/cards/{card_id}/items", json={"item_id": "42"}, headers=headers)
    assert bound.json()["items"] == ["42"]

    cards = client.get(f"/api/accounts/{account_id}/cards").json()["cards"]
    assert cards[0]["available"] == 2 and cards[0]["total"] == 2

    secrets = client.get(f"/api/cards/{card_id}/secrets").json()["secrets"]
    assert len(secrets) == 2
    assert all(secret["masked"] != "SECRET-0001" for secret in secrets)

    assert client.get(f"/api/accounts/{account_id}/orders").json()["orders"] == []
    bad = client.get(f"/api/accounts/{account_id}/orders", params={"status": "bogus"})
    assert bad.status_code == 400

    rule = client.post(
        f"/api/accounts/{account_id}/delivery-rules",
        json={"code": "book", "name": "电子书", "template": "提取码 {secret}", "item_id": "42"},
        headers=headers,
    )
    assert rule.status_code == 200
    rule_id = rule.json()["id"]
    patched_rule = client.patch(
        f"/api/delivery-rules/{rule_id}", json={"enabled": False}, headers=headers
    )
    assert patched_rule.json()["enabled"] is False

    patched_card = client.patch(f"/api/cards/{card_id}", json={"enabled": False}, headers=headers)
    assert patched_card.json()["enabled"] is False

    assert client.get(f"/api/accounts/{account_id}/outbox").json()["tasks"] == []
    assert client.delete(f"/api/delivery-rules/{rule_id}", headers=headers).json() == {"ok": True}
    assert client.delete(f"/api/cards/{card_id}", headers=headers).json() == {"ok": True}
    assert client.get(f"/api/accounts/{account_id}/cards").json()["cards"] == []


def test_card_pool_api_guards(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    blank = client.post(f"/api/accounts/{account_id}/cards", json={"name": ""}, headers=headers)
    assert blank.status_code == 422

    card = client.post(
        f"/api/accounts/{account_id}/cards", json={"name": "电子书"}, headers=headers
    ).json()
    card_id = card["id"]

    empty = client.post(
        f"/api/cards/{card_id}/secrets", json={"text": " \n\n"}, headers=headers
    )
    assert empty.status_code == 400

    too_many = "\n".join(f"S-{index}" for index in range(5001))
    oversized = client.post(
        f"/api/cards/{card_id}/secrets", json={"text": too_many}, headers=headers
    )
    assert oversized.status_code == 400

    missing = client.post("/api/cards/99999/secrets", json={"text": "X"}, headers=headers)
    assert missing.status_code == 404
    assert (
        client.patch("/api/cards/99999", json={"enabled": False}, headers=headers).status_code
        == 404
    )

    first = client.post(
        f"/api/cards/{card_id}/secrets", json={"text": "A\nB"}, headers=headers
    ).json()
    assert first["inserted"] == 2 and first["card"]["available"] == 2
    second = client.post(
        f"/api/cards/{card_id}/secrets", json={"text": "B\nC"}, headers=headers
    ).json()
    assert second["inserted"] == 1 and second["duplicates"] == 1
    assert second["card"]["available"] == 3

    _call(
        client,
        ItemRepo(client.app.state.db).ensure,
        account_id,
        "1096",
        title="电子书",
        status="在售",
    )
    item_path = f"/api/accounts/{account_id}/items/1096/delivery"
    saved = client.put(
        item_path,
        json={
            "enabled": True,
            "mode": "card",
            "card_id": card_id,
            "template": "卡密 {secret}",
        },
        headers=headers,
    )
    assert saved.status_code == 200
    delivery = saved.json()["delivery"]
    assert delivery["card_name"] == "电子书" and delivery["card_available"] == 3

    disabled = client.patch(f"/api/cards/{card_id}", json={"enabled": False}, headers=headers)
    assert disabled.json()["enabled"] is False
    assert (
        client.get(f"/api/accounts/{account_id}/items").json()["items"][0]["delivery"]["card_id"]
        == card_id
    )

    assert client.delete(f"/api/cards/{card_id}", headers=headers).json() == {"ok": True}
    cleared = client.get(item_path).json()["delivery"]
    assert cleared["card_id"] is None
    assert cleared["card_name"] == "" and cleared["card_available"] == 0
    assert cleared["enabled"] is True


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


def test_item_catalog_and_delivery_config_api(
    client: TestClient,
    authed: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)
    supervisor = client.app.state.supervisor
    pages = {
        1: ListingPage(
            [
                ListedItem(
                    "1096185526203",
                    "【最新小说合集】",
                    10,
                    "在售",
                    "https://img.alicdn.com/a.jpg",
                ),
                ListedItem(
                    "710100715884",
                    "已卖掉的旧商品",
                    99,
                    "已售出",
                    "https://img.alicdn.com/b.jpg",
                ),
            ],
            next_page=False,
        )
    }

    async def fake_fetch(jar, user_id, page_number, *, proxy=""):  # type: ignore[no-untyped-def]
        return pages[page_number]

    monkeypatch.setattr(supervisor.catalog, "_fetcher", fake_fetch)

    no_cookie = client.post(f"/api/accounts/{account_id}/items/sync", headers=headers)
    assert no_cookie.status_code == 502

    _call(client, supervisor.accounts.save_cookie, account_id, {"unb": "10001"})
    synced = client.post(f"/api/accounts/{account_id}/items/sync", headers=headers)
    assert synced.status_code == 200
    assert synced.json()["fetched"] == 2
    assert synced.json()["counts"] == {"在售": 1, "已售出": 1}

    listed = client.get(f"/api/accounts/{account_id}/items").json()
    assert [item["item_id"] for item in listed["items"]] == ["1096185526203"]
    assert listed["items"][0]["title"] == "【最新小说合集】"
    assert listed["items"][0]["delivery"] is None
    assert listed["last_synced_at"]

    item_path = f"/api/accounts/{account_id}/items/1096185526203/delivery"
    saved = client.put(
        item_path,
        json={
            "enabled": True,
            "mode": "fixed",
            "content": "下载：https://example.com",
            "auto_consign": False,
        },
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.json()["delivery"]["mode"] == "fixed"
    assert saved.json()["delivery"]["auto_consign"] is False
    assert client.get(item_path).json()["delivery"]["content"] == "下载：https://example.com"

    empty = client.put(item_path, json={"mode": "fixed", "content": "  "}, headers=headers)
    assert empty.status_code == 400

    missing = client.put(
        f"/api/accounts/{account_id}/items/does-not-exist/delivery",
        json={"mode": "manual"},
        headers=headers,
    )
    assert missing.status_code == 404

    other_account = _make_account(client, "20002")
    other_card = client.post(
        f"/api/accounts/{other_account}/cards", json={"name": "别家卡池"}, headers=headers
    ).json()
    foreign = client.put(
        item_path,
        json={"mode": "card", "card_id": other_card["id"]},
        headers=headers,
    )
    assert foreign.status_code == 400

    default_off = client.put(item_path, json={"mode": "manual"}, headers=headers)
    assert default_off.status_code == 200
    assert default_off.json()["delivery"]["enabled"] is False

    assert client.delete(item_path, headers=headers).json() == {"ok": True}
    assert client.get(item_path).json()["delivery"] is None


def test_missing_account_guards(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    missing = 99999

    assert (
        client.patch(f"/api/accounts/{missing}", json={"remark": "x"}, headers=headers).status_code
        == 404
    )
    assert client.post(f"/api/accounts/{missing}/enable", headers=headers).status_code == 404
    assert client.post(f"/api/accounts/{missing}/disable", headers=headers).status_code == 404
    assert client.delete(f"/api/accounts/{missing}", headers=headers).status_code == 404
    assert client.get(f"/api/accounts/{missing}/rules").status_code == 404
    assert client.get(f"/api/accounts/{missing}/ai").status_code == 404
    assert client.get(f"/api/accounts/{missing}/cards").status_code == 404
    assert (
        client.post(
            f"/api/accounts/{missing}/rules",
            json={"keyword": "在吗", "reply": "在的"},
            headers=headers,
        ).status_code
        == 404
    )


class _FakeQR:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.timeouts: list[int] = []
        self.targets: list[int | None] = []

    async def start(self, timeout: int, account_id: int | None = None) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("网络不可用")
        self.timeouts.append(timeout)
        self.targets.append(account_id)
        return {"session_id": "s1", "qr": "data:image/png;base64,AAA", "state": "waiting"}

    async def status(self, session_id: str) -> dict[str, object] | None:
        if session_id != "s1":
            return None
        return {"state": "success", "account": {"xianyu_id": "10001"}}


def test_qr_login_endpoints(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}

    fake = _FakeQR()
    client.app.state.qr = fake
    started = client.post("/api/accounts/qr", headers=headers)
    assert started.status_code == 200
    assert started.json()["session_id"] == "s1"
    assert started.json()["qr"] == "data:image/png;base64,AAA"
    assert fake.timeouts == [client.app.state.settings.http_timeout]
    assert fake.targets == [None]

    status_response = client.get("/api/accounts/qr/s1")
    assert status_response.status_code == 200
    assert status_response.json() == {"state": "success", "account": {"xianyu_id": "10001"}}
    assert client.get("/api/accounts/qr/missing").status_code == 404

    client.app.state.qr = _FakeQR(fail=True)
    failed = client.post("/api/accounts/qr", headers=headers)
    assert failed.status_code == 502
    assert "网络不可用" in failed.json()["detail"]


def test_qr_login_bound_to_existing_account(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)
    fake = _FakeQR()
    client.app.state.qr = fake

    started = client.post("/api/accounts/qr", json={"account_id": account_id}, headers=headers)
    assert started.status_code == 200
    assert fake.targets == [account_id]

    missing = client.post("/api/accounts/qr", json={"account_id": 999}, headers=headers)
    assert missing.status_code == 404
    assert fake.targets == [account_id]


class _FakeSender:
    def __init__(self, result: SendResult) -> None:
        self.result = result
        self.sent: list[tuple[str, str, str]] = []

    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult:
        self.sent.append((chat_id, to_user_id, text))
        return self.result


def test_chat_filters_guards_and_manual_reply(
    client: TestClient, authed: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    first = _make_account(client)
    second = _make_account(client, "20002")
    chats = ChatRepo(client.app.state.db)
    chat_a = _call(
        client, chats.ensure, first, "buyerA", peer_id="bA", peer_name="买家A", item_id="42"
    )
    _call(client, chats.ensure, second, "buyerB", peer_id="bB", peer_name="买家B")

    assert {chat["chat_id"] for chat in client.get("/api/chats").json()} == {"buyerA", "buyerB"}
    filtered = client.get("/api/chats", params={"account_id": second}).json()
    assert [chat["chat_id"] for chat in filtered] == ["buyerB"]

    assert client.get("/api/chats/99999/messages").status_code == 404
    assert (
        client.post("/api/chats/99999/pause", json={"minutes": 5}, headers=headers).status_code
        == 404
    )
    assert client.post("/api/chats/99999/unpause", headers=headers).status_code == 404
    assert (
        client.post("/api/chats/99999/reply", json={"text": "x"}, headers=headers).status_code
        == 404
    )
    assert (
        client.post(f"/api/chats/{chat_a.id}/pause", json={"minutes": 0}, headers=headers).status_code
        == 422
    )

    sender = _FakeSender(SendResult(ok=True, msg_id="m-1"))
    monkeypatch.setattr(
        client.app.state.supervisor, "sender_for", lambda account_id: sender
    )
    replied = client.post(
        f"/api/chats/{chat_a.id}/reply", json={"text": "手动回复"}, headers=headers
    )
    assert replied.status_code == 200
    assert replied.json() == {"ok": True, "msg_id": "m-1"}
    assert sender.sent == [("buyerA", "bA", "手动回复")]

    messages = client.get(f"/api/chats/{chat_a.id}/messages").json()
    assert messages[-1]["direction"] == "out"
    assert messages[-1]["text"] == "手动回复"
    assert messages[-1]["status"] == "sent"


def test_rules_validation_guards(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    assert (
        client.patch("/api/rules/99999", json={"enabled": False}, headers=headers).status_code
        == 404
    )
    assert client.delete("/api/blocks/99999", headers=headers).json() == {"ok": True}
    assert (
        client.put(
            f"/api/accounts/{account_id}/reply-config",
            json={"debounce_seconds": -1},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/accounts/{account_id}/blocks",
            json={"kind": "bogus", "value": "x"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/accounts/{account_id}/rules",
            json={"keyword": "", "reply": "x"},
            headers=headers,
        ).status_code
        == 422
    )


def test_account_ops_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    initial = client.get(f"/api/accounts/{account_id}/ops")
    assert initial.status_code == 200
    assert initial.json() == {
        "account_id": account_id,
        "auto_polish": False,
        "flower_enabled": False,
    }

    updated = client.put(
        f"/api/accounts/{account_id}/ops", json={"auto_polish": True}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["auto_polish"] is True and updated.json()["flower_enabled"] is False

    again = client.put(
        f"/api/accounts/{account_id}/ops", json={"flower_enabled": True}, headers=headers
    )
    assert again.json()["auto_polish"] is True and again.json()["flower_enabled"] is True
    assert client.get(f"/api/accounts/{account_id}/ops").json() == again.json()

    assert client.get("/api/accounts/99999/ops").status_code == 404
    assert (
        client.put(
            "/api/accounts/99999/ops", json={"auto_polish": True}, headers=headers
        ).status_code
        == 404
    )


def test_polish_item_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)
    supervisor = client.app.state.supervisor
    _call(client, supervisor.accounts.save_cookie, account_id, {"_m_h5_tk": "tk"})
    repo = ItemRepo(client.app.state.db)
    _call(client, repo.ensure, account_id, "1096", title="电子书", status="在售")
    _call(client, repo.ensure, account_id, "2000", title="已售", status="已售出")

    calls: list[str] = []

    async def fake_polish(jar: Any, item_id: str, *, proxy: str = "") -> PolishResult:
        calls.append(item_id)
        return PolishResult(ok=True, message="擦亮成功")

    supervisor.polish._polisher = fake_polish

    path = f"/api/accounts/{account_id}/items/1096/polish"
    first = client.post(path, headers=headers)
    assert first.status_code == 200
    assert first.json()["ok"] is True and first.json()["already"] is False
    assert first.json()["message"] == "擦亮成功"
    assert first.json()["polished_at"]

    second = client.post(path, headers=headers)
    assert second.status_code == 200
    assert second.json()["already"] is True and second.json()["message"] == "今天已擦亮"
    assert calls == ["1096"]

    items = client.get(f"/api/accounts/{account_id}/items").json()["items"]
    assert items[0]["item_id"] == "1096"
    assert items[0]["polished_at"] and items[0]["polish_error"] == ""

    missing = client.post(f"/api/accounts/{account_id}/items/missing/polish", headers=headers)
    assert missing.status_code == 404
    sold = client.post(f"/api/accounts/{account_id}/items/2000/polish", headers=headers)
    assert sold.status_code == 409


def test_polish_item_api_guards_login_and_reports_failure(
    client: TestClient, authed: tuple[TestClient, str]
) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)
    supervisor = client.app.state.supervisor
    repo = ItemRepo(client.app.state.db)
    _call(client, repo.ensure, account_id, "3000", title="电子书", status="在售")

    no_login = client.post(f"/api/accounts/{account_id}/items/3000/polish", headers=headers)
    assert no_login.status_code == 409

    _call(client, supervisor.accounts.save_cookie, account_id, {"_m_h5_tk": "tk"})

    async def failing_polish(jar: Any, item_id: str, *, proxy: str = "") -> PolishResult:
        return PolishResult(ok=False, message="FAIL_BIZ_SYSTEM_ERROR::系统繁忙")

    supervisor.polish._polisher = failing_polish
    failed = client.post(f"/api/accounts/{account_id}/items/3000/polish", headers=headers)
    assert failed.status_code == 502
    assert "系统繁忙" in failed.json()["detail"]
    stored = _call(client, repo.get, account_id, "3000")
    assert stored is not None and stored.polished_at == ""
    assert "系统繁忙" in stored.polish_error


class _FakeOrderSync:
    def __init__(
        self, result: OrderSyncResult | None = None, *, error: OrderSyncError | None = None
    ) -> None:
        self.result = result or OrderSyncResult(
            account_id=1,
            fetched=3,
            inserted=2,
            queued=1,
            manual=1,
            delivered=0,
            skipped=0,
            last_synced_at="2026-11-11T02:00:00Z",
        )
        self.error = error
        self.calls: list[int] = []

    async def sync(self, account_id: int, **_: Any) -> OrderSyncResult:
        self.calls.append(account_id)
        if self.error is not None:
            raise self.error
        return self.result


def test_orders_sync_api(client: TestClient, authed: tuple[TestClient, str]) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)
    fake = _FakeOrderSync()
    client.app.state.supervisor.order_sync = fake

    response = client.post(f"/api/accounts/{account_id}/orders/sync", headers=headers)

    assert response.status_code == 200
    assert fake.calls == [account_id]
    assert response.json()["inserted"] == 2
    assert response.json()["last_synced_at"] == "2026-11-11T02:00:00Z"

    _call(
        client,
        client.app.state.supervisor.accounts.set_orders_synced_at,
        account_id,
        "2026-11-11T02:00:00Z",
    )
    listed = client.get(f"/api/accounts/{account_id}/orders")
    assert listed.status_code == 200
    assert listed.json()["last_synced_at"] == "2026-11-11T02:00:00Z"

    assert client.post("/api/accounts/999/orders/sync", headers=headers).status_code == 404

    client.app.state.supervisor.order_sync = _FakeOrderSync(error=OrderSyncError("登录已失效"))
    failed = client.post(f"/api/accounts/{account_id}/orders/sync", headers=headers)
    assert failed.status_code == 502
    assert "登录已失效" in failed.json()["detail"]


def test_account_nickname_api(
    client: TestClient, authed: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    account_id = _make_account(client)

    assert client.post(f"/api/accounts/{account_id}/nickname", headers=headers).status_code == 409

    _call(client, client.app.state.supervisor.accounts.save_cookie, account_id, {"unb": "10001"})
    assert client.post(f"/api/accounts/{account_id}/nickname", headers=headers).status_code == 409

    _call(client, ChatRepo(client.app.state.db).ensure, account_id, "c1", peer_id="b1")

    import app.api.accounts as accounts_api

    async def fake_fetch(jar, chat_id, *, proxy: str = "") -> str:
        assert chat_id == "c1"
        return "18号当铺"

    monkeypatch.setattr(accounts_api, "fetch_own_nickname", fake_fetch)
    response = client.post(f"/api/accounts/{account_id}/nickname", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"nickname": "18号当铺"}

    assert client.post("/api/accounts/999/nickname", headers=headers).status_code == 404

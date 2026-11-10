from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin, require_write
from app.db.repo.delivery import (
    ORDER_STATUSES,
    CardRepo,
    DeliveryRuleRepo,
    OrderRepo,
    mask_secret,
)
from app.db.repo.items import ItemDeliveryRepo, ItemRepo
from app.delivery.catalog import CatalogError
from app.delivery.polish import PolishError
from app.protocol.listing import STATUS_ON_SALE

router = APIRouter(tags=["delivery"])

ITEM_STATUSES = ("在售", "已售出", "已下架", "未知")


async def _account_or_404(request: Request, account_id: int):  # type: ignore[no-untyped-def]
    account = await request.app.state.supervisor.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


def _card_payload(card) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": card.id,
        "account_id": card.account_id,
        "name": card.name,
        "note": card.note,
        "enabled": card.enabled,
        "available": card.available,
        "used": card.used,
        "total": card.total,
    }


def _order_payload(order) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": order.id,
        "account_id": order.account_id,
        "order_no": order.order_no,
        "chat_id": order.chat_id,
        "buyer_id": order.buyer_id,
        "item_id": order.item_id,
        "title": order.title,
        "amount_cents": order.amount_cents,
        "status": order.status,
        "paid_at": order.paid_at,
        "delivered_at": order.delivered_at,
        "detail": order.detail,
        "card_secret_id": order.card_secret_id,
        "created_at": order.created_at,
        "flower_status": order.flower_status,
        "flower_at": order.flower_at,
        "flower_error": order.flower_error,
    }


def _rule_payload(rule) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": rule.id,
        "account_id": rule.account_id,
        "code": rule.code,
        "name": rule.name,
        "item_id": rule.item_id,
        "template": rule.template,
        "priority": rule.priority,
        "enabled": rule.enabled,
    }


def _item_delivery_payload(config, card=None) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "card_id": config.card_id,
        "card_name": card.name if card is not None else "",
        "card_available": card.available if card is not None else 0,
        "template": config.template,
        "content": config.content,
        "auto_consign": config.auto_consign,
        "remark": config.remark,
    }


def _item_payload(item, config=None, card=None) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": item.id,
        "account_id": item.account_id,
        "item_id": item.item_id,
        "title": item.title,
        "price_cents": item.price_cents,
        "status": item.status,
        "cover_url": item.cover_url,
        "synced_at": item.synced_at,
        "polished_at": item.polished_at,
        "polish_error": item.polish_error,
        "delivery": _item_delivery_payload(config, card) if config is not None else None,
    }


class CardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)


class CardPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


class SecretImport(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class ItemBind(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)


class DeliveryRuleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    template: str = Field(min_length=1, max_length=4000)
    item_id: str = Field(default="", max_length=64)
    priority: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True


class DeliveryRulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    template: str | None = Field(default=None, min_length=1, max_length=4000)
    item_id: str | None = Field(default=None, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class ItemDeliveryPut(BaseModel):
    enabled: bool = False
    mode: Literal["card", "fixed", "manual"] = "card"
    card_id: int | None = None
    template: str = Field(default="", max_length=4000)
    content: str = Field(default="", max_length=4000)
    auto_consign: bool = True
    remark: str = Field(default="", max_length=200)


@router.get("/accounts/{account_id}/cards")
async def list_cards(
    account_id: int, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    cards = await CardRepo(request.app.state.db).list_by_account(account_id)
    return {"cards": [_card_payload(card) for card in cards]}


@router.post("/accounts/{account_id}/cards")
async def create_card(
    account_id: int, body: CardCreate, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    card = await CardRepo(request.app.state.db).create(account_id, name=body.name, note=body.note)
    return _card_payload(card)


@router.patch("/cards/{card_id}")
async def patch_card(
    card_id: int, body: CardPatch, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    card = await CardRepo(request.app.state.db).update(
        card_id, name=body.name, note=body.note, enabled=body.enabled
    )
    if card is None:
        raise HTTPException(status_code=404, detail="卡券不存在")
    return _card_payload(card)


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, bool]:
    await CardRepo(request.app.state.db).delete(card_id)
    return {"ok": True}


@router.get("/cards/{card_id}/items")
async def list_card_items(
    card_id: int, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    items = await CardRepo(request.app.state.db).items_of(card_id)
    return {"items": items}


@router.post("/cards/{card_id}/items")
async def bind_card_item(
    card_id: int, body: ItemBind, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    repo = CardRepo(request.app.state.db)
    card = await repo.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡券不存在")
    await repo.bind_item(card.account_id, card_id, body.item_id.strip())
    return {"items": await repo.items_of(card_id)}


@router.delete("/cards/{card_id}/items/{item_id}")
async def unbind_card_item(
    card_id: int, item_id: str, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    repo = CardRepo(request.app.state.db)
    await repo.unbind_item(card_id, item_id)
    return {"items": await repo.items_of(card_id)}


@router.get("/cards/{card_id}/secrets")
async def list_card_secrets(
    card_id: int, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    secrets = await CardRepo(request.app.state.db).list_secrets(card_id)
    return {
        "secrets": [
            {
                "id": secret.id,
                "masked": mask_secret(secret.secret),
                "status": secret.status,
                "order_no": secret.order_no,
                "claimed_at": secret.claimed_at,
                "used_at": secret.used_at,
            }
            for secret in secrets
        ]
    }


@router.post("/cards/{card_id}/secrets")
async def import_card_secrets(
    card_id: int, body: SecretImport, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    repo = CardRepo(request.app.state.db)
    card = await repo.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="卡券不存在")
    lines = [line.strip() for line in body.text.splitlines()]
    secrets = [line for line in lines if line]
    if not secrets:
        raise HTTPException(status_code=400, detail="没有可导入的卡密")
    if len(secrets) > 5000:
        raise HTTPException(status_code=400, detail="单次最多导入 5000 条")
    inserted = await repo.add_secrets(card_id, secrets)
    refreshed = await repo.get(card_id)
    return {
        "inserted": inserted,
        "duplicates": len(secrets) - inserted,
        "card": _card_payload(refreshed) if refreshed is not None else None,
    }


@router.get("/accounts/{account_id}/orders")
async def list_orders(
    account_id: int,
    request: Request,
    status: str = "",
    limit: int = 200,
    _session=Depends(require_admin),
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    if status and status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail="未知订单状态")
    orders = await OrderRepo(request.app.state.db).list(
        account_id, status=status, limit=max(1, min(limit, 500))
    )
    return {"orders": [_order_payload(order) for order in orders]}


@router.post("/orders/{order_id}/redeliver")
async def redeliver_order(
    order_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, bool]:
    ok = await request.app.state.supervisor.deliveries.redeliver(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"ok": True}


@router.post("/orders/{order_id}/mark-delivered")
async def mark_order_delivered(
    order_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, bool]:
    ok = await request.app.state.supervisor.deliveries.mark_delivered(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"ok": True}


@router.post("/orders/{order_id}/consign")
async def consign_order(
    order_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    ok, reason = await request.app.state.supervisor.deliveries.consign(order_id)
    return {"ok": ok, "reason": reason}


@router.get("/accounts/{account_id}/outbox")
async def list_outbox(
    account_id: int, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    tasks = await request.app.state.supervisor.deliveries.outbox.list_recent(account_id)
    return {"tasks": tasks}


@router.get("/accounts/{account_id}/delivery-rules")
async def list_delivery_rules(
    account_id: int, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    rules = await DeliveryRuleRepo(request.app.state.db).list_by_account(account_id)
    return {"rules": [_rule_payload(rule) for rule in rules]}


@router.post("/accounts/{account_id}/delivery-rules")
async def create_delivery_rule(
    account_id: int,
    body: DeliveryRuleCreate,
    request: Request,
    _session=Depends(require_write),
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    try:
        rule = await DeliveryRuleRepo(request.app.state.db).create(
            account_id,
            code=body.code,
            name=body.name,
            template=body.template,
            item_id=body.item_id,
            priority=body.priority,
            enabled=body.enabled,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="规则编码已存在") from exc
    return _rule_payload(rule)


@router.patch("/delivery-rules/{rule_id}")
async def patch_delivery_rule(
    rule_id: int,
    body: DeliveryRulePatch,
    request: Request,
    _session=Depends(require_write),
) -> dict[str, object]:
    rule = await DeliveryRuleRepo(request.app.state.db).update(
        rule_id,
        name=body.name,
        template=body.template,
        item_id=body.item_id,
        priority=body.priority,
        enabled=body.enabled,
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    return _rule_payload(rule)


@router.delete("/delivery-rules/{rule_id}")
async def delete_delivery_rule(
    rule_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, bool]:
    await DeliveryRuleRepo(request.app.state.db).delete(rule_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/items/sync")
async def sync_items(
    account_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    account = await _account_or_404(request, account_id)
    try:
        result = await request.app.state.supervisor.catalog.sync(account)
    except CatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    items = await ItemRepo(request.app.state.db).counts(account_id)
    return {
        "fetched": result.fetched,
        "pages": result.pages,
        "synced_at": result.synced_at,
        "counts": items,
    }


@router.get("/accounts/{account_id}/items")
async def list_items(
    account_id: int,
    request: Request,
    status: str = "在售",
    q: str = "",
    limit: int = 500,
    _session=Depends(require_admin),
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    if status and status not in ITEM_STATUSES:
        raise HTTPException(status_code=400, detail="未知商品状态")
    repo = ItemRepo(request.app.state.db)
    items = await repo.list(
        account_id, status=status, query=q.strip(), limit=max(1, min(limit, 1000))
    )
    configs = {
        config.item_id: config
        for config in await ItemDeliveryRepo(request.app.state.db).list_by_account(account_id)
    }
    cards = {
        card.id: card for card in await CardRepo(request.app.state.db).list_by_account(account_id)
    }
    payloads = []
    for item in items:
        config = configs.get(item.item_id)
        card = cards.get(config.card_id) if config is not None and config.card_id else None
        payloads.append(_item_payload(item, config, card))
    return {
        "items": payloads,
        "counts": await repo.counts(account_id),
        "last_synced_at": await repo.last_synced_at(account_id),
    }


@router.post("/accounts/{account_id}/items/{item_id}/polish")
async def polish_item_now(
    account_id: int, item_id: str, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    account = await _account_or_404(request, account_id)
    repo = ItemRepo(request.app.state.db)
    item = await repo.get(account_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="商品不存在，请先同步商品")
    if item.status != STATUS_ON_SALE:
        raise HTTPException(status_code=409, detail="仅支持在售商品擦亮")
    if not await request.app.state.supervisor.accounts.load_cookie(account_id):
        raise HTTPException(status_code=409, detail="账号尚未登录，请先扫码登录")
    try:
        outcome = await request.app.state.supervisor.polish.polish(account, item_id)
    except PolishError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if outcome.offline:
        raise HTTPException(status_code=409, detail=outcome.message)
    if not outcome.ok:
        raise HTTPException(status_code=502, detail=outcome.message)
    refreshed = await repo.get(account_id, item_id)
    return {
        "ok": True,
        "already": outcome.already,
        "message": outcome.message,
        "polished_at": refreshed.polished_at if refreshed is not None else "",
        "polish_error": refreshed.polish_error if refreshed is not None else "",
    }


@router.get("/accounts/{account_id}/items/{item_id}/delivery")
async def get_item_delivery(
    account_id: int, item_id: str, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    config = await ItemDeliveryRepo(request.app.state.db).get(account_id, item_id)
    card = None
    if config is not None and config.card_id is not None:
        card = await CardRepo(request.app.state.db).get(config.card_id)
    return {"delivery": _item_delivery_payload(config, card) if config is not None else None}


@router.put("/accounts/{account_id}/items/{item_id}/delivery")
async def put_item_delivery(
    account_id: int,
    item_id: str,
    body: ItemDeliveryPut,
    request: Request,
    _session=Depends(require_write),
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    item_repo = ItemRepo(request.app.state.db)
    if await item_repo.get(account_id, item_id) is None:
        raise HTTPException(status_code=404, detail="商品不存在，请先同步商品")
    if body.mode == "fixed" and not body.content.strip():
        raise HTTPException(status_code=400, detail="固定发货内容不能为空")
    card = None
    if body.card_id is not None:
        card = await CardRepo(request.app.state.db).get(body.card_id)
        if card is None or card.account_id != account_id:
            raise HTTPException(status_code=400, detail="卡密池不存在或不属于该账号")
    config = await ItemDeliveryRepo(request.app.state.db).upsert(
        account_id,
        item_id,
        enabled=body.enabled,
        mode=body.mode,
        card_id=body.card_id,
        template=body.template,
        content=body.content,
        auto_consign=body.auto_consign,
        remark=body.remark,
    )
    return {"delivery": _item_delivery_payload(config, card)}


@router.delete("/accounts/{account_id}/items/{item_id}/delivery")
async def delete_item_delivery(
    account_id: int, item_id: str, request: Request, _session=Depends(require_write)
) -> dict[str, bool]:
    await _account_or_404(request, account_id)
    await ItemDeliveryRepo(request.app.state.db).delete(account_id, item_id)
    return {"ok": True}

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin, require_write
from app.db.repo.rules import BLOCK_KINDS, BlockRepo, KeywordRuleRepo, ReplyConfigRepo

router = APIRouter(tags=["rules"])


async def _account_or_404(request: Request, account_id: int) -> None:
    account = await request.app.state.supervisor.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")


class RuleCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    reply: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=100, ge=0, le=10000)


class RulePatch(BaseModel):
    keyword: str | None = Field(default=None, min_length=1, max_length=200)
    reply: str | None = Field(default=None, min_length=1, max_length=2000)
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class ReplyConfigBody(BaseModel):
    default_reply: str = Field(default="", max_length=2000)
    debounce_seconds: int = Field(default=0, ge=0, le=86400)
    reply_expiry_seconds: int = Field(default=0, ge=0, le=86400 * 30)


class BlockCreate(BaseModel):
    kind: str = Field(pattern="^(user|item|keyword)$")
    value: str = Field(min_length=1, max_length=200)


@router.get("/accounts/{account_id}/rules")
async def get_rules(
    account_id: int, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    db = request.app.state.db
    rules = await KeywordRuleRepo(db).list_by_account(account_id)
    config = await ReplyConfigRepo(db).get(account_id)
    blocks = await BlockRepo(db).list_by_account(account_id)
    return {
        "rules": [
            {
                "id": rule.id,
                "keyword": rule.keyword,
                "reply": rule.reply,
                "priority": rule.priority,
                "enabled": rule.enabled,
            }
            for rule in rules
        ],
        "config": {
            "default_reply": config.default_reply,
            "debounce_seconds": config.debounce_seconds,
            "reply_expiry_seconds": config.reply_expiry_seconds,
        },
        "blocks": [
            {"id": block.id, "kind": block.kind, "value": block.value} for block in blocks
        ],
    }


@router.post("/accounts/{account_id}/rules")
async def create_rule(
    account_id: int, body: RuleCreate, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    rule = await KeywordRuleRepo(request.app.state.db).create(
        account_id, keyword=body.keyword, reply=body.reply, priority=body.priority
    )
    return {
        "id": rule.id,
        "keyword": rule.keyword,
        "reply": rule.reply,
        "priority": rule.priority,
        "enabled": rule.enabled,
    }


@router.patch("/rules/{rule_id}")
async def patch_rule(
    rule_id: int, body: RulePatch, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    rule = await KeywordRuleRepo(request.app.state.db).update(
        rule_id,
        keyword=body.keyword,
        reply=body.reply,
        priority=body.priority,
        enabled=body.enabled,
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {
        "id": rule.id,
        "keyword": rule.keyword,
        "reply": rule.reply,
        "priority": rule.priority,
        "enabled": rule.enabled,
    }


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, request: Request, _session=Depends(require_write)) -> dict[str, bool]:
    await KeywordRuleRepo(request.app.state.db).delete(rule_id)
    return {"ok": True}


@router.put("/accounts/{account_id}/reply-config")
async def put_reply_config(
    account_id: int, body: ReplyConfigBody, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    config = await ReplyConfigRepo(request.app.state.db).put(
        account_id,
        default_reply=body.default_reply,
        debounce_seconds=body.debounce_seconds,
        reply_expiry_seconds=body.reply_expiry_seconds,
    )
    return {
        "default_reply": config.default_reply,
        "debounce_seconds": config.debounce_seconds,
        "reply_expiry_seconds": config.reply_expiry_seconds,
    }


@router.post("/accounts/{account_id}/blocks")
async def create_block(
    account_id: int, body: BlockCreate, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    await _account_or_404(request, account_id)
    if body.kind not in BLOCK_KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 user/item/keyword")
    block = await BlockRepo(request.app.state.db).add(account_id, body.kind, body.value)
    return {"id": block.id, "kind": block.kind, "value": block.value}


@router.delete("/blocks/{entry_id}")
async def delete_block(entry_id: int, request: Request, _session=Depends(require_write)) -> dict[str, bool]:
    await BlockRepo(request.app.state.db).remove(entry_id)
    return {"ok": True}

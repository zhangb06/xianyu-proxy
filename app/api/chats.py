from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin, require_write
from app.db.repo.chats import ChatRepo
from app.db.repo.messages import MessageRepo

router = APIRouter(tags=["chats"])


def _serialize_chat(chat, account_remark: str) -> dict[str, object]:
    return {
        "id": chat.id,
        "account_id": chat.account_id,
        "account_remark": account_remark,
        "chat_id": chat.chat_id,
        "peer_id": chat.peer_id,
        "peer_name": chat.peer_name,
        "item_id": chat.item_id,
        "paused": chat.reply_paused,
        "paused_until": chat.paused_until,
        "last_message_at": chat.last_message_at,
    }


def _serialize_message(message) -> dict[str, object]:
    return {
        "id": message.id,
        "direction": message.direction,
        "kind": message.kind,
        "sender_id": message.sender_id,
        "text": message.text,
        "status": message.status,
        "timestamp_ms": message.timestamp_ms,
        "created_at": message.id,
    }


@router.get("/chats")
async def list_chats(
    request: Request, account_id: int | None = None, _session=Depends(require_admin)
) -> list[dict[str, object]]:
    db = request.app.state.db
    chats = ChatRepo(db)
    if account_id is not None:
        rows = await chats.list_by_account(account_id)
    else:
        rows = await chats.list_recent()
    accounts = {a.id: a for a in await request.app.state.supervisor.accounts.list_all()}
    return [_serialize_chat(chat, accounts[chat.account_id].remark) for chat in rows]


@router.get("/chats/{row_id}/messages")
async def list_chat_messages(
    row_id: int, request: Request, _session=Depends(require_admin)
) -> list[dict[str, object]]:
    db = request.app.state.db
    chat = await ChatRepo(db).get_by_id(row_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await MessageRepo(db).list_by_chat(chat.account_id, chat.chat_id, limit=200)
    return [_serialize_message(m) for m in reversed(messages)]


class PauseBody(BaseModel):
    minutes: int = Field(ge=1, le=10080)


@router.post("/chats/{row_id}/pause")
async def pause_chat(
    row_id: int, body: PauseBody, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    db = request.app.state.db
    chat = await ChatRepo(db).get_by_id(row_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    until = (datetime.now(UTC) + timedelta(minutes=body.minutes)).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    await ChatRepo(db).pause(row_id, until)
    return {"paused_until": until}


@router.post("/chats/{row_id}/unpause")
async def unpause_chat(row_id: int, request: Request, _session=Depends(require_write)) -> dict[str, bool]:
    db = request.app.state.db
    chat = await ChatRepo(db).get_by_id(row_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    await ChatRepo(db).unpause(row_id)
    return {"ok": True}


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/chats/{row_id}/reply")
async def manual_reply(
    row_id: int, body: ReplyBody, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    db = request.app.state.db
    chat = await ChatRepo(db).get_by_id(row_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    supervisor = request.app.state.supervisor
    account = await supervisor.accounts.get(chat.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    actor = supervisor.sender_for(account.id)
    if actor is None:
        raise HTTPException(status_code=409, detail="账号未在线，无法发送")
    result = await supervisor.pipeline.send_manual(account, chat, body.text, actor)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.reason or "发送失败")
    return {"ok": True, "msg_id": result.msg_id}

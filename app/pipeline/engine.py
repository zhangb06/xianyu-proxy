from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from loguru import logger

from app.ai.replier import AIReplyService
from app.core.ids import generate_uuid
from app.core.timeutil import utc_now_ms
from app.db.engine import Database
from app.db.repo.accounts import Account
from app.db.repo.chats import Chat, ChatRepo
from app.db.repo.events import log_event
from app.db.repo.messages import MessageRepo
from app.db.repo.rules import BlockRepo, KeywordRuleRepo, ReplyConfigRepo

REPLYABLE_KINDS = ("text", "image")


@dataclass(slots=True)
class SendResult:
    ok: bool
    msg_id: str = ""
    reason: str = ""
    outcome: str = ""


class OutboundSender(Protocol):
    async def send_ws(self, chat_id: str, to_user_id: str, text: str) -> SendResult: ...


class ReplyPipeline:
    """去重 → 落库 → 分类 → 规则匹配 → 回复决策。

    出站先落库再发送；自发消息与卡片消息只落库对账，不进回复管道。
    """

    def __init__(self, db: Database, ai: AIReplyService | None = None) -> None:
        self.db = db
        self.ai = ai
        self.messages = MessageRepo(db)
        self.chats = ChatRepo(db)
        self.rules = KeywordRuleRepo(db)
        self.configs = ReplyConfigRepo(db)
        self.blocks = BlockRepo(db)

    async def handle_inbound(self, account: Account, parsed: dict[str, Any], sender: OutboundSender) -> str:
        account_id = account.id
        inserted = await self.messages.insert_inbound(account_id, parsed)
        chat = await self.chats.ensure(
            account_id,
            parsed["chat_id"],
            peer_id=parsed["sender_id"],
            peer_name=parsed["sender_name"],
            item_id=parsed["item_id"],
            timestamp_ms=parsed["timestamp_ms"],
        )
        if parsed["timestamp_ms"]:
            await self.chats.touch_message(chat.id, parsed["timestamp_ms"])
        if not inserted:
            return "duplicate"
        if parsed["is_self"]:
            return "self_echo"
        if parsed["kind"] not in REPLYABLE_KINDS:
            return "ignored"

        if await self._is_blocked(account_id, parsed):
            return "blocked"
        if chat.reply_paused:
            return "chat_paused"

        config = await self.configs.get(account_id)
        if config.reply_expiry_seconds and _is_expired(parsed["timestamp_ms"], config.reply_expiry_seconds):
            return "expired"

        if config.debounce_seconds and _within_debounce(chat, config.debounce_seconds):
            return "debounced"

        decision = await self._decide_reply(account_id, chat, parsed["text"])
        if decision is None:
            return "no_rule"
        reply_text, source = decision

        outcome = await self._send(account, chat, parsed, reply_text, sender, source=source)
        return outcome.outcome

    async def send_manual(
        self, account: Account, chat: Chat, text: str, sender: OutboundSender
    ) -> SendResult:
        parsed = {
            "dedup_key": f"manual:{generate_uuid()}",
            "msg_id": "",
            "chat_id": chat.chat_id,
            "sender_id": chat.peer_id,
            "item_id": chat.item_id,
            "timestamp_ms": utc_now_ms(),
        }
        return await self._send(account, chat, parsed, text, sender, source="manual")

    async def _send(
        self,
        account: Account,
        chat: Chat,
        parsed: dict[str, Any],
        text: str,
        sender: OutboundSender,
        *,
        source: str,
    ) -> SendResult:
        account_id = account.id
        message_id = await self.messages.insert_outbound(
            account_id,
            chat_id=chat.chat_id,
            dedup_key=parsed["dedup_key"] if source == "manual" else f"reply:{parsed['dedup_key']}",
            kind="text",
            text=text,
            item_id=parsed.get("item_id", ""),
            status="pending",
        )
        try:
            result = await sender.send_ws(chat.chat_id, parsed["sender_id"], text)
        except Exception as exc:
            logger.warning(f"账号 {account_id} 回复发送异常: {exc}")
            await self.messages.set_status(message_id, "failed")
            await log_event(
                self.db,
                type="reply.send_error",
                account_id=account_id,
                level="warning",
                detail={"chat_id": chat.chat_id, "source": source, "error": str(exc)},
            )
            return SendResult(ok=False, reason=str(exc), outcome="send_error")

        if not result.ok:
            await self.messages.set_status(message_id, "failed", msg_id=result.msg_id)
            await log_event(
                self.db,
                type="reply.rejected" if result.reason else "reply.no_response",
                account_id=account_id,
                level="warning",
                detail={"chat_id": chat.chat_id, "source": source, "reason": result.reason},
            )
            return SendResult(ok=False, msg_id=result.msg_id, reason=result.reason, outcome="send_failed")

        await self.messages.set_status(message_id, "sent", msg_id=result.msg_id)
        await self.chats.mark_replied(chat.id, utc_now_ms())
        await log_event(
            self.db,
            type="reply.sent",
            account_id=account_id,
            level="info",
            detail={"chat_id": chat.chat_id, "source": source, "msg_id": result.msg_id},
        )
        return SendResult(ok=True, msg_id=result.msg_id, outcome="replied")

    async def _is_blocked(self, account_id: int, parsed: dict[str, Any]) -> bool:
        if await self.blocks.is_blocked(account_id, "user", parsed["sender_id"]):
            return True
        if parsed["item_id"] and await self.blocks.is_blocked(account_id, "item", parsed["item_id"]):
            return True
        text = parsed["text"]
        if text:
            entries = await self.blocks.list_by_account(account_id)
            for entry in entries:
                if entry.kind == "keyword" and entry.value in text:
                    return True
        return False

    async def _decide_reply(
        self, account_id: int, chat: Chat, text: str
    ) -> tuple[str, str] | None:
        """AI 优先；未启用或调用失败时回退关键词与默认话术。"""
        if self.ai is not None:
            ai_reply = await self.ai.generate(account_id, chat.chat_id, text)
            if ai_reply.ok:
                await log_event(
                    self.db,
                    type="ai.reply",
                    account_id=account_id,
                    level="info",
                    detail={
                        "chat_id": chat.chat_id,
                        "latency_ms": ai_reply.latency_ms,
                        "prompt_tokens": ai_reply.prompt_tokens,
                        "completion_tokens": ai_reply.completion_tokens,
                    },
                )
                return ai_reply.text, "ai"
            await log_event(
                self.db,
                type="ai.fallback",
                account_id=account_id,
                level="warning",
                detail={
                    "chat_id": chat.chat_id,
                    "error": ai_reply.error,
                    "latency_ms": ai_reply.latency_ms,
                },
            )

        fallback = await self._match_reply(account_id, text)
        if fallback is None:
            return None
        return fallback, "auto"

    async def _match_reply(self, account_id: int, text: str) -> str | None:
        rules = await self.rules.list_active(account_id)
        for rule in rules:
            if rule.keyword and rule.keyword in text:
                return rule.reply
        config = await self.configs.get(account_id)
        return config.default_reply or None


def _is_expired(timestamp_ms: int, expiry_seconds: int) -> bool:
    return utc_now_ms() - timestamp_ms > expiry_seconds * 1000


def _within_debounce(chat: Chat, debounce_seconds: int) -> bool:
    if not chat.last_reply_at:
        return False
    try:
        last = datetime.fromisoformat(chat.last_reply_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    elapsed = (datetime.now(UTC) - last).total_seconds()
    return 0 <= elapsed < debounce_seconds

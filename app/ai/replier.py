from __future__ import annotations

import re
import time
from dataclasses import dataclass

from loguru import logger

from app.ai.client import AIError, ChatClient
from app.db.engine import Database
from app.db.repo.ai import AIConfig, AIConfigRepo, AIUsageRepo
from app.db.repo.messages import MessageRepo

MAX_REPLY_CHARS = 200
_MONEY = re.compile(r"(?:(?:¥|￥)\s*(\d+(?:\.\d+)?))|(?:(\d+(?:\.\d+)?)\s*(?:元|块))")


@dataclass(slots=True)
class AIReply:
    ok: bool
    text: str = ""
    error: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class AIReplyService:
    """组装上下文并调用 AI；任何失败都返回 ok=False，由管线降级。"""

    def __init__(self, db: Database, secret: str) -> None:
        self.db = db
        self.configs = AIConfigRepo(db, secret)
        self.usage = AIUsageRepo(db)
        self.messages = MessageRepo(db)

    async def generate(self, account_id: int, chat_id: str, incoming_text: str) -> AIReply:
        config = await self.configs.get(account_id)
        if not config.enabled:
            return AIReply(ok=False, error="disabled")

        api_key = await self.configs.get_api_key(account_id)
        if not api_key:
            return AIReply(ok=False, error="no_key")

        stored = await self.messages.list_by_chat(account_id, chat_id, limit=config.max_history)
        history = list(reversed(stored))
        messages = _build_messages(config, history, incoming_text)
        result = await self._call(account_id, chat_id, config, api_key, messages)
        if not result.ok:
            return result

        outcome = _enforce_floor(config, result.text)
        if outcome is not None:
            result.ok = False
            result.error = outcome
            await self.usage.record(
                account_id,
                chat_id=chat_id,
                status="below_floor",
                latency_ms=result.latency_ms,
                detail=result.text[:200],
            )
        return result

    async def test(self, account_id: int, text: str) -> AIReply:
        config = await self.configs.get(account_id)
        api_key = await self.configs.get_api_key(account_id)
        if not api_key:
            return AIReply(ok=False, error="no_key")
        messages = _build_messages(config, [], text)
        return await self._call(account_id, "", config, api_key, messages)

    async def _call(
        self,
        account_id: int,
        chat_id: str,
        config: AIConfig,
        api_key: str,
        messages: list[dict[str, str]],
    ) -> AIReply:
        client = ChatClient(
            base_url=config.base_url,
            api_key=api_key,
            model=config.model,
            timeout=float(config.timeout_seconds),
        )
        started = time.monotonic()
        try:
            result = await client.complete(messages)
        except AIError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning(f"账号 {account_id} AI 调用失败({exc.kind}): {exc}")
            await self.usage.record(
                account_id,
                chat_id=chat_id,
                status=exc.kind,
                latency_ms=latency_ms,
                detail=str(exc)[:300],
            )
            return AIReply(ok=False, error=exc.kind, latency_ms=latency_ms)

        latency_ms = int((time.monotonic() - started) * 1000)
        text = result.text.strip()[:MAX_REPLY_CHARS]
        if not text:
            await self.usage.record(
                account_id, chat_id=chat_id, status="empty", latency_ms=latency_ms
            )
            return AIReply(
                ok=False,
                error="empty",
                latency_ms=latency_ms,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )

        await self.usage.record(
            account_id,
            chat_id=chat_id,
            status="ok",
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        return AIReply(
            ok=True,
            text=text,
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )


def _build_messages(
    config: AIConfig, history: list[object], incoming_text: str
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": _system_prompt(config)}]
    for item in history:
        text = str(getattr(item, "text", "") or "").strip()
        kind = str(getattr(item, "kind", ""))
        if not text or kind != "text":
            continue
        role = "user" if getattr(item, "direction", "in") == "in" else "assistant"
        messages.append({"role": role, "content": text[:500]})

    if messages[-1]["role"] != "user" and incoming_text:
        messages.append({"role": "user", "content": incoming_text[:500]})
    return messages


def _system_prompt(config: AIConfig) -> str:
    parts = [
        "你是一名闲鱼二手交易店铺的客服，用中文替卖家回复买家。",
        "规则：",
        "1. 回复简短自然，1-3 句话，不超过 60 字，不要使用 Markdown 或列表。",
        "2. 热情但不过度承诺；不确定的信息请买家稍等，人工会尽快回复。",
        "3. 不承诺具体发货时间，不引导站外交易，不索要手机号、微信等隐私信息。",
        "4. 买家询问商品细节时只依据下方商品信息回答，不要编造参数或库存。",
    ]
    if config.price_floor_cents > 0:
        floor = config.price_floor_cents / 100
        parts.append(f"5. 议价时报价不得低于 {floor:.2f} 元，可以在此价格之上小幅让步。")
    if config.persona.strip():
        parts.append(f"店铺信息：{config.persona.strip()}")
    if config.product_info.strip():
        parts.append(f"商品信息：{config.product_info.strip()}")
    return "\n".join(parts)


def _enforce_floor(config: AIConfig, text: str) -> str | None:
    """回复中出现低于底价的金额时不外发，交给关键词/默认话术兜底。"""
    if config.price_floor_cents <= 0:
        return None
    floor_yuan = config.price_floor_cents / 100
    for match in _MONEY.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            continue
        if amount < floor_yuan:
            return "below_floor"
    return None

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from loguru import logger

from app.ai.client import AIError, ChatClient
from app.ai.items import ItemInfoService
from app.db.engine import Database
from app.db.repo.ai import AIConfig, AIConfigRepo, AIUsageRepo
from app.db.repo.messages import MessageRepo
from app.protocol.items import ItemInfo

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

    def __init__(self, db: Database, secret: str, items: ItemInfoService | None = None) -> None:
        self.db = db
        self.configs = AIConfigRepo(db, secret)
        self.usage = AIUsageRepo(db)
        self.messages = MessageRepo(db)
        self.items = items

    async def generate(
        self, account_id: int, chat_id: str, incoming_text: str, item_id: str = ""
    ) -> AIReply:
        config = await self.configs.get(account_id)
        if not config.enabled:
            return AIReply(ok=False, error="disabled")

        api_key = await self.configs.get_api_key(account_id)
        if not api_key:
            return AIReply(ok=False, error="no_key")

        item = await self.items.get(account_id, item_id) if self.items and item_id else None
        stored = await self.messages.list_by_chat(account_id, chat_id, limit=config.max_history)
        history = list(reversed(stored))
        messages = _build_messages(config, history, incoming_text, item)
        result = await self._call(account_id, chat_id, config, api_key, messages)
        if not result.ok:
            return result

        outcome = _enforce_floor(config, result.text, item.price_cents if item else 0)
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
    config: AIConfig,
    history: list[object],
    incoming_text: str,
    item: ItemInfo | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _system_prompt(config, item)}
    ]
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


def _system_prompt(config: AIConfig, item: ItemInfo | None = None) -> str:
    parts = [
        "你是一名闲鱼二手交易店铺的客服，用中文替卖家回复买家。",
        "规则：",
        "1. 回复简短自然，1-3 句话，不超过 60 字，不要使用 Markdown 或列表。",
        "2. 每条回复尽量推进对话：回答后追问一个具体问题（如买家心理价位、使用需求），不要空泛应付。",
        "3. 买家问商品是什么、包含什么、怎么发货时，用商品信息里的标题和描述简要回答 1-2 句，"
        "不要整段复述。",
        "4. 只依据下方商品信息回答，禁止编造参数、成色、库存、版本或服务承诺；"
        "商品信息里没有的内容不要猜测，回复「这个我确认一下」。",
        "5. 不知道的其他信息请买家稍等，由人工确认；不承诺具体发货时间，不引导站外交易，"
        "不索要手机号、微信等隐私信息。",
        "6. 不使用「亲」等客服腔，按卖家人设自然说话，语气友好但不卑微。",
    ]
    listing = config.listing_price_cents or (item.price_cents if item else 0)
    listing_yuan = listing / 100
    floor = config.price_floor_cents / 100
    if listing_yuan > 0:
        parts.append(f"7. 商品标价：{listing_yuan:g} 元，这是买家当前看到的价格。")
    if listing_yuan > 0 and listing_yuan <= 1:
        parts.append(
            "8. 标价已极低，不存在议价空间：礼貌说明已是标价、不议价，"
            "不要提供折扣、包邮或让价。不要邀请买家还价。"
        )
    elif floor > 0 and floor < listing_yuan:
        parts.append(
            f"8. 议价空间：最低可接受 {floor:g} 元，最高不超过标价 {listing_yuan:g} 元；"
            f"买家砍价时可小幅让步，但不得低于 {floor:g} 元。"
        )
    elif floor > 0:
        parts.append("8. 没有让价空间：礼貌说明已是底价，不要提供折扣或让价。")
    else:
        parts.append(
            "8. 未知底价：不要主动议价或承诺让价/包邮；"
            "买家砍价时说明需要和卖家确认。"
        )

    info_lines: list[str] = []
    if item is not None:
        info_lines.append(f"商品标题：{item.title}")
        if item.description:
            info_lines.append(f"商品描述：{item.description}")
        info_lines.append(f"商品状态：{item.status}")
    if config.product_info.strip():
        info_lines.append(f"卖家补充：{config.product_info.strip()}")
    if info_lines:
        parts.append("商品信息（回答依据，以此为准；未列出的内容视为不知道）：")
        parts.extend(info_lines)
    else:
        parts.append("商品信息：暂无。不要描述商品细节，也不要评论价格高低。")
    if config.persona.strip():
        parts.append(f"卖家/店铺信息：{config.persona.strip()}")
    return "\n".join(parts)


def _enforce_floor(config: AIConfig, text: str, item_price_cents: int = 0) -> str | None:
    """拦截越界回复：低于可接受价的报价，以及无任何价格信息时的金额承诺。"""
    amounts = []
    for match in _MONEY.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            amounts.append(float(raw))
        except (TypeError, ValueError):
            continue

    effective_floor = (
        config.price_floor_cents or config.listing_price_cents or item_price_cents
    )
    if effective_floor <= 0:
        return "no_floor_price" if amounts else None

    floor_yuan = effective_floor / 100
    if any(amount < floor_yuan for amount in amounts):
        return "below_floor"
    return None

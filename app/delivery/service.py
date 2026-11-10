from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.ai.items import ItemInfoService
from app.core.timeutil import utc_now_iso
from app.db.engine import Database
from app.db.repo.accounts import Account, AccountRepo
from app.db.repo.chats import Chat, ChatRepo
from app.db.repo.delivery import (
    CardRepo,
    CardSecret,
    CardSecretRepo,
    DeliveryRuleRepo,
    Order,
    OrderRepo,
    OutboxRepo,
    OutboxTask,
)
from app.db.repo.events import log_event
from app.db.repo.items import ItemDelivery, ItemDeliveryRepo, ItemRepo
from app.db.repo.messages import MessageRepo
from app.db.repo.ops import AccountOpsRepo
from app.db.repo.rules import BlockRepo
from app.protocol.mtop import CookieJar
from app.protocol.trade import consign_dummy

if TYPE_CHECKING:
    from app.pipeline.engine import OutboundSender

PAID_MARKERS = ("我已付款", "买家已付款", "等待你发货", "记得及时发货")
REMINDER_MARKERS = ("记得及时发货",)
PENDING_MARKERS = ("我已拍下", "买家已拍下", "待付款")
SHIPPED_MARKERS = ("我已发货", "你已发货", "买家已发货", "查看发货凭证")
OTHER_NOTICES = ("订单即将自动确认收货", "蚂蚁森林能量")

DEFAULT_TEMPLATE = "已收到付款，卡密如下：\n{secret}\n按说明使用，有问题随时找我。"
FLOWER_TEXT = "如果这次交易对你有帮助，方便的话请送一朵小红花，谢谢～"
FLOWER_DELAY_SECONDS = 1800
RECONCILE_GRACE_SECONDS = 600
SYNTHETIC_DEDUP_SECONDS = 120
MAX_ATTEMPTS = 6


class DeliveryRetry(RuntimeError):
    """可重试失败：发送失败、账号离线等。"""


class DeliveryPermanent(RuntimeError):
    """需人工处理：未绑定卡券、库存不足。"""


def detect_order_event(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if any(marker in value for marker in PAID_MARKERS):
        return "paid"
    if any(marker in value for marker in SHIPPED_MARKERS):
        return "shipped"
    if any(marker in value for marker in PENDING_MARKERS):
        return "pending"
    return ""


def is_system_notice(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if value.startswith("[") and 1 < value.find("]") <= 12:
        return True
    if detect_order_event(value):
        return True
    return any(marker in value for marker in OTHER_NOTICES)


def is_bracketed_notice(text: str) -> bool:
    """整条被方括号包裹才算闲鱼系统通知；买家手打文本不会命中。"""
    value = (text or "").strip()
    return len(value) >= 3 and value.startswith("[") and value.endswith("]")


def is_payment_reminder(text: str) -> bool:
    value = (text or "").strip()
    return any(marker in value for marker in REMINDER_MARKERS)


_ORDER_KEYS = ("orderId", "bizOrderId", "orderNo", "tradeOrderId", "mainOrderId")


def extract_order_no(raw: Any, depth: int = 0) -> str:
    if depth > 8:
        return ""
    if isinstance(raw, dict):
        for key in _ORDER_KEYS:
            value = raw.get(key)
            if isinstance(value, (str, int)) and str(value).isdigit() and len(str(value)) >= 8:
                return str(value)
        for value in raw.values():
            found = extract_order_no(value, depth + 1)
            if found:
                return found
    elif isinstance(raw, list):
        for value in raw:
            found = extract_order_no(value, depth + 1)
            if found:
                return found
    return ""


def is_real_order_no(order_no: str) -> bool:
    return order_no.isdigit() and len(order_no) >= 10


def delivery_key(account_id: int, order_no: str) -> str:
    return f"deliver:{account_id}:{order_no}"


def flower_key(account_id: int, order_no: str) -> str:
    return f"flower:{account_id}:{order_no}"


def render_text(template: str, order: Order, secret: str = "") -> str:
    values = {
        "{secret}": secret,
        "{order_no}": order.order_no,
        "{item_title}": order.title,
        "{buyer}": order.buyer_id,
        "{chat_id}": order.chat_id,
        "{price}": f"¥{order.amount_cents / 100:.2f}" if order.amount_cents else "",
    }
    text = template
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def _iso_from_ms(timestamp_ms: int) -> str:
    if not timestamp_ms:
        return utc_now_iso()
    moment = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _paid_within(paid_at: str, timestamp_ms: int, window: int = SYNTHETIC_DEDUP_SECONDS) -> bool:
    if not timestamp_ms:
        return False
    moment = _parse_iso(paid_at)
    if moment is None:
        return False
    return abs(moment.timestamp() - timestamp_ms / 1000) <= window


@dataclass(slots=True)
class DeliveryOutcome:
    ok: bool
    status: str
    detail: str = ""


class DeliveryService:
    """订单事件 → 卡密领取 → outbox 发送 → 虚拟发货标记。"""

    def __init__(
        self,
        db: Database,
        accounts: AccountRepo,
        items: ItemInfoService | None = None,
    ) -> None:
        self.db = db
        self.accounts = accounts
        self.items = items
        self.cards = CardRepo(db)
        self.secrets = CardSecretRepo(db)
        self.orders = OrderRepo(db)
        self.rules = DeliveryRuleRepo(db)
        self.outbox = OutboxRepo(db)
        self.messages = MessageRepo(db)
        self.catalog = ItemRepo(db)
        self.item_deliveries = ItemDeliveryRepo(db)
        self.ops = AccountOpsRepo(db)
        self.chats = ChatRepo(db)
        self.blocks = BlockRepo(db)

    async def reset(self) -> None:
        """进程重启后回收中断的发送：outbox 重排、订单回 paid、卡密解锁。"""
        await self.outbox.reset_sending()
        await self.db.write(
            "UPDATE orders SET status = 'paid', detail = '程序重启，重新排队', updated_at = ? "
            "WHERE status = 'delivering'",
            (utc_now_iso(),),
        )
        await self.db.write(
            "UPDATE card_secret SET status = 'available', order_no = '', claimed_at = NULL "
            "WHERE status = 'claimed'"
        )

    async def observe(self, account: Account, chat: Chat, parsed: dict[str, Any]) -> str:
        event = detect_order_event(parsed["text"])
        if event not in ("paid", "shipped"):
            return event

        raw_order_no = extract_order_no(parsed.get("raw"))
        if not is_bracketed_notice(parsed["text"]) and not is_real_order_no(raw_order_no):
            await log_event(
                self.db,
                type="order.untrusted_notice",
                account_id=account.id,
                level="warning",
                detail={
                    "chat_id": chat.chat_id,
                    "event": event,
                    "text": parsed["text"][:200],
                },
            )
            logger.warning(f"账号 {account.id} 忽略非系统通知且无真实订单号的{event}文本")
            return ""

        if event == "shipped":
            await self._mark_shipped(account.id, chat.chat_id)
            return event

        order_no = raw_order_no or f"{chat.chat_id}-{parsed['timestamp_ms']}"
        existing = await self.orders.get_by_no(account.id, order_no)
        if existing is not None:
            return event

        if not raw_order_no:
            if is_payment_reminder(parsed["text"]):
                if await self.orders.latest_for_chat(account.id, chat.chat_id) is not None:
                    return event
            else:
                item_id = parsed["item_id"] or chat.item_id
                latest = await self.orders.latest_for_chat(
                    account.id, chat.chat_id, item_id=item_id
                )
                if latest is not None and _paid_within(latest.paid_at, parsed["timestamp_ms"]):
                    return event

        title = await self._item_title(account.id, parsed["item_id"] or chat.item_id)
        order = await self.orders.create_if_absent(
            account.id,
            order_no=order_no,
            chat_id=chat.chat_id,
            buyer_id=chat.peer_id,
            item_id=parsed["item_id"] or chat.item_id,
            title=title,
            paid_at=_iso_from_ms(parsed["timestamp_ms"]),
        )
        if order is None:
            return event
        await self.outbox.enqueue(
            account.id,
            dedup_key=delivery_key(account.id, order_no),
            task="delivery",
            payload={"order_id": order.id},
        )
        await log_event(
            self.db,
            type="order.paid",
            account_id=account.id,
            level="info",
            detail={"order_no": order_no, "chat_id": chat.chat_id, "item_id": parsed["item_id"]},
        )
        return event

    async def execute(self, task: OutboxTask, sender: OutboundSender) -> DeliveryOutcome:
        order_id = int(task.payload.get("order_id") or 0)
        order = await self.orders.get_by_id(order_id)
        if order is None:
            raise DeliveryPermanent("订单不存在")
        if order.status == "delivered":
            return DeliveryOutcome(ok=True, status="delivered")
        if order.status == "manual":
            raise DeliveryPermanent("订单已转人工")
        if not await self.orders.start_delivery(order.id):
            return DeliveryOutcome(ok=True, status="skipped")

        config = await self.item_deliveries.get(order.account_id, order.item_id)
        if config is not None:
            if not config.enabled:
                await self._manual(order, "商品已关闭自动发货")
                raise DeliveryPermanent("商品已关闭自动发货")
            return await self._execute_item(order, config, sender)
        return await self._execute_legacy(order, sender)

    async def _execute_item(
        self, order: Order, config: ItemDelivery, sender: OutboundSender
    ) -> DeliveryOutcome:
        if config.mode == "manual":
            await self._manual(order, "商品已设为人工发货")
            raise DeliveryPermanent("商品已设为人工发货")
        if config.mode == "fixed":
            text = render_text(config.content, order)
            if not text.strip():
                await self._manual(order, "固定发货内容为空")
                raise DeliveryPermanent("固定发货内容为空")
            return await self._send(
                order, text, sender, secret=None, auto_consign=config.auto_consign
            )

        card_id = config.card_id
        if card_id is None:
            await self._manual(order, "商品未选择卡密池")
            raise DeliveryPermanent("商品未选择卡密池")
        card = await self.cards.get(card_id)
        if card is None:
            await self._manual(order, "卡密池不存在")
            raise DeliveryPermanent("卡密池不存在")
        if not card.enabled:
            await self._manual(order, "卡密池已停用")
            raise DeliveryPermanent("卡密池已停用")
        return await self._deliver_card(
            order,
            card_id,
            config.template or DEFAULT_TEMPLATE,
            sender,
            auto_consign=config.auto_consign,
        )

    async def _execute_legacy(self, order: Order, sender: OutboundSender) -> DeliveryOutcome:
        card_id = await self.cards.card_for_item(order.account_id, order.item_id)
        rule = await self.rules.template_for(order.account_id, order.item_id)
        if card_id is None or rule is None:
            await self._manual(order, "商品未配置自动发货")
            raise DeliveryPermanent("商品未配置自动发货")
        return await self._deliver_card(order, card_id, rule.template, sender, auto_consign=True)

    async def _deliver_card(
        self,
        order: Order,
        card_id: int,
        template: str,
        sender: OutboundSender,
        *,
        auto_consign: bool,
    ) -> DeliveryOutcome:
        secret = await self.secrets.claim(card_id, order.order_no)
        if secret is None:
            await self._manual(order, "卡密库存不足")
            raise DeliveryPermanent("卡密库存不足")
        text = render_text(template, order, secret.secret)
        return await self._send(order, text, sender, secret=secret, auto_consign=auto_consign)

    async def _send(
        self,
        order: Order,
        text: str,
        sender: OutboundSender,
        *,
        secret: CardSecret | None,
        auto_consign: bool,
    ) -> DeliveryOutcome:
        message_id = await self.messages.insert_outbound(
            order.account_id,
            chat_id=order.chat_id,
            dedup_key=f"delivery:{order.order_no}",
            kind="text",
            text=text,
            item_id=order.item_id,
            status="pending",
        )
        result = await sender.send_ws(order.chat_id, order.buyer_id or order.chat_id, text)
        if not result.ok:
            if secret is not None:
                await self.secrets.release(secret.id)
            await self.messages.set_status(message_id, "failed", msg_id=result.msg_id)
            await self.orders.set_status(order.id, "paid", detail=f"发送失败：{result.reason}")
            raise DeliveryRetry(result.reason or "send_failed")

        await self.messages.set_status(message_id, "sent", msg_id=result.msg_id)
        secret_id = None
        if secret is not None:
            await self.secrets.mark_used(secret.id)
            secret_id = secret.id
        await self.orders.set_status(
            order.id, "delivered", card_secret_id=secret_id, delivered=True
        )
        await log_event(
            self.db,
            type="order.delivered",
            account_id=order.account_id,
            level="info",
            detail={"order_no": order.order_no, "chat_id": order.chat_id},
        )
        await self._request_flower(order)
        if auto_consign:
            await self._consign(order)
        return DeliveryOutcome(ok=True, status="delivered")

    async def _request_flower(self, order: Order) -> None:
        ops = await self.ops.get(order.account_id)
        if not ops.flower_enabled:
            return
        queued = await self.outbox.enqueue(
            order.account_id,
            dedup_key=flower_key(order.account_id, order.order_no),
            task="flower",
            payload={"order_id": order.id},
            delay_seconds=FLOWER_DELAY_SECONDS,
        )
        if queued:
            await self.orders.set_flower(order.id, "pending")

    async def execute_flower(self, task: OutboxTask, sender: OutboundSender) -> DeliveryOutcome:
        order_id = int(task.payload.get("order_id") or 0)
        order = await self.orders.get_by_id(order_id)
        if order is None or order.flower_status == "sent":
            return DeliveryOutcome(ok=True, status="skipped")
        ops = await self.ops.get(order.account_id)
        if not ops.flower_enabled:
            await self.orders.set_flower(order.id, "skipped", error="功能已关闭")
            return DeliveryOutcome(ok=True, status="skipped")
        if order.status != "delivered":
            await self.orders.set_flower(order.id, "skipped", error="订单未发货")
            return DeliveryOutcome(ok=True, status="skipped")
        chat = await self.chats.get(order.account_id, order.chat_id)
        if chat is not None and chat.reply_paused:
            await self.orders.set_flower(order.id, "skipped", error="会话已暂停")
            return DeliveryOutcome(ok=True, status="skipped")
        buyer = order.buyer_id or order.chat_id
        if await self.blocks.is_blocked(order.account_id, "user", buyer):
            await self.orders.set_flower(order.id, "skipped", error="买家已拉黑")
            return DeliveryOutcome(ok=True, status="skipped")

        message_id = await self.messages.insert_outbound(
            order.account_id,
            chat_id=order.chat_id,
            dedup_key=flower_key(order.account_id, order.order_no),
            kind="text",
            text=FLOWER_TEXT,
            item_id=order.item_id,
            status="pending",
        )
        result = await sender.send_ws(order.chat_id, buyer, FLOWER_TEXT)
        if not result.ok:
            await self.messages.set_status(message_id, "failed", msg_id=result.msg_id)
            raise DeliveryRetry(result.reason or "send_failed")
        await self.messages.set_status(message_id, "sent", msg_id=result.msg_id)
        await self.orders.set_flower(order.id, "sent", sent=True)
        return DeliveryOutcome(ok=True, status="sent")

    async def flower_failed(self, task: OutboxTask, error: str) -> None:
        """小红花任务超限失败：只更新订单小红花列，不动发货状态。"""
        order_id = int(task.payload.get("order_id") or 0)
        if order_id:
            await self.orders.set_flower(order_id, "failed", error=error)
        await self.outbox.dead(task.id, error)

    async def redeliver(self, order_id: int) -> bool:
        order = await self.orders.get_by_id(order_id)
        if order is None:
            return False
        await self.orders.set_status(order.id, "paid", detail="")
        key = delivery_key(order.account_id, order.order_no)
        if not await self.outbox.requeue(key):
            await self.outbox.enqueue(
                order.account_id,
                dedup_key=key,
                task="delivery",
                payload={"order_id": order.id},
            )
        return True

    async def mark_delivered(self, order_id: int) -> bool:
        order = await self.orders.get_by_id(order_id)
        if order is None:
            return False
        await self.orders.set_status(order.id, "delivered", detail="人工标记已发货", delivered=True)
        return True

    async def consign(self, order_id: int) -> tuple[bool, str]:
        order = await self.orders.get_by_id(order_id)
        if order is None:
            return False, "订单不存在"
        return await self._consign(order)

    async def fail_permanently(self, task: OutboxTask, error: str) -> None:
        order_id = int(task.payload.get("order_id") or 0)
        order = await self.orders.get_by_id(order_id)
        if order is not None and order.status != "delivered":
            await self._manual(order, error)
        await self.outbox.dead(task.id, error)

    async def reconcile(self, account_id: int, *, grace_seconds: int = RECONCILE_GRACE_SECONDS) -> int:
        """补录漏掉的付款通知；过旧的消息只登记待人工确认。"""
        rows = await self.db.fetch_all(
            "SELECT id, chat_id, sender_id, item_id, text, timestamp_ms, raw_json, created_at "
            "FROM message WHERE account_id = ? AND direction = 'in' AND kind = 'text' "
            "AND text != '' ORDER BY id",
            (account_id,),
        )
        accounts = {a.id: a for a in await self.accounts.list_all()}
        account = accounts.get(account_id)
        if account is None:
            return 0

        created = 0
        now = datetime.now(UTC)
        for row in rows:
            text = str(row["text"])
            if detect_order_event(text) != "paid":
                continue
            raw_order_no = extract_order_no(_loads(row["raw_json"]))
            if not is_bracketed_notice(text) and not is_real_order_no(raw_order_no):
                continue
            order_no = raw_order_no or f"{row['chat_id']}-{int(row['timestamp_ms'])}"
            if await self.orders.get_by_no(account_id, order_no) is not None:
                continue
            chat_row = await self.db.fetch_one(
                "SELECT peer_id, item_id FROM chat WHERE account_id = ? AND chat_id = ?",
                (account_id, row["chat_id"]),
            )
            item_id = str(row["item_id"] or (chat_row["item_id"] if chat_row else "") or "")
            if not raw_order_no:
                chat_id = str(row["chat_id"])
                if is_payment_reminder(text):
                    if await self.orders.latest_for_chat(account_id, chat_id) is not None:
                        continue
                else:
                    latest = await self.orders.latest_for_chat(
                        account_id, chat_id, item_id=item_id
                    )
                    if latest is not None and _paid_within(
                        latest.paid_at, int(row["timestamp_ms"])
                    ):
                        continue
            created_at = _parse_iso(str(row["created_at"]))
            stale = created_at is not None and (now - created_at).total_seconds() > grace_seconds
            order = await self.orders.create_if_absent(
                account_id,
                order_no=order_no,
                chat_id=str(row["chat_id"]),
                buyer_id=str(chat_row["peer_id"] if chat_row else ""),
                item_id=item_id,
                paid_at=_iso_from_ms(int(row["timestamp_ms"])),
            )
            if order is None:
                continue
            created += 1
            if stale:
                await self._manual(order, "补录订单，需人工确认后重发")
                continue
            await self.outbox.enqueue(
                account_id,
                dedup_key=delivery_key(account_id, order_no),
                task="delivery",
                payload={"order_id": order.id},
            )
        if created:
            await log_event(
                self.db,
                type="order.reconciled",
                account_id=account_id,
                level="info",
                detail={"created": created},
            )
        return created

    async def _mark_shipped(self, account_id: int, chat_id: str) -> None:
        row = await self.db.fetch_one(
            "SELECT * FROM orders WHERE account_id = ? AND chat_id = ? "
            "AND status != 'delivered' ORDER BY id DESC LIMIT 1",
            (account_id, chat_id),
        )
        if row is None:
            return
        order = Order.from_row(row)
        await self.orders.set_status(order.id, "delivered", detail="闲鱼已标记发货", delivered=True)

    async def _manual(self, order: Order, detail: str) -> None:
        await self.orders.set_status(order.id, "manual", detail=detail)
        await log_event(
            self.db,
            type="order.manual",
            account_id=order.account_id,
            level="warning",
            detail={"order_no": order.order_no, "reason": detail},
        )

    async def _consign(self, order: Order) -> tuple[bool, str]:
        if not is_real_order_no(order.order_no):
            return False, "无真实订单号，跳过自动发货标记"
        cookie = await self.accounts.load_cookie(order.account_id)
        if not cookie:
            await self._consign_failed(order, "缺少 Cookie")
            return False, "缺少 Cookie"
        account = await self.accounts.get(order.account_id)
        try:
            await consign_dummy(
                CookieJar(cookie),
                order.order_no,
                proxy=account.proxy_url if account is not None else "",
            )
        except Exception as exc:
            await self._consign_failed(order, str(exc))
            return False, str(exc)
        return True, ""

    async def _consign_failed(self, order: Order, error: str) -> None:
        logger.warning(f"订单 {order.order_no} 自动发货标记失败: {error}")
        await log_event(
            self.db,
            type="order.consign_failed",
            account_id=order.account_id,
            level="warning",
            detail={"order_no": order.order_no, "error": error},
        )

    async def _item_title(self, account_id: int, item_id: str) -> str:
        if not item_id:
            return ""
        cached = await self.catalog.get(account_id, item_id)
        if cached is not None and cached.title:
            return cached.title
        if self.items is None:
            return ""
        info = await self.items.get(account_id, item_id)
        if info is None:
            return ""
        await self.catalog.ensure(
            account_id, item_id, title=info.title, price_cents=info.price_cents, status=info.status
        )
        return info.title


def _loads(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.protocol.mtop import CookieJar, MtopError, get_m_h5_tk, mtop_call

CONSIGN_API = "mtop.taobao.idle.logistic.consign.dummy"
SOLD_API = "mtop.taobao.idle.trade.merchant.sold.get"

_BEIJING = timezone(timedelta(hours=8))

# 闲鱼订单状态 → 本地处理语义：paid 未发货、delivered 已发货、skip 不建单
_STATUS_MAP = {
    "待付款": "skip",
    "待发货": "paid",
    "已发货": "delivered",
    "交易成功": "delivered",
    "交易关闭": "skip",
    "退款中": "skip",
    "退款成功": "skip",
    "已退款": "skip",
    "退款关闭": "skip",
}


async def consign_dummy(jar: CookieJar, order_id: str, *, proxy: str = "") -> None:
    """虚拟商品无需物流发货。"""
    await get_m_h5_tk(jar, proxy=proxy)
    body = await mtop_call(
        jar,
        CONSIGN_API,
        "1.0",
        {"orderId": order_id, "tradeText": "", "picList": [], "newUnconsign": True},
        proxy=proxy,
    )
    ret = " ".join(str(item) for item in (body.get("ret") or []))
    if "SUCCESS::" not in ret:
        raise MtopError(ret or "发货标记失败")


@dataclass(slots=True)
class SoldOrder:
    order_no: str
    item_id: str
    buyer_id: str
    buyer_nick: str
    amount_cents: int
    created_at: str
    state: str


@dataclass(slots=True)
class SoldOrderPage:
    orders: list[SoldOrder] = field(default_factory=list)
    next_page: bool = False
    total_count: int = 0


def parse_sold_order(item: dict[str, Any]) -> SoldOrder | None:
    common = item.get("commonData") or {}
    order_no = str(common.get("orderId") or "").strip()
    if not order_no:
        return None
    if str(common.get("inRefund") or "").lower() == "true":
        state = "skip"
    else:
        state = _STATUS_MAP.get(str(common.get("orderStatus") or ""), "skip")
    price = (item.get("priceVO") or {}).get("totalPrice") or "0"
    try:
        amount_cents = int(round(float(price) * 100))
    except (TypeError, ValueError):
        amount_cents = 0
    created_at = ""
    raw_created = str(common.get("createTime") or "")
    if raw_created:
        try:
            created_at = (
                datetime.strptime(raw_created, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=_BEIJING)
                .astimezone(UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        except ValueError:
            created_at = ""
    buyer = item.get("buyerInfoVO") or {}
    return SoldOrder(
        order_no=order_no,
        item_id=str(common.get("itemId") or ""),
        buyer_id=str(buyer.get("buyerId") or ""),
        buyer_nick=str(buyer.get("userNick") or ""),
        amount_cents=amount_cents,
        created_at=created_at,
        state=state,
    )


async def fetch_sold_orders_page(
    jar: CookieJar,
    *,
    page: int = 1,
    rows_per_page: int = 30,
    query_code: str = "ALL",
    proxy: str = "",
) -> SoldOrderPage:
    await get_m_h5_tk(jar, proxy=proxy)
    body = await mtop_call(
        jar,
        SOLD_API,
        "1.0",
        {
            "pageNumber": page,
            "rowsPerPage": rows_per_page,
            "orderIds": "",
            "queryCode": query_code,
            "orderSearchParam": "{}",
        },
        proxy=proxy,
        referer="https://seller.goofish.com/",
        origin="https://seller.goofish.com",
    )
    ret = " ".join(str(item) for item in (body.get("ret") or []))
    if "SUCCESS::" not in ret:
        raise MtopError(ret or "获取闲鱼订单列表失败")
    module = (body.get("data") or {}).get("module") or {}
    orders = [order for order in map(parse_sold_order, module.get("items") or []) if order]
    return SoldOrderPage(
        orders=orders,
        next_page=str(module.get("nextPage")).lower() == "true",
        total_count=int(module.get("totalCount") or 0),
    )
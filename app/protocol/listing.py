from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.protocol.mtop import CookieJar, MtopError, get_m_h5_tk, mtop_call

LIST_API = "mtop.idle.web.xyh.item.list"
LIST_PAGE_SIZE = 20
MAX_SYNC_PAGES = 20

STATUS_ON_SALE = "在售"
STATUS_SOLD = "已售出"
STATUS_OFFLINE = "已下架"
STATUS_UNKNOWN = "未知"

_STATUS_BY_CODE = {0: STATUS_ON_SALE, 1: STATUS_SOLD, 2: STATUS_OFFLINE}


@dataclass(frozen=True, slots=True)
class ListedItem:
    item_id: str
    title: str
    price_cents: int
    status: str
    cover_url: str


@dataclass(frozen=True, slots=True)
class ListingPage:
    items: list[ListedItem]
    next_page: bool


def _cents(value: Any) -> int:
    text = str(value or "").strip().replace("¥", "").replace(",", "")
    if not text:
        return 0
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return 0
    if amount < 0:
        return 0
    return int((amount * 100).quantize(Decimal("1")))


def _https(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("http://"):
        return "https://" + value[len("http://") :]
    return value


def parse_listing(data: dict[str, Any]) -> ListingPage:
    items: list[ListedItem] = []
    seen: set[str] = set()
    cards = data.get("cardList")
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        payload = card.get("cardData")
        if not isinstance(payload, dict):
            payload = card
        detail = payload.get("detailParams")
        if not isinstance(detail, dict):
            detail = {}
        item_id = str(payload.get("id") or detail.get("itemId") or "").strip()
        title = str(payload.get("title") or detail.get("title") or "").strip()
        if not item_id or not title or item_id in seen:
            continue
        seen.add(item_id)

        price_info = payload.get("priceInfo")
        price = _cents(price_info.get("price")) if isinstance(price_info, dict) else 0
        if price == 0:
            price = _cents(detail.get("soldPrice"))

        pic_info = payload.get("picInfo")
        cover = ""
        if isinstance(pic_info, dict):
            cover = str(pic_info.get("picUrl") or "")
        cover = _https(cover or str(detail.get("picUrl") or ""))

        code = payload.get("itemStatus")
        status = _STATUS_BY_CODE.get(code if isinstance(code, int) else -1, STATUS_UNKNOWN)
        items.append(
            ListedItem(
                item_id=item_id,
                title=title,
                price_cents=price,
                status=status,
                cover_url=cover,
            )
        )
    return ListingPage(items=items, next_page=bool(data.get("nextPage")))


async def fetch_listing_page(
    jar: CookieJar,
    user_id: str,
    page_number: int = 1,
    *,
    page_size: int = LIST_PAGE_SIZE,
    proxy: str = "",
) -> ListingPage:
    await get_m_h5_tk(jar, proxy=proxy)
    body = await mtop_call(
        jar,
        LIST_API,
        "1.0",
        {
            "needGroupInfo": True,
            "pageNumber": page_number,
            "userId": user_id,
            "pageSize": page_size,
        },
        proxy=proxy,
    )
    ret = " ".join(str(item) for item in (body.get("ret") or []))
    if "SUCCESS::" not in ret:
        raise MtopError(ret or "商品列表获取失败")
    data = body.get("data")
    if not isinstance(data, dict):
        return ListingPage(items=[], next_page=False)
    return parse_listing(data)

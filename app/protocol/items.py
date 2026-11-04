from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.protocol.mtop import CookieJar, MtopError, get_m_h5_tk, mtop_call

ITEM_INFO_API = "mtop.taobao.idle.item.info"
TITLE_LIMIT = 200
DESCRIPTION_LIMIT = 600


@dataclass(frozen=True, slots=True)
class ItemInfo:
    item_id: str
    title: str
    description: str
    price_cents: int
    status: str


def _cents(value: Any) -> int:
    if isinstance(value, dict):
        cent = value.get("cent")
        if isinstance(cent, (int, float)) and cent >= 0:
            return int(cent)
        value = value.get("amount")
    try:
        return max(0, int(round(float(value) * 100)))
    except (TypeError, ValueError):
        return 0


def parse_item_info(data: dict[str, Any]) -> ItemInfo | None:
    item_id = str(data.get("itemId") or "")
    title = str(data.get("title") or "").strip()
    if not item_id or not title:
        return None

    description = re.sub(r"\s+", " ", str(data.get("description") or "")).strip()
    price = _cents(data.get("reservePriceMoney")) or _cents(data.get("reservePrice"))
    if price == 0:
        price = _cents((data.get("attributeMap") or {}).get("firstPrice"))
    status = "在售" if data.get("online") is True and not data.get("pullOff") else "已下架"
    return ItemInfo(
        item_id=item_id,
        title=title[:TITLE_LIMIT],
        description=description[:DESCRIPTION_LIMIT],
        price_cents=price,
        status=status,
    )


async def fetch_item_info(jar: CookieJar, item_id: str, *, proxy: str = "") -> ItemInfo | None:
    await get_m_h5_tk(jar, proxy=proxy)
    body = await mtop_call(jar, ITEM_INFO_API, "1.0", {"itemId": item_id}, proxy=proxy)
    ret = " ".join(str(item) for item in (body.get("ret") or []))
    if "SUCCESS::" not in ret:
        raise MtopError(ret or "商品详情获取失败")
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    return parse_item_info(data)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.protocol.mtop import CookieJar, get_m_h5_tk, mtop_call

POLISH_API = "mtop.taobao.idle.item.polish"

_DONE_MARKERS = (
    "IDLEITEM_POLISH_AGAIN",
    "POLISH_DUPLICATE",
    "宝贝已经擦亮过了",
    "一天只能擦亮一次",
)
_OFFLINE_MARKERS = ("UNSUPPORTED_ITEM_STATUS", "已下架商品不支持该操作")


@dataclass(frozen=True, slots=True)
class PolishResult:
    ok: bool
    already: bool = False
    offline: bool = False
    message: str = ""


def parse_polish_response(body: dict[str, Any]) -> PolishResult:
    ret = " ".join(str(item) for item in (body.get("ret") or []))
    if "SUCCESS::" in ret:
        return PolishResult(ok=True, message="擦亮成功")
    if any(marker in ret for marker in _DONE_MARKERS):
        return PolishResult(ok=True, already=True, message="今天已擦亮")
    if any(marker in ret for marker in _OFFLINE_MARKERS):
        return PolishResult(ok=False, offline=True, message=ret or "商品已下架，无法擦亮")
    return PolishResult(ok=False, message=ret or "擦亮失败")


async def polish_item(jar: CookieJar, item_id: str, *, proxy: str = "") -> PolishResult:
    await get_m_h5_tk(jar, proxy=proxy)
    body = await mtop_call(
        jar,
        POLISH_API,
        "1.0",
        {"itemId": item_id},
        extra_params={"v": "2.0"},
        proxy=proxy,
    )
    return parse_polish_response(body)
from __future__ import annotations

from app.protocol.mtop import CookieJar, MtopError, get_m_h5_tk, mtop_call

CONSIGN_API = "mtop.taobao.idle.logistic.consign.dummy"


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

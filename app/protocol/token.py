from __future__ import annotations

from typing import Any

from app.protocol.mtop import IM_APP_KEY, CookieJar, mtop_call

TOKEN_API = "mtop.taobao.idlemessage.pc.login.token"


def extract_access_token(payload: dict[str, Any]) -> str | None:
    ret = payload.get("ret") or []
    if isinstance(ret, str):
        ret = [ret]
    if not any("SUCCESS::" in str(item) for item in ret):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    token = data.get("accessToken")
    return token if isinstance(token, str) and token else None


async def fetch_im_token(
    jar: CookieJar, device_id: str, *, timeout: float = 30.0, proxy: str = ""
) -> str | None:
    payload = await mtop_call(
        jar,
        TOKEN_API,
        "1.0",
        {"appKey": IM_APP_KEY, "deviceId": device_id},
        extra_params={
            "dangerouslySetWindvaneParams": "%5Bobject%20Object%5D",
            "smToken": "token",
            "queryToken": "sm",
            "sm": "sm",
            "spm_cnt": "a21ybx.im.0.0",
            "spm_pre": "a21ybx.home.sidebar.1.4c053da6vYwnmf",
            "log_id": "4c053da6vYwnmf",
        },
        timeout=timeout,
        proxy=proxy,
    )
    return extract_access_token(payload)

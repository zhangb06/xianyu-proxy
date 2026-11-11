from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

APP_KEY = "34839810"
IM_APP_KEY = "444e9908a51d1cb236a27862abc769c9"
H5_BASE = "https://h5api.m.goofish.com/h5"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TOKEN_ERRORS = (
    "FAIL_SYS_TOKEN_EXOIRED",
    "FAIL_SYS_TOKEN_EXPIRED",
    "FAIL_SYS_SESSION_EXPIRED",
    "FAIL_SYS_TOKEN_EMPTY",
    "令牌过期",
)


class MtopError(RuntimeError):
    pass


@dataclass
class CookieJar:
    cookies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: str | dict[str, str] | None) -> CookieJar:
        if isinstance(raw, dict):
            return cls({str(key): str(value) for key, value in raw.items()})
        return cls(parse_cookie_string(raw or ""))

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.cookies.get(name, default)

    def update(self, values: dict[str, str]) -> None:
        self.cookies.update(values)

    def merge_response(self, response: httpx.Response) -> None:
        self.update(parse_set_cookie_headers(response.headers.get_list("set-cookie")))
        self.update({str(key): str(value) for key, value in response.cookies.items()})

    def header(self) -> str:
        return "; ".join(f"{key}={value}" for key, value in self.cookies.items())

    def as_dict(self) -> dict[str, str]:
        return dict(self.cookies)


def parse_cookie_string(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def parse_set_cookie_headers(headers: list[str]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in headers:
        first = header.split(";", 1)[0]
        if "=" not in first:
            continue
        key, value = first.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def generate_sign(t: str, token: str, data: str, app_key: str = APP_KEY) -> str:
    return hashlib.md5(f"{token}&{t}&{app_key}&{data}".encode()).hexdigest()


def _headers(
    jar: CookieJar,
    referer: str = "https://www.goofish.com/",
    origin: str = "https://www.goofish.com",
) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": origin,
        "Referer": referer,
        "User-Agent": USER_AGENT,
        "Cookie": jar.header(),
    }


def _token_of(jar: CookieJar) -> str:
    return (jar.get("_m_h5_tk") or "").split("_", 1)[0]


async def mtop_call(
    jar: CookieJar,
    api: str,
    version: str,
    data: dict[str, Any],
    *,
    extra_params: dict[str, str] | None = None,
    method: str = "POST",
    timeout: float = 30.0,
    proxy: str = "",
    referer: str = "https://www.goofish.com/",
    origin: str = "https://www.goofish.com",
) -> dict[str, Any]:
    """调用 mtop 接口，令牌过期时重签一次。"""
    url = f"{H5_BASE}/{api}/{version}/"
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    last_error = ""

    for attempt in range(2):
        t = str(int(time.time() * 1000))
        params = {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": t,
            "sign": generate_sign(t, _token_of(jar), payload),
            "v": version,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.im.0.0",
        }
        if extra_params:
            params.update(extra_params)

        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, proxy=proxy or None
            ) as client:
                if method.upper() == "GET":
                    response = await client.get(
                        url, params={**params, "data": payload}, headers=_headers(jar, referer, origin)
                    )
                else:
                    response = await client.post(
                        url, params=params, data={"data": payload}, headers=_headers(jar, referer, origin)
                    )
        except httpx.HTTPError as exc:
            last_error = f"请求异常: {exc}"
            if attempt == 0:
                continue
            raise MtopError(last_error) from exc

        jar.merge_response(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise MtopError(f"{api} 返回非 JSON: {response.text[:200]}") from exc

        ret = body.get("ret") or [""]
        ret_text = " ".join(str(item) for item in ret)
        if "SUCCESS::" in ret_text:
            return body
        if attempt == 0 and any(marker in ret_text for marker in _TOKEN_ERRORS):
            continue
        return body

    raise MtopError(last_error or f"{api} 调用失败")


async def get_m_h5_tk(jar: CookieJar, *, timeout: float = 30.0, proxy: str = "") -> None:
    """访问一次 H5 页面以获取 _m_h5_tk。"""
    api = "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get"
    url = f"{H5_BASE}/{api}/1.0/"
    referer = "https://passport.goofish.com/"
    data_value = json.dumps({"bizScene": "home"}, separators=(",", ":"))

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=proxy or None) as client:
        first = await client.get(url, headers=_headers(jar, referer))
        jar.merge_response(first)

        t = str(int(time.time() * 1000))
        params = {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": t,
            "sign": generate_sign(t, _token_of(jar), data_value),
            "v": "1.0",
            "type": "originaljson",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "data": data_value,
        }
        second = await client.post(
            url, params=params, headers=_headers(jar, referer), cookies=jar.as_dict()
        )
        jar.merge_response(second)


def decode_payload(payload: str) -> dict[str, Any] | None:
    """base64 → JSON；失败再尝试 MessagePack。"""
    try:
        raw = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except (ValueError, TypeError):
        return None

    try:
        value = json.loads(raw.decode("utf-8"))
        if isinstance(value, dict):
            return value
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    try:
        import msgpack

        value = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    return _normalize_keys(value)


def _normalize_keys(value: Any) -> Any:
    """闲鱼 msgpack 载荷使用整数键，统一转为字符串键以匹配下游解析。"""
    if isinstance(value, dict):
        return {str(key): _normalize_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_keys(item) for item in value]
    return value

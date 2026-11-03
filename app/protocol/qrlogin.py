from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import qrcode
import qrcode.constants

from app.core.ids import generate_session_id
from app.protocol.mtop import CookieJar, get_m_h5_tk

PASSPORT = "https://passport.goofish.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_VIEW_DATA = re.compile(r"window\.viewData\s*=\s*(\{.*?\});", re.DOTALL)


class QRLoginError(RuntimeError):
    pass


@dataclass
class QRSession:
    session_id: str
    jar: CookieJar
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "waiting"
    png: bytes = b""
    code_content: str = ""
    verification_url: str | None = None
    unb: str | None = None


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://passport.goofish.com/",
        "Origin": "https://passport.goofish.com",
    }


def render_png(content: str) -> bytes:
    qr = qrcode.QRCode(
        version=5,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    buffer = BytesIO()
    qr.make_image().save(buffer, format="PNG")
    return buffer.getvalue()


class QRLogin:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.mini_login_url = f"{PASSPORT}/mini_login.htm"
        self.generate_url = f"{PASSPORT}/newlogin/qrcode/generate.do"
        self.query_url = f"{PASSPORT}/newlogin/qrcode/query.do"

    async def generate(self) -> QRSession:
        session = QRSession(session_id=generate_session_id(), jar=CookieJar())
        await get_m_h5_tk(session.jar, timeout=self.timeout)
        await self._load_form(session)

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                self.generate_url,
                params=session.params,
                headers=_headers(),
                cookies=session.jar.as_dict(),
            )
        session.jar.merge_response(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise QRLoginError(f"二维码接口返回异常: {response.text[:200]}") from exc

        content = body.get("content") or {}
        if content.get("success") is not True:
            raise QRLoginError(f"获取登录二维码失败: {body}")
        data = content.get("data") or {}
        session.params.update({"t": data.get("t"), "ck": data.get("ck")})
        session.code_content = str(data.get("codeContent") or "")
        if not session.code_content:
            raise QRLoginError("二维码内容为空")
        session.png = render_png(session.code_content)
        return session

    async def _load_form(self, session: QRSession) -> None:
        params = {
            "lang": "zh_cn",
            "appName": "xianyu",
            "appEntrance": "web",
            "styleType": "vertical",
            "bizParams": "",
            "notLoadSsoView": False,
            "notKeepLogin": False,
            "isMobile": False,
            "qrCodeFirst": False,
            "stie": 77,
            "rnd": random.random(),
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                self.mini_login_url,
                params=params,
                headers=_headers(),
                cookies=session.jar.as_dict(),
            )
        session.jar.merge_response(response)

        match = _VIEW_DATA.search(response.text)
        if not match:
            raise QRLoginError("获取登录参数失败")
        view_data = json.loads(match.group(1))
        form = view_data.get("loginFormData")
        if not form:
            raise QRLoginError("登录参数缺少 loginFormData")
        form["umidTag"] = "SERVER"
        session.params.update(form)

    async def poll_once(self, session: QRSession) -> str:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(
                self.query_url,
                data=session.params,
                headers=_headers(),
                cookies=session.jar.as_dict(),
            )
        session.jar.merge_response(response)

        try:
            body = response.json()
        except ValueError:
            return session.status
        data = (body.get("content") or {}).get("data") or {}
        status = data.get("qrCodeStatus")

        if status == "CONFIRMED":
            if data.get("iframeRedirect") is True:
                session.status = "verification_required"
                session.verification_url = data.get("iframeRedirectUrl")
            else:
                session.status = "success"
                session.unb = session.jar.get("unb")
        elif status == "SCANED":
            session.status = "scanned"
        elif status == "EXPIRED":
            session.status = "expired"
        return session.status

    async def wait(self, session: QRSession, *, timeout: float = 300.0, interval: float = 0.8) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.poll_once(session)
            if status in {"success", "expired", "verification_required"}:
                return status
            await asyncio.sleep(interval)
        session.status = "expired"
        return session.status


def save_png(session: QRSession, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(session.png)


def data_url(session: QRSession) -> str:
    return "data:image/png;base64," + base64.b64encode(session.png).decode("ascii")

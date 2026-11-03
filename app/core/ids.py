from __future__ import annotations

import secrets
import string
import time
import uuid

_DEVICE_CHARS = string.digits + string.ascii_uppercase + string.ascii_lowercase
_DASH_INDEXES = (8, 13, 18, 23)


def generate_mid() -> str:
    """LWP 请求标识：随机前缀 + 毫秒时间戳。"""
    return f"{secrets.randbelow(1000)}{int(time.time() * 1000)} 0"


def generate_uuid() -> str:
    return f"-{int(time.time() * 1000)}1"


def generate_device_id(user_id: str) -> str:
    """生成与闲鱼 IM 互通的设备 ID。

    36 位随机体，固定位置插入分隔符与版本位，末尾追加 "-{user_id}"。
    """
    chars: list[str] = []
    for index in range(36):
        if index in _DASH_INDEXES:
            chars.append("-")
        elif index == 14:
            chars.append("4")
        elif index == 19:
            chars.append(_DEVICE_CHARS[(secrets.randbelow(16) & 0x3) | 0x8])
        else:
            chars.append(_DEVICE_CHARS[secrets.randbelow(16)])
    return "".join(chars) + "-" + user_id


def generate_session_id() -> str:
    return uuid.uuid4().hex

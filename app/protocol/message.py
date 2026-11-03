from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

_CONTENT_KEYS = ("text", "image", "picUrl", "audio")


def is_sync_package(frame: Any) -> bool:
    try:
        data = frame["body"]["syncPushPackage"]["data"]
    except (KeyError, TypeError):
        return False
    return isinstance(data, list) and len(data) > 0


def is_chat_message(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    message_1 = message.get("1")
    if not isinstance(message_1, dict):
        return False
    message_10 = message_1.get("10")
    return isinstance(message_10, dict) and "reminderContent" in message_10


def decode_content(raw: Any) -> dict[str, Any] | None:
    """解析消息载荷：优先明文 JSON，其次 base64 编码的 JSON。"""
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip()
    if text.startswith("{"):
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    padded = raw + "=" * (-len(raw) % 4)
    try:
        value = json.loads(base64.b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def looks_like_content(value: Any) -> bool:
    return isinstance(value, dict) and (
        "contentType" in value or any(key in value for key in _CONTENT_KEYS)
    )


def decode_first_content(candidates: list[Any]) -> dict[str, Any] | None:
    for candidate in candidates:
        decoded = decode_content(candidate)
        if decoded is not None and looks_like_content(decoded):
            return decoded
    return None


def interpret_content(decoded: dict[str, Any]) -> tuple[str, list[str], str]:
    """返回 (文本, 图片列表, 类型)，类型为 text / image / audio / 空。"""
    if not isinstance(decoded, dict):
        return "", [], ""

    content_type = decoded.get("contentType", 0)
    if content_type == 1 and "text" in decoded:
        return _text_of(decoded["text"]), [], "text"
    if content_type == 2 and "image" in decoded:
        return "", _image_urls(decoded), "image"
    if content_type == 3 and "audio" in decoded:
        return "[语音消息]", [], "audio"

    if "text" in decoded:
        return _text_of(decoded["text"]), [], "text"
    if decoded.get("picUrl"):
        return "", [str(decoded["picUrl"])], "image"
    return "", [], ""


def _text_of(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return str(value)


def _image_urls(decoded: dict[str, Any]) -> list[str]:
    pics = (decoded.get("image") or {}).get("pics") or []
    if not isinstance(pics, list):
        return []
    return [str(pic["url"]) for pic in pics if isinstance(pic, dict) and pic.get("url")]


def extract_item_id(message: dict[str, Any]) -> str:
    message_1 = message.get("1") or {}
    message_10 = message_1.get("10") or {}

    reminder_url = str(message_10.get("reminderUrl") or "")
    item_id = _query_value(reminder_url, "itemId")
    if item_id:
        return item_id

    for key in ("bizTag", "extJson"):
        raw = message_10.get(key)
        if not raw:
            continue
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("itemId"):
            return str(value["itemId"])

    card = ((message_1.get("6") or {}).get("3") or {}).get("5")
    if isinstance(card, str):
        try:
            card_obj = json.loads(card)
        except json.JSONDecodeError:
            return ""
        jump_url = _nested(
            card_obj,
            "dxCard",
            "item",
            "main",
            "exContent",
            "button",
            "intent",
            "page",
            "jumpUrl",
        )
        return _query_value(str(jump_url or ""), "itemId")
    return ""


def extract_message_id(message: dict[str, Any]) -> str | None:
    message_1 = message.get("1")
    holders = []
    if isinstance(message_1, dict):
        holders.append(message_1.get("10"))
    holders.append(message.get("4"))
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        for key in ("bizTag", "extJson"):
            raw = holder.get(key)
            if not isinstance(raw, str):
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("messageId"):
                return str(value["messageId"])
    return None


def _query_value(url: str, key: str) -> str:
    if f"{key}=" not in url:
        return ""
    return url.split(f"{key}=", 1)[1].split("&", 1)[0]


def _nested(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def fallback_dedup_key(chat_id: str, sender_id: str, timestamp_ms: int, text: str) -> str:
    basis = f"{chat_id}|{sender_id}|{timestamp_ms}|{text}".encode()
    return hashlib.sha1(basis).hexdigest()


def parse_chat_message(message: dict[str, Any], self_id: str = "") -> dict[str, Any] | None:
    message_1 = message.get("1")
    if not isinstance(message_1, dict):
        return None

    chat_id = str(message_1.get("2") or "").split("@", 1)[0]
    try:
        timestamp_ms = int(message_1.get("5") or 0)
    except (TypeError, ValueError):
        timestamp_ms = 0

    message_10 = message_1.get("10")
    content = decode_first_content(
        [
            ((message_1.get("6") or {}).get("3") or {}).get("5"),
            ((message_1.get("6") or {}).get("3") or {}).get("1"),
        ]
    )

    if isinstance(message_10, dict) and "reminderContent" in message_10:
        sender_id = str(message_10.get("senderUserId") or "")
        sender_name = str(message_10.get("senderNick") or message_10.get("reminderTitle") or "")
        text = str(message_10.get("reminderContent") or "")
        kind = "text"
        if content is not None:
            parsed_text, images, parsed_kind = interpret_content(content)
            if parsed_kind == "image" and images:
                text = "\n".join(images)
                kind = "image"
            elif parsed_kind == "audio":
                text = parsed_text or "[语音消息]"
                kind = "audio"
            elif parsed_text:
                text = parsed_text
    else:
        sender_id = str((message_1.get("1") or {}).get("1") or "").split("@", 1)[0]
        sender_name = "系统"
        text = str(((message_1.get("6") or {}).get("3") or {}).get("2") or "")
        kind = "card"

    msg_id = extract_message_id(message)
    return {
        "msg_id": msg_id,
        "dedup_key": msg_id or fallback_dedup_key(chat_id, sender_id, timestamp_ms, text),
        "chat_id": chat_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "text": text,
        "kind": kind,
        "item_id": extract_item_id(message),
        "timestamp_ms": timestamp_ms,
        "is_self": bool(self_id) and sender_id == self_id,
        "raw": message,
    }

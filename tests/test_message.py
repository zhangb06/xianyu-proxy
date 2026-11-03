from __future__ import annotations

import base64
import json

from app.protocol.message import (
    extract_item_id,
    fallback_dedup_key,
    interpret_content,
    is_chat_message,
    parse_chat_message,
)


def _content(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()


def _message(
    *,
    sender: str = "buyer1",
    text: str = "你好",
    item_id: str = "999",
    content: dict | None = None,
) -> dict:
    message_1 = {
        "1": {"1": "seller1@goofish"},
        "2": "buyer1@goofish",
        "5": 1700000000000,
        "10": {
            "senderUserId": sender,
            "senderNick": "买家",
            "reminderContent": text,
            "reminderUrl": f"https://www.goofish.com/item?itemId={item_id}&x=1",
        },
        "6": {"3": {"5": _content(content or {"contentType": 1, "text": {"text": text}})}},
    }
    return {"1": message_1}


def test_parse_text_message() -> None:
    parsed = parse_chat_message(_message(), "seller1")
    assert parsed is not None
    assert parsed["chat_id"] == "buyer1"
    assert parsed["sender_id"] == "buyer1"
    assert parsed["text"] == "你好"
    assert parsed["kind"] == "text"
    assert parsed["item_id"] == "999"
    assert parsed["timestamp_ms"] == 1700000000000
    assert parsed["is_self"] is False
    assert parsed["dedup_key"]


def test_self_echo_detected() -> None:
    parsed = parse_chat_message(_message(sender="seller1"), "seller1")
    assert parsed is not None
    assert parsed["is_self"] is True


def test_image_message() -> None:
    content = {"contentType": 2, "image": {"pics": [{"url": "https://img/1.jpg"}]}}
    parsed = parse_chat_message(_message(content=content), "seller1")
    assert parsed is not None
    assert parsed["kind"] == "image"
    assert parsed["text"] == "https://img/1.jpg"


def test_interpret_audio() -> None:
    text, images, kind = interpret_content({"contentType": 3, "audio": {}})
    assert (text, images, kind) == ("[语音消息]", [], "audio")


def test_is_chat_message_requires_reminder() -> None:
    assert is_chat_message(_message())
    assert not is_chat_message({"1": {"10": {}}})


def test_item_id_from_card() -> None:
    card = {
        "dxCard": {
            "item": {
                "main": {
                    "exContent": {
                        "button": {"intent": {"page": {"jumpUrl": "fleamarket://item?itemId=42"}}}
                    }
                }
            }
        }
    }
    message = {"1": {"10": {"reminderContent": "x", "bizTag": "{}"}, "6": {"3": {"5": json.dumps(card)}}}}
    assert extract_item_id(message) == "42"


def test_fallback_dedup_key_stable() -> None:
    first = fallback_dedup_key("chat", "user", 1, "hi")
    second = fallback_dedup_key("chat", "user", 1, "hi")
    third = fallback_dedup_key("chat", "user", 1, "hello")
    assert first == second
    assert first != third
    assert len(first) == 40

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

# 真实下行帧（bizType 40 / objectType 40000）的 data 字段，msgpack 整数键编码
_REAL_DATA = (
    "ggGLAYEBtTIyMjI0NTM5ODcxOTVAZ29vZmlzaAKzNjg4NjE4OTA4ODdAZ29vZmlzaAOxNDM3NTcwMjQ4NzUwOS5QTk0EAAXPAAABoaDkZV4GggFlA4UBoAKm5L2g5aW9A6AEAQXaADd7ImF0VXNlcnMiOltdLCJjb250ZW50VHlwZSI6MSwidGV4dCI6eyJ0ZXh0Ijoi5L2g5aW9In19BwIIAQkACt4AEatfYXBwVmVyc2lvbqU5LjkuOalfcGxhdGZvcm2kb2hvc6ZiaXpUYWfaAEF7InNvdXJjZUlkIjoiUzoxIiwibWVzc2FnZUlkIjoiMzQ2ZWYwZDI5OWZiNDU4Y2E0ZjgzNmFlNjgyYTVlMzMifahjbGllbnRJcK8yMjMuMTA0LjIwMi4xMTKsZGV0YWlsTm90aWNlpuS9oOWlvadleHRKc29u2gCdeyJxdWlja1JlcGx5IjoiMSIsInV0ZGlkIjoiYWY2KzBtTytvcWdEQUVzbkJtT2o0RFVFIiwibWVzc2FnZUlkIjoiMzQ2ZWYwZDI5OWZiNDU4Y2E0ZjgzNmFlNjgyYTVlMzMiLCJ1bWlkVG9rZW4iOiI1aklCTjE5TFBDU1ZuVEtob0F5WXlJbGszT2ZBSjlBdSIsInRhZyI6InUifaRwb3J0pTQzNDQ1r3JlbWluZGVyQ29udGVudKbkvaDlpb2ucmVtaW5kZXJOb3RpY2W15Y+R5p2l5LiA5p2h5paw5raI5oGvrXJlbWluZGVyVGl0bGWleCoqKjOrcmVtaW5kZXJVcmzaAIlmbGVhbWFya2V0Oi8vbWVzc2FnZV9jaGF0P2l0ZW1JZD0xMDk2MTg1NTI2MjAzJnBlZXJVc2VySWQ9MjIyMjQ1Mzk4NzE5NSZzaWQ9Njg4NjE4OTA4ODcmbWVzc2FnZUlkPTM0NmVmMGQyOTlmYjQ1OGNhNGY4MzZhZTY4MmE1ZTMzJmFkdj1ub6xzZW5kZXJVc2VySWStMjIyMjQ1Mzk4NzE5Na5zZW5kZXJVc2VyVHlwZaEwq3Nlc3Npb25UeXBloTGkdW1pZNoAIVdWNjMwMzk2MzY5ZmM3YzI2MjAwYmY4NDY4MDA1YjgzZKl1bWlkVG9rZW7aACA1aklCTjE5TFBDU1ZuVEtob0F5WXlJbGszT2ZBSjlBdaV1dGRpZLhhZjYrMG1PK29xZ0RBRXNuQm1PajREVUUMAQOBqG5lZWRQdXNopHRydWU="
)


def test_real_msgpack_payload_decodes_with_string_keys() -> None:
    from app.protocol.mtop import decode_payload

    message = decode_payload(_REAL_DATA)
    assert message is not None
    assert is_chat_message(message) is True


def test_real_payload_parses_chat_fields() -> None:
    from app.protocol.mtop import decode_payload

    message = decode_payload(_REAL_DATA)
    parsed = parse_chat_message(message, "930190078")
    assert parsed is not None
    assert parsed["chat_id"] == "68861890887"
    assert parsed["sender_id"] == "2222453987195"
    assert parsed["text"] == "你好"
    assert parsed["kind"] == "text"
    assert parsed["item_id"] == "1096185526203"
    assert parsed["is_self"] is False
    assert parsed["msg_id"] == "346ef0d299fb458ca4f836ae682a5e33"

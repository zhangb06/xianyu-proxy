from __future__ import annotations

from app.protocol.message import is_chat_message, parse_chat_message
from app.protocol.mtop import decode_payload

# 真实下行帧（bizType 40 / objectType 40000）的 data 字段，msgpack 整数键编码
_REAL_DATA = (
    "ggGLAYEBtTIyMjI0NTM5ODcxOTVAZ29vZmlzaAKzNjg4NjE4OTA4ODdAZ29vZmlzaAOxNDM3NTcwMjQ4NzUwOS5QTk0EAAXPAAABoaDkZV4GggFlA4UBoAKm5L2g5aW9A6AEAQXaADd7ImF0VXNlcnMiOltdLCJjb250ZW50VHlwZSI6MSwidGV4dCI6eyJ0ZXh0Ijoi5L2g5aW9In19BwIIAQkACt4AEatfYXBwVmVyc2lvbqU5LjkuOalfcGxhdGZvcm2kb2hvc6ZiaXpUYWfaAEF7InNvdXJjZUlkIjoiUzoxIiwibWVzc2FnZUlkIjoiMzQ2ZWYwZDI5OWZiNDU4Y2E0ZjgzNmFlNjgyYTVlMzMifahjbGllbnRJcK8yMjMuMTA0LjIwMi4xMTKsZGV0YWlsTm90aWNlpuS9oOWlvadleHRKc29u2gCdeyJxdWlja1JlcGx5IjoiMSIsInV0ZGlkIjoiYWY2KzBtTytvcWdEQUVzbkJtT2o0RFVFIiwibWVzc2FnZUlkIjoiMzQ2ZWYwZDI5OWZiNDU4Y2E0ZjgzNmFlNjgyYTVlMzMiLCJ1bWlkVG9rZW4iOiI1aklCTjE5TFBDU1ZuVEtob0F5WXlJbGszT2ZBSjlBdSIsInRhZyI6InUifaRwb3J0pTQzNDQ1r3JlbWluZGVyQ29udGVudKbkvaDlpb2ucmVtaW5kZXJOb3RpY2W15Y+R5p2l5LiA5p2h5paw5raI5oGvrXJlbWluZGVyVGl0bGWleCoqKjOrcmVtaW5kZXJVcmzaAIlmbGVhbWFya2V0Oi8vbWVzc2FnZV9jaGF0P2l0ZW1JZD0xMDk2MTg1NTI2MjAzJnBlZXJVc2VySWQ9MjIyMjQ1Mzk4NzE5NSZzaWQ9Njg4NjE4OTA4ODcmbWVzc2FnZUlkPTM0NmVmMGQyOTlmYjQ1OGNhNGY4MzZhZTY4MmE1ZTMzJmFkdj1ub6xzZW5kZXJVc2VySWStMjIyMjQ1Mzk4NzE5Na5zZW5kZXJVc2VyVHlwZaEwq3Nlc3Npb25UeXBloTGkdW1pZNoAIVdWNjMwMzk2MzY5ZmM3YzI2MjAwYmY4NDY4MDA1YjgzZKl1bWlkVG9rZW7aACA1aklCTjE5TFBDU1ZuVEtob0F5WXlJbGszT2ZBSjlBdaV1dGRpZLhhZjYrMG1PK29xZ0RBRXNuQm1PajREVUUMAQOBqG5lZWRQdXNopHRydWU="
)


def test_real_msgpack_payload_decodes_with_string_keys() -> None:
    message = decode_payload(_REAL_DATA)
    assert message is not None
    assert is_chat_message(message) is True


def test_real_payload_parses_chat_fields() -> None:
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

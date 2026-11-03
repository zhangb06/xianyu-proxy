from __future__ import annotations

from app.core.ids import generate_device_id, generate_mid, generate_uuid


def test_device_id_shape() -> None:
    device_id = generate_device_id("1234567890")
    body, suffix = device_id.rsplit("-", 1)
    assert len(body) == 36
    assert suffix == "1234567890"
    assert [index for index, char in enumerate(body) if char == "-"] == [8, 13, 18, 23]
    assert body[14] == "4"
    assert body[19] in "89AB"


def test_mid_format() -> None:
    mid = generate_mid()
    number, suffix = mid.split(" ")
    assert number.isdigit()
    assert len(number) >= 14
    assert suffix == "0"


def test_uuid_format() -> None:
    value = generate_uuid()
    assert value.startswith("-")
    assert value.endswith("1")
    assert value[1:-1].isdigit()

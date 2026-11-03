from __future__ import annotations

import base64
import hashlib
import json

import msgpack

from app.protocol.mtop import CookieJar, decode_payload, generate_sign, parse_cookie_string


def test_sign_composition() -> None:
    expected = hashlib.md5(b'token&1700000000000&34839810&{"a":1}').hexdigest()
    assert generate_sign("1700000000000", "token", '{"a":1}') == expected


def test_sign_custom_app_key() -> None:
    expected = hashlib.md5(b'&1&app&{}').hexdigest()
    assert generate_sign("1", "", "{}", "app") == expected


def test_cookie_parsing() -> None:
    cookies = parse_cookie_string("a=1; b=2=3; broken")
    assert cookies == {"a": "1", "b": "2=3"}
    jar = CookieJar.from_raw(cookies)
    assert jar.header() == "a=1; b=2=3"
    assert jar.get("missing") is None


def test_decode_payload_json() -> None:
    raw = base64.b64encode(json.dumps({"1": {"2": "x"}}).encode()).decode()
    assert decode_payload(raw) == {"1": {"2": "x"}}


def test_decode_payload_msgpack() -> None:
    raw = base64.b64encode(msgpack.packb({"hello": "world"})).decode()
    assert decode_payload(raw) == {"hello": "world"}


def test_decode_payload_invalid() -> None:
    assert decode_payload("not-base64!!") is None

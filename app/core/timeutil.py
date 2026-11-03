from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)

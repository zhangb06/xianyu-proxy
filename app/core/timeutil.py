from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

_BEIJING = timezone(timedelta(hours=8))


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def beijing_today() -> str:
    return datetime.now(_BEIJING).date().isoformat()


def beijing_now() -> datetime:
    return datetime.now(_BEIJING)


def beijing_date(iso: str) -> str:
    """UTC ISO 时间对应的北京时间自然日；空串/无法解析返回空串。"""
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_BEIJING).date().isoformat()

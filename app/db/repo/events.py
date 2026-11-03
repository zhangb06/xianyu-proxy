from __future__ import annotations

import json
from typing import Any

from app.db.engine import Database


async def log_event(
    db: Database,
    *,
    type: str,
    account_id: int | None = None,
    level: str = "info",
    detail: dict[str, Any] | None = None,
) -> None:
    await db.write(
        "INSERT INTO event_log (account_id, type, level, detail_json) VALUES (?, ?, ?, ?)",
        (account_id, type, level, json.dumps(detail or {}, ensure_ascii=False)),
    )

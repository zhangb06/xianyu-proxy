from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import require_admin

router = APIRouter(tags=["events"])


@router.get("/events")
async def list_events(
    request: Request,
    account_id: int | None = None,
    type: str | None = None,
    level: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _session=Depends(require_admin),
) -> list[dict[str, object]]:
    sql = "SELECT id, account_id, type, level, detail_json, created_at FROM event_log WHERE 1 = 1"
    params: list[object] = []
    if account_id is not None:
        sql += " AND account_id = ?"
        params.append(account_id)
    if type:
        sql += " AND type = ?"
        params.append(type)
    if level:
        sql += " AND level = ?"
        params.append(level)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = await request.app.state.db.fetch_all(sql, tuple(params))
    return [
        {
            "id": row["id"],
            "account_id": row["account_id"],
            "type": row["type"],
            "level": row["level"],
            "detail": json.loads(row["detail_json"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]

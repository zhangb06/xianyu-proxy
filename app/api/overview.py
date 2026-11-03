from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_admin
from app.db.repo.messages import MessageRepo

router = APIRouter(tags=["overview"])


@router.get("/overview")
async def overview(request: Request, _session=Depends(require_admin)) -> dict[str, object]:
    db = request.app.state.db
    accounts = await db.fetch_all(
        "SELECT id, remark, xianyu_id, status, enabled FROM account ORDER BY id"
    )
    counts = await MessageRepo(db).count_today()
    issues = await db.fetch_all(
        "SELECT id, account_id, type, level, detail_json, created_at FROM event_log "
        "WHERE level IN ('warning', 'error') ORDER BY id DESC LIMIT 20"
    )
    return {
        "accounts": {
            "total": len(accounts),
            "online": sum(1 for row in accounts if row["status"] == "online"),
            "items": [
                {
                    "id": row["id"],
                    "remark": row["remark"],
                    "xianyu_id": row["xianyu_id"],
                    "status": row["status"],
                    "enabled": bool(row["enabled"]),
                }
                for row in accounts
            ],
        },
        "today": counts,
        "issues": [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "type": row["type"],
                "level": row["level"],
                "detail": json.loads(row["detail_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in issues
        ],
    }

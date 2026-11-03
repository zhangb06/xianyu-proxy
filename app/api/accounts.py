from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/accounts")
async def list_accounts(request: Request) -> list[dict[str, object]]:
    rows = await request.app.state.db.fetch_all(
        "SELECT id, xianyu_id, remark, enabled, status, verification_mode, last_error, updated_at "
        "FROM account ORDER BY id"
    )
    return [
        {
            "id": row["id"],
            "xianyu_id": row["xianyu_id"],
            "remark": row["remark"],
            "enabled": bool(row["enabled"]),
            "status": row["status"],
            "verification_mode": row["verification_mode"],
            "last_error": row["last_error"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]

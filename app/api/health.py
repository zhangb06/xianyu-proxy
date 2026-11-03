from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    db = request.app.state.db
    row = await db.fetch_one("SELECT COUNT(*) AS total FROM account")
    return {
        "status": "ok",
        "version": __version__,
        "accounts": int(row["total"]) if row else 0,
    }

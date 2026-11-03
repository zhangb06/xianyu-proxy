from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin, require_write
from app.db.repo.accounts import Account

router = APIRouter(tags=["accounts"])


def serialize_account(account: Account) -> dict[str, object]:
    return {
        "id": account.id,
        "xianyu_id": account.xianyu_id,
        "remark": account.remark,
        "enabled": account.enabled,
        "status": account.status,
        "device_id": account.device_id,
        "proxy_url": account.proxy_url,
        "verification_mode": account.verification_mode,
        "last_error": account.last_error,
    }


class AccountPatch(BaseModel):
    remark: str | None = None
    proxy_url: str | None = None
    verification_mode: Literal["", "manual", "auto_fallback"] | None = None


@router.get("/accounts")
async def list_accounts(
    request: Request, _session=Depends(require_admin)
) -> list[dict[str, object]]:
    accounts = await request.app.state.supervisor.accounts.list_all()
    return [serialize_account(account) for account in accounts]


@router.patch("/accounts/{account_id}")
async def patch_account(
    account_id: int, body: AccountPatch, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    repo = request.app.state.supervisor.accounts
    account = await repo.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if body.remark is not None or body.proxy_url is not None or body.verification_mode is not None:
        await repo.update_fields(
            account_id,
            remark=body.remark,
            proxy_url=body.proxy_url,
            verification_mode=body.verification_mode,
        )
    account = await repo.get(account_id)
    assert account is not None
    return serialize_account(account)


@router.post("/accounts/{account_id}/enable")
async def enable_account(
    account_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    repo = request.app.state.supervisor.accounts
    if await repo.get(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    await repo.set_enabled(account_id, True)
    await repo.set_status(account_id, "new")
    await request.app.state.supervisor.reload()
    account = await repo.get(account_id)
    assert account is not None
    return serialize_account(account)


@router.post("/accounts/{account_id}/disable")
async def disable_account(
    account_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, object]:
    repo = request.app.state.supervisor.accounts
    if await repo.get(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    await repo.set_enabled(account_id, False)
    await repo.set_status(account_id, "disabled")
    await request.app.state.supervisor.reload()
    account = await repo.get(account_id)
    assert account is not None
    return serialize_account(account)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int, request: Request, _session=Depends(require_write)
) -> dict[str, bool]:
    repo = request.app.state.supervisor.accounts
    if await repo.get(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    await request.app.state.supervisor.stop_account(account_id)
    await repo.delete(account_id)
    return {"ok": True}


@router.post("/accounts/qr")
async def start_qr_login(request: Request, _session=Depends(require_write)) -> dict[str, object]:
    settings = request.app.state.settings
    try:
        return await request.app.state.qr.start(timeout=settings.http_timeout)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"获取二维码失败: {exc}") from exc


@router.get("/accounts/qr/{session_id}")
async def qr_login_status(
    session_id: str, request: Request, _session=Depends(require_admin)
) -> dict[str, object]:
    result = await request.app.state.qr.status(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="扫码会话不存在或已过期")
    return result

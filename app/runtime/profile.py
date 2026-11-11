from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.db.repo.chats import ChatRepo
from app.protocol.mtop import CookieJar, MtopError
from app.protocol.trade import fetch_own_nickname

NICKNAME_INTERVAL_SECONDS = 60.0

NicknameFetcher = Callable[..., Awaitable[str]]


async def fill_account_nickname(
    db: Database,
    accounts: AccountRepo,
    account_id: int,
    *,
    fetch: NicknameFetcher | None = None,
) -> str:
    """备注为空时，借最近会话读取卖家昵称并写入；任何失败都静默跳过。"""
    account = await accounts.get(account_id)
    if account is None or account.remark:
        return ""
    cookie = await accounts.load_cookie(account_id)
    if not cookie:
        return ""
    chat_id = await ChatRepo(db).latest_chat_id(account_id)
    if not chat_id:
        return ""
    fetcher = fetch or fetch_own_nickname
    try:
        nickname = await fetcher(CookieJar(dict(cookie)), chat_id, proxy=account.proxy_url)
    except MtopError as exc:
        logger.info(f"账号 {account_id} 自动读取昵称失败: {exc}")
        return ""
    except Exception as exc:
        logger.warning(f"账号 {account_id} 自动读取昵称异常: {exc}")
        return ""
    if not nickname:
        return ""
    await accounts.set_remark_if_empty(account_id, nickname)
    return nickname
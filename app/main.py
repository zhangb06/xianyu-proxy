from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from loguru import logger

from app.api.accounts import router as accounts_router
from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.secrets import resolve_secret
from app.db.engine import Database
from app.db.repo.accounts import AccountRepo
from app.platform import make_paths


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        current = settings or get_settings()
        paths = make_paths(current.data_dir).ensure()
        setup_logging(current.log_level, paths.logs / "app.log")

        db = Database(paths.db)
        await db.connect()
        secret = await resolve_secret(db, current.secret_key)

        app.state.settings = current
        app.state.paths = paths
        app.state.db = db
        app.state.accounts = AccountRepo(db, secret)
        logger.info(f"xianyu-lite 启动: data={paths.data}")
        try:
            yield
        finally:
            await db.close()
            logger.info("xianyu-lite 已停止")

    app = FastAPI(title="xianyu-lite", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(accounts_router, prefix="/api", tags=["accounts"])
    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning", workers=1)


if __name__ == "__main__":
    main()

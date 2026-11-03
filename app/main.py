from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.accounts import router as accounts_router
from app.api.auth import router as auth_router
from app.api.chats import router as chats_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.overview import router as overview_router
from app.api.rules import router as rules_router
from app.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.secrets import resolve_secret
from app.db.engine import Database
from app.platform import make_paths
from app.runtime.qrlogin_manager import QRLoginManager
from app.runtime.supervisor import Supervisor

WEB_DIST = Path(__file__).parent / "web" / "dist"


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        current = settings or get_settings()
        paths = make_paths(current.data_dir).ensure()
        setup_logging(current.log_level, paths.logs / "app.log")

        db = Database(paths.db)
        await db.connect()
        secret = await resolve_secret(db, current.secret_key)

        supervisor = Supervisor(db, current, secret)
        await supervisor.start()

        app.state.settings = current
        app.state.paths = paths
        app.state.db = db
        app.state.secret = secret
        app.state.supervisor = supervisor
        app.state.qr = QRLoginManager(supervisor.accounts, supervisor)
        logger.info(f"xianyu-lite {__version__} 启动: data={paths.data}")
        try:
            yield
        finally:
            await supervisor.stop()
            await db.close()
            logger.info("xianyu-lite 已停止")

    app = FastAPI(title="xianyu-lite", version=__version__, lifespan=lifespan)
    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(auth_router, prefix="/api")
    app.include_router(accounts_router, prefix="/api")
    app.include_router(overview_router, prefix="/api")
    app.include_router(rules_router, prefix="/api")
    app.include_router(chats_router, prefix="/api")
    app.include_router(events_router, prefix="/api")
    if WEB_DIST.is_dir():
        app.mount("/", SPAStaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning", workers=1)


if __name__ == "__main__":
    main()

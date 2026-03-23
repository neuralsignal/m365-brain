"""FastAPI application factory for multi-user m365-extract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from m365_extract.auth.token_store import TokenStore
from m365_extract.config import Config
from m365_extract.user_manager import UserManager
from m365_extract.web.exceptions import AccessDeniedError, SyncError, UserNotFoundError, WebConfigError
from m365_extract.web.routes_admin import router as admin_router
from m365_extract.web.routes_auth import router as auth_router
from m365_extract.web.routes_health import router as health_router
from m365_extract.web.routes_sync import router as sync_router
from m365_extract.web.scheduler import SyncScheduler

log = structlog.get_logger()


def create_app(config: Config) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config.web is None:
        msg = "WebConfig is required for web mode (config.web is None)"
        raise WebConfigError(msg)

    web_config = config.web

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        token_store = TokenStore(db_path=web_config.db_path, fernet_key=web_config.fernet_key, check_same_thread=False)
        user_manager = UserManager(db_path=web_config.db_path, check_same_thread=False)

        app.state.config = config
        app.state.token_store = token_store
        app.state.user_manager = user_manager

        scheduler = SyncScheduler(config=config, token_store=token_store, user_manager=user_manager)
        scheduler.start()
        app.state.scheduler = scheduler

        log.info("web.started", host=web_config.host, port=web_config.port)
        yield
        scheduler.shutdown()
        log.info("web.shutdown")

    app = FastAPI(title="m365-extract", lifespan=lifespan)

    app.add_middleware(SessionMiddleware, secret_key=web_config.secret_key)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(sync_router)
    app.include_router(admin_router)

    @app.exception_handler(UserNotFoundError)
    async def user_not_found_handler(request: Request, exc: UserNotFoundError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(SyncError)
    async def sync_error_handler(request: Request, exc: SyncError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=500)

    @app.exception_handler(AccessDeniedError)
    async def access_denied_handler(request: Request, exc: AccessDeniedError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=403)

    return app

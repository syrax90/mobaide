from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from claude_mobile.core import SessionManager

from .auth.jwt_service import JwtService
from .auth.router import router as auth_router
from .auth.user_store import UserStore
from .config import ServerConfig
from .sessions.router import router as sessions_router
from .ws.event_fanout import SessionListBroadcaster
from .ws.session_io import router as ws_session_io_router
from .ws.session_list import router as ws_session_list_router

logger = logging.getLogger(__name__)


def create_app(config: ServerConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    if config is None:
        load_dotenv()
        config = ServerConfig.from_env()

    logging.basicConfig(level=config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting Claude Mobile server")
        await app.state.session_manager.start_liveness_monitor()
        yield
        logger.info("Shutting down Claude Mobile server")
        # Cancel all fanout tasks
        for fanout in app.state.fanouts.values():
            fanout.stop()
        await app.state.session_manager.shutdown()

    app = FastAPI(title="Claude Mobile Server", version="0.1.0", lifespan=lifespan)

    # Shared state
    app.state.config = config
    app.state.session_manager = SessionManager(max_sessions=config.max_sessions)
    app.state.jwt_service = JwtService(
        secret_key=config.jwt_secret_key,
        algorithm=config.jwt_algorithm,
        access_ttl_minutes=config.jwt_access_token_expire_minutes,
        refresh_ttl_minutes=config.jwt_refresh_token_expire_minutes,
    )
    user_store = UserStore(config.user_store_path)
    user_store.load()
    app.state.user_store = user_store
    app.state.broadcaster = SessionListBroadcaster()
    app.state.fanouts = {}  # session_id -> SessionEventFanout

    # Routers
    app.include_router(auth_router)
    app.include_router(sessions_router)
    app.include_router(ws_session_io_router)
    app.include_router(ws_session_list_router)

    # Health endpoint (no auth)
    @app.get("/api/health", tags=["health"])
    async def health():
        sm = app.state.session_manager
        return {
            "status": "ok",
            "sessions": len(sm.list_sessions()),
            "max_sessions": sm.max_sessions,
        }

    return app

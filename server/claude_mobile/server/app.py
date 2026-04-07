from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from dotenv import load_dotenv
from fastapi import FastAPI, Request

from claude_mobile.core import ClaudeLauncher, SessionManager
from claude_mobile.linear.scanner import AutoScanService
from claude_mobile.linear.worktree import WorktreeManager

from .auth.jwt_service import JwtService
from .auth.router import router as auth_router
from .auth.user_store import UserStore
from .autoscan.router import router as autoscan_router
from .autoscan.router import ws_router as autoscan_ws_router
from .config import ServerConfig
from .logging import bind_request_context, clear_request_context, get_logger
from .logging_setup import configure_logging, shutdown_logging
from .projects.router import router as projects_router
from .sessions.router import router as sessions_router
from .events import EventBus
from .ws.event_fanout import SessionListBroadcaster
from .ws.events import router as ws_events_router
from .ws.session_io import router as ws_session_io_router
from .ws.session_list import router as ws_session_list_router

logger = get_logger(__name__)

# Endpoints excluded from per-request HTTP middleware logging (high-frequency, low-value).
_HTTP_LOG_SKIP_PATHS = frozenset({"/api/health"})


def _generate_correlation_id() -> str:
    """16-byte hex (W3C trace-id shape) — forward-compatible with OpenTelemetry."""
    return secrets.token_hex(16)


def create_app(config: ServerConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    if config is None:
        load_dotenv()
        config = ServerConfig.from_env()

    configure_logging(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("server.starting")
        await app.state.session_manager.start_liveness_monitor()
        yield
        logger.info("server.shutting_down")
        # Stop auto-scan service if running
        auto_scan = getattr(app.state, "auto_scan_service", None)
        if auto_scan is not None:
            await auto_scan.stop()
        # Cancel all fanout tasks
        for fanout in app.state.fanouts.values():
            fanout.stop()
        await app.state.session_manager.shutdown()
        shutdown_logging()

    app = FastAPI(title="Claude Mobile Server", version="0.1.0", lifespan=lifespan)

    # -- Middleware (added in reverse-execution order: outermost added last) --

    @app.middleware("http")
    async def _http_logging_middleware(request: Request, call_next):
        if request.url.path in _HTTP_LOG_SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        client_ip = request.client.host if request.client else None
        request_id = correlation_id.get() or ""
        bind_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
        )
        logger.info(
            "http.request.received",
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent"),
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "http.request.failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            clear_request_context()
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        username = getattr(request.state, "username", None)
        logger.info(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            username=username,
        )
        clear_request_context()
        return response

    # CorrelationIdMiddleware runs OUTSIDE the http logging middleware (added later → outer).
    app.add_middleware(
        CorrelationIdMiddleware,
        header_name="X-Request-ID",
        generator=_generate_correlation_id,
    )

    # Shared state
    app.state.config = config
    app.state.projects_config = config.projects_config

    # Build a ClaudeLauncher (and a worktree manager if auto-scan settings
    # are present so launcher-driven worktrees use the same conventions).
    worktree_mgr: WorktreeManager | None = None
    if config.auto_scan_config is not None:
        prefix = config.auto_scan_config.branch_prefix or "feature/claude"
        worktree_mgr = WorktreeManager(
            worktree_base=config.auto_scan_config.worktree_base,
            branch_prefix=prefix,
            main_branch=config.auto_scan_config.main_branch,
        )
    app.state.claude_launcher = ClaudeLauncher(worktree_mgr=worktree_mgr)

    app.state.session_manager = SessionManager(
        max_sessions=config.max_sessions,
        projects_config=config.projects_config,
        launcher=app.state.claude_launcher,
    )
    app.state.jwt_service = JwtService(
        secret_key=config.jwt_secret_key,
        algorithm=config.jwt_algorithm,
        access_ttl_minutes=config.jwt_access_token_expire_minutes,
        refresh_ttl_minutes=config.jwt_refresh_token_expire_minutes,
    )
    user_store = UserStore(config.user_store_path)
    user_store.load()
    app.state.user_store = user_store
    app.state.event_bus = EventBus()
    app.state.broadcaster = SessionListBroadcaster()
    app.state.fanouts = {}  # session_id -> SessionEventFanout

    # Auto-scan service (optional — only if PROJECTS is configured)
    if config.auto_scan_config is not None:
        app.state.auto_scan_service = AutoScanService(
            config=config.auto_scan_config,
            session_manager=app.state.session_manager,
            launcher=app.state.claude_launcher,
            event_bus=app.state.event_bus,
        )
    else:
        app.state.auto_scan_service = None

    # Routers
    app.include_router(auth_router)
    app.include_router(sessions_router)
    app.include_router(projects_router)
    app.include_router(ws_session_io_router)
    app.include_router(ws_session_list_router)
    app.include_router(ws_events_router)
    app.include_router(autoscan_router)
    app.include_router(autoscan_ws_router)

    # Health endpoint (no auth)
    @app.get("/api/health", tags=["health"])
    async def health():
        sm = app.state.session_manager
        auto_scan = getattr(app.state, "auto_scan_service", None)
        result = {
            "status": "ok",
            "sessions": len(sm.list_sessions()),
            "max_sessions": sm.max_sessions,
        }
        if auto_scan is not None:
            result["auto_scan"] = auto_scan.get_status()["status"]
        return result

    return app

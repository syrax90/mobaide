from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from claude_mobile.core import ClaudeLauncher, ProjectsStore, SessionManager
from claude_mobile.linear.config import AutoScanConfig
from claude_mobile.linear.rate_limiter import LinearRateLimiter
from claude_mobile.linear.scanner import AutoScanSessionManager
from claude_mobile.linear.worktree import WorktreeManager

from .auth.jwt_service import JwtService
from .auth.router import router as auth_router
from .auth.user_store import UserStore
from .autoscan.router import router as autoscan_router
from .autoscan.router import ws_router as autoscan_ws_router
from .config import AgentConfig
from .logging import bind_request_context, clear_request_context, get_logger
from .logging_setup import configure_logging, shutdown_logging
from .projects.router import router as projects_router
from .sessions.router import router as sessions_router
from .events import EventBus
from .ws.event_fanout import SessionListBroadcaster
from .ws.events import router as ws_events_router
from .ws.session_io import (
    ensure_fanout,
    router as ws_session_io_router,
    stop_fanout,
)
from .ws.session_list import router as ws_session_list_router

logger = get_logger(__name__)

# Endpoints excluded from per-request HTTP middleware logging (high-frequency, low-value).
_HTTP_LOG_SKIP_PATHS = frozenset({"/api/health"})


def _generate_correlation_id() -> str:
    """16-byte hex (W3C trace-id shape) — forward-compatible with OpenTelemetry."""
    return secrets.token_hex(16)


def create_app(config: AgentConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    if config is None:
        load_dotenv()
        config = AgentConfig.from_env()

    configure_logging(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("agent.starting")
        await app.state.session_manager.start_liveness_monitor()
        # Start relay client if configured (connects to Central Server).
        if config.relay_config is not None:
            from claude_mobile.relay.client import RelayClient

            app.state.relay_client = RelayClient(
                config=config.relay_config,
                session_manager=app.state.session_manager,
                event_bus=app.state.event_bus,
                broadcaster=app.state.broadcaster,
                app_state=app.state,
            )
            await app.state.relay_client.start()
        else:
            app.state.relay_client = None
        yield
        logger.info("agent.shutting_down")
        # Relay client must stop first so it unsubscribes from fanouts/buses before they are torn down.
        relay_client = getattr(app.state, "relay_client", None)
        if relay_client is not None:
            await relay_client.stop()
        autoscan_mgr = getattr(app.state, "autoscan_manager", None)
        if autoscan_mgr is not None:
            await autoscan_mgr.shutdown()
        for fanout in app.state.fanouts.values():
            fanout.stop()
        await app.state.session_manager.shutdown()
        shutdown_logging()

    app = FastAPI(title="Claude Mobile Agent", version="0.1.0", lifespan=lifespan)

    # Strip `linear_api_key` inputs from validation errors so a malformed body
    # cannot echo the secret back to the caller; other fields keep `input`.
    _SECRET_FIELDS = frozenset({"linear_api_key"})

    @app.exception_handler(RequestValidationError)
    async def _scrub_validation_errors(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        scrubbed = []
        for err in exc.errors():
            loc = err.get("loc", ())
            if any(part in _SECRET_FIELDS for part in loc):
                err = {k: v for k, v in err.items() if k != "input"}
            scrubbed.append(err)
        return JSONResponse(status_code=422, content={"detail": scrubbed})

    # Middleware is added in reverse execution order: outermost registered last.
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

    app.state.config = config

    # Mutable, file-backed projects store (seeds from PROJECTS env var on
    # first boot, then file wins forever). Every downstream consumer
    # (SessionManager, auto-scan, relay proxy, WS snapshot) reads through
    # this single reference.
    projects_store = ProjectsStore(config.projects_store_path)
    projects_store.load()
    app.state.projects_store = projects_store

    # Worktree manager shares auto-scan conventions so launcher-driven worktrees stay consistent.
    auto_scan_cfg = config.auto_scan_config
    worktree_mgr: WorktreeManager | None = None
    if auto_scan_cfg is not None:
        prefix = auto_scan_cfg.branch_prefix or "feature/claude"
        worktree_mgr = WorktreeManager(
            worktree_base=auto_scan_cfg.worktree_base,
            branch_prefix=prefix,
            main_branch=auto_scan_cfg.main_branch,
        )
    app.state.claude_launcher = ClaudeLauncher(worktree_mgr=worktree_mgr)

    # event_bus / broadcaster / fanouts must exist before SessionManager
    # so the on_session_created hook can attach a SessionEventFanout
    # (and publish `sessions.changed` via the bus) the moment any session
    # is created — including headless Auto Scan subsessions.
    app.state.event_bus = EventBus()
    app.state.broadcaster = SessionListBroadcaster()
    app.state.fanouts = {}  # session_id -> SessionEventFanout

    def _on_session_created(session) -> None:
        ensure_fanout(app.state, session)

    def _on_session_removed(session_id: str) -> None:
        stop_fanout(app.state, session_id)

    app.state.session_manager = SessionManager(
        max_sessions=config.max_sessions,
        projects_store=projects_store,
        launcher=app.state.claude_launcher,
        event_bus=app.state.event_bus,
        on_session_created=_on_session_created,
        on_session_removed=_on_session_removed,
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

    # Auto-scan manager is always wired in. Whether it has any projects to
    # operate on is a runtime question answered by the projects store —
    # adding a Linear-keyed project via the CRUD API makes it immediately
    # available for auto-scan without a restart.
    if auto_scan_cfg is None:
        auto_scan_cfg = AutoScanConfig()
    app.state.linear_rate_limiter = LinearRateLimiter()
    app.state.autoscan_manager = AutoScanSessionManager(
        config=auto_scan_cfg,
        session_manager=app.state.session_manager,
        projects_store=projects_store,
        launcher=app.state.claude_launcher,
        event_bus=app.state.event_bus,
        rate_limiter=app.state.linear_rate_limiter,
    )

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
        autoscan_mgr = getattr(app.state, "autoscan_manager", None)
        result = {
            "status": "ok",
            "sessions": len(sm.list_sessions()),
            "max_sessions": sm.max_sessions,
        }
        if autoscan_mgr is not None:
            result["auto_scans_active"] = len(autoscan_mgr.list())
        return result

    return app

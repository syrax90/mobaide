from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mobaide import __version__
from mobaide.core import ClaudeLauncher, ProjectsStore, SessionManager
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.rate_limiter import LinearRateLimiter
from mobaide.linear.scanner import AutoScanSessionManager
from mobaide.linear.worktree import WorktreeManager
from mobaide.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)

from .auth.jwt_service import JwtService
from .auth.router import router as auth_router
from .auth.user_store import UserStore
from .autoscan.router import router as autoscan_router
from .autoscan.router import ws_router as autoscan_ws_router
from .config import AgentConfig
from .git.router import router as git_router
from .install_paths import default_prefix, read_trust_public_key
from .logging import bind_request_context, clear_request_context, get_logger
from .logging_setup import configure_logging, shutdown_logging
from .projects.router import router as projects_router
from .sessions.router import router as sessions_router
from .events import EventBus
from .updater_router import router as updater_router
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


def _build_updater(app: FastAPI):
    """Return a ``StagedBinaryUpdater`` bound to this agent's session
    manager, or ``None`` if the install layout has no trust key.

    When the agent runs from a developer checkout there is no
    ``<prefix>/trust/central_server.pub`` and the auto-updater stays
    disabled — manifests delivered over the relay are recorded but
    never applied, fail-closed.
    """
    from mobaide.agent.updater import InstallLayout, StagedBinaryUpdater

    layout = InstallLayout(prefix=default_prefix())
    public_key_b64 = read_trust_public_key(layout)
    if public_key_b64 is None:
        logger.info(
            "updater.disabled reason=no_trust_key prefix=%s",
            layout.prefix,
        )
        return None
    return StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=app.state.session_manager,
        current_version=__version__,
    )


def _resolve_env_file() -> Path | None:
    """Return the canonical agent ``.env`` path if one exists.

    Resolution order:

    1. ``<install_prefix>/mobaide_agent/.env`` — production layout written
       by the install scripts (see ``env_path_for_prefix``).
    2. ``<package_root>/../.env`` — developer-checkout fallback so
       ``python -m mobaide`` works from a source tree without requiring
       ``MOBAIDE_AGENT_PREFIX`` to be set. Only triggers in editable / dev
       installs where ``__file__`` resolves into the repo source tree; for
       wheel installs ``parents[2]`` is ``site-packages`` and the fallback
       silently misses.

    Returning ``None`` when both candidates miss lets pydantic-settings
    fall back to process-environment vars (which is what tests rely on).
    """
    from mobaide.agent._env_writer import env_path_for_prefix

    candidate = env_path_for_prefix(default_prefix())
    if candidate.exists():
        return candidate

    dev_candidate = Path(__file__).resolve().parents[2] / ".env"
    return dev_candidate if dev_candidate.exists() else None


def create_app(config: AgentConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    if config is None:
        config = AgentConfig.from_env(env_file=_resolve_env_file())

    configure_logging(config)

    relay_cfg = config.relay_config
    pairing_pending = relay_cfg is not None and relay_cfg.pairing_pending
    if pairing_pending:
        logger.warning(
            "[mobaide-agent] Pairing required — the mobile app will pair "
            "this agent automatically once the QR is scanned. "
            "Run `mobaide-agent show-qr` to re-render.",
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("agent.starting")
        await app.state.session_manager.start_liveness_monitor()
        app.state.pairing_task = None
        if relay_cfg is not None and not pairing_pending:
            from mobaide.relay.client import RelayClient

            app.state.relay_client = RelayClient(
                config=relay_cfg,
                session_manager=app.state.session_manager,
                event_bus=app.state.event_bus,
                broadcaster=app.state.broadcaster,
                app_state=app.state,
            )
            await app.state.relay_client.start()
            app.state.updater = _build_updater(app)
            if app.state.updater is not None:
                app.state.relay_client.set_manifest_update_callback(
                    app.state.updater.consider_manifest
                )
        elif relay_cfg is not None and pairing_pending:
            app.state.relay_client = None
            app.state.updater = None
            from mobaide.agent.pairing_monitor import (
                default_layout,
                run_pairing_monitor,
            )

            def _on_paired() -> None:
                from mobaide.__main__ import _try_restart_service

                logger.info("agent.pairing_complete restart_supervisor=true")
                _try_restart_service()

            layout = default_layout()
            app.state.pairing_task = asyncio.create_task(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url=relay_cfg.server_url,
                    current_agent_id=relay_cfg.agent_id or None,
                    on_paired=_on_paired,
                ),
                name="pairing-monitor",
            )
        else:
            app.state.relay_client = None
            app.state.updater = None
        yield
        logger.info("agent.shutting_down")
        pairing_task = getattr(app.state, "pairing_task", None)
        if pairing_task is not None and not pairing_task.done():
            pairing_task.cancel()
            try:
                await pairing_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("agent.pairing_task_failed: %s", exc)
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

    app = FastAPI(title="Mobaide Agent", version=__version__, lifespan=lifespan)

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
    app.state.pairing_pending = pairing_pending

    # Mutable, file-backed projects store (seeds from PROJECTS env var on
    # first boot, then file wins forever). Every downstream consumer
    # (SessionManager, auto-scan, relay proxy, WS snapshot) reads through
    # this single reference.
    projects_store = ProjectsStore(config.projects_store_path)
    projects_store.load(seed_json=config.projects)
    app.state.projects_store = projects_store

    # Worktree manager is always wired so Standard-mode sessions can create
    # worktrees regardless of whether auto-scan is configured. Auto Scan
    # conventions (``worktree_base``, ``branch_prefix``, ``main_branch``) come
    # from AutoScanConfig when present; otherwise sensible defaults apply.
    auto_scan_cfg = config.auto_scan_config or AutoScanConfig()
    prefix = auto_scan_cfg.branch_prefix or "feature/claude"
    worktree_mgr = WorktreeManager(
        worktree_base=auto_scan_cfg.worktree_base,
        branch_prefix=prefix,
        main_branch=auto_scan_cfg.main_branch,
    )
    app.state.worktree_manager = worktree_mgr
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
    app.include_router(git_router)
    app.include_router(ws_session_io_router)
    app.include_router(ws_session_list_router)
    app.include_router(ws_events_router)
    app.include_router(autoscan_router)
    app.include_router(autoscan_ws_router)
    app.include_router(updater_router)

    # Health endpoint (no auth)
    @app.get("/api/health", tags=["health"])
    async def health():
        sm = app.state.session_manager
        autoscan_mgr = getattr(app.state, "autoscan_manager", None)
        result = {
            "status": "ok",
            "sessions": len(sm.list_sessions()),
            "max_sessions": sm.max_sessions,
            "pairing_pending": getattr(app.state, "pairing_pending", False),
        }
        if autoscan_mgr is not None:
            result["auto_scans_active"] = len(autoscan_mgr.list())
        return result

    @app.get("/api/version", tags=["version"])
    async def version_info() -> dict[str, object]:
        relay_client = getattr(app.state, "relay_client", None)
        manifest = relay_client.latest_manifest if relay_client else None
        return {
            "name": "mobaide_agent",
            "server_version": __version__,
            "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "min_client_version": MIN_CLIENT_PROTOCOL_VERSION,
            "capabilities": {},
            "latest_agent_version": (manifest or {}).get("version"),
            "manifest_url": (manifest or {}).get("manifest_url"),
            "manifest_signature": (manifest or {}).get("manifest_signature"),
        }

    return app

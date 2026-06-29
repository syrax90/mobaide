"""FastAPI application factory for the Central Server."""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agents.service import AgentService
from .auth.email import build_email_sender
from .auth.rate_limit import ResendRateLimiter
from .auth.service import AuthService
from .config import CentralServerConfig
from .db.database import Database
from .legal.loader import render_privacy, resolve_legal_dir
from .templates.service import TemplateService
from .logging_setup import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
    shutdown_logging,
)
from .manifest.store import ManifestStore
from .pairing.service import PairingService
from .pairing.ws_handlers import PairingWsRegistry
from .relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from .relay.routing import RoutingTable

logger = get_logger(__name__)

_HTTP_LOG_SKIP_PATHS = frozenset({"/api/health"})

# Static files and templates live alongside the package directory in a source
# checkout, or alongside the working directory when packaged into a Docker
# image that only ships site-packages.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CWD_STATIC = Path.cwd() / "static"
_STATIC_DIR = _CWD_STATIC if _CWD_STATIC.is_dir() else _PROJECT_ROOT / "static"


def _generate_correlation_id() -> str:
    return secrets.token_hex(16)


def create_app(config: CentralServerConfig | None = None) -> FastAPI:
    """Build and return the Central Server FastAPI application."""
    if config is None:
        load_dotenv()
        config = CentralServerConfig.from_env()

    configure_logging(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("central_server.starting")

        db = Database(config.db_path)
        await db.connect()
        app.state.db = db

        app.state.routing = RoutingTable()
        app.state.manifest_store = ManifestStore(config.manifest_store_path)
        # Warm the on-disk manifest cache before the first /api/version
        # request so the synchronous file read never blocks the asyncio
        # event loop after startup.
        app.state.manifest_store.read()
        email_sender = build_email_sender(config)
        resend_limiter = ResendRateLimiter(
            cooldown_seconds=config.email_verification_resend_cooldown_seconds,
            hourly_cap=config.email_verification_resend_hourly_cap,
        )
        reset_email_limiter = ResendRateLimiter(
            cooldown_seconds=config.password_reset_email_cooldown_seconds,
            hourly_cap=config.password_reset_email_hourly_cap,
        )
        # Per-IP limiter has no cooldown — only the rolling hourly cap. Same
        # in-memory caveat as resend_limiter (documented in rate_limit.py).
        reset_ip_limiter = ResendRateLimiter(
            cooldown_seconds=0,
            hourly_cap=config.password_reset_ip_hourly_cap,
        )
        app.state.email_sender = email_sender
        app.state.resend_limiter = resend_limiter
        app.state.reset_email_limiter = reset_email_limiter
        app.state.reset_ip_limiter = reset_ip_limiter
        app.state.auth_service = AuthService(
            config,
            db,
            email_sender=email_sender,
            resend_limiter=resend_limiter,
            reset_email_limiter=reset_email_limiter,
            routing=app.state.routing,
        )
        await app.state.auth_service.seed_admin_user()
        app.state.pairing_service = PairingService(db)
        app.state.pairing_ws_registry = PairingWsRegistry()
        app.state.agent_service = AgentService(db, app.state.routing)
        app.state.template_service = TemplateService(db, app.state.routing)

        from .relay.mobile_proxy import MobileProxy

        app.state.mobile_proxy = MobileProxy(app.state.routing)

        # Runs every 60 s; interval is not surfaced in the function signature.
        cleanup_task = asyncio.create_task(
            _token_cleanup_loop(db), name="token-cleanup"
        )
        deletion_sweeper_task = asyncio.create_task(
            _account_deletion_sweeper_loop(app.state.auth_service, config),
            name="account-deletion-sweeper",
        )
        heartbeat_flush_task = asyncio.create_task(
            _heartbeat_flush_loop(db, app.state.routing, config),
            name="heartbeat-flush",
        )

        yield

        logger.info("central_server.shutting_down")
        cleanup_task.cancel()
        deletion_sweeper_task.cancel()
        heartbeat_flush_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            await deletion_sweeper_task
        except asyncio.CancelledError:
            pass
        try:
            await heartbeat_flush_task
        except asyncio.CancelledError:
            pass
        # Final sweep after the loop is cancelled (no interleaving) and
        # before db.close() so still-connected agents' freshest heartbeats
        # survive the restart.
        try:
            await _flush_agent_heartbeats(db, app.state.routing)
        except Exception as exc:
            logger.exception("heartbeat_flush.shutdown_error: %s", exc)
        app.state.auth_service.close()
        await db.close()
        shutdown_logging()

    app = FastAPI(
        title="Central Server",
        version=__version__,
        lifespan=lifespan,
    )

    # Middleware is executed in reverse registration order.
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
        logger.info(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        clear_request_context()
        return response

    app.add_middleware(
        CorrelationIdMiddleware,
        header_name="X-Request-ID",
        generator=_generate_correlation_id,
    )

    app.state.config = config
    # Render the privacy policy once at startup; the route handler then
    # returns the cached HTML via the Jinja shell template. Both the body
    # HTML and a separate H2-only TOC are cached so the sticky-sidebar
    # template doesn't need to re-parse the document per request.
    _rendered_privacy = render_privacy(resolve_legal_dir())
    app.state.privacy_html = _rendered_privacy["html"]
    app.state.privacy_toc_html = _rendered_privacy["toc"]

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from .agents.router import router as agents_router
    from .auth.router import router as auth_router
    from .dashboard.router import router as dashboard_router
    from .devices.router import router as devices_router
    from .legal.router import router as legal_router
    from .manifest.router import router as manifest_router
    from .pairing.router import router as pairing_router
    from .portal.router import router as portal_router
    from .relay.agent_ws import router as agent_ws_router
    from .relay.mobile_proxy import router as mobile_proxy_router
    from .relay.mobile_ws import router as mobile_ws_router
    from .relay.user_ws import router as user_ws_router
    from .templates.router import router as templates_router

    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(agent_ws_router)
    app.include_router(mobile_ws_router)
    app.include_router(mobile_proxy_router)
    app.include_router(user_ws_router)
    app.include_router(pairing_router)
    app.include_router(devices_router)
    app.include_router(dashboard_router)
    app.include_router(portal_router)
    app.include_router(legal_router)
    app.include_router(manifest_router)
    app.include_router(templates_router)

    # Unauthenticated — intentionally public for load-balancer probes.
    @app.get("/api/health", tags=["health"])
    async def health():
        routing = app.state.routing
        db = app.state.db
        agents = await db.list_agents()
        return {
            "status": "ok",
            "agents_online": len(routing.list_online_agents()),
            "agents_registered": len(agents),
        }

    @app.get("/api/version", tags=["version"])
    async def version_info() -> dict[str, object]:
        manifest = app.state.manifest_store.read()
        return {
            "name": "central_server",
            "server_version": __version__,
            "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "min_client_version": MIN_CLIENT_PROTOCOL_VERSION,
            "capabilities": {},
            "latest_agent_version": manifest.body.get("version") if manifest else None,
        }

    return app


async def _token_cleanup_loop(db: Database) -> None:
    """Periodically clean up expired pairing and email-verification tokens."""
    while True:
        try:
            await asyncio.sleep(60)
            deleted = await db.cleanup_expired_tokens()
            if deleted:
                logger.info("token_cleanup.done deleted=%d", deleted)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("token_cleanup.error: %s", exc)


async def _account_deletion_sweeper_loop(
    auth_service: AuthService, config: CentralServerConfig
) -> None:
    """Periodically purge users whose soft-delete grace window has elapsed.

    Interval defaults to 1 h — short enough that final-purge SLA stays in the
    hours range, long enough that steady-state load is negligible. Per-user
    safety (refetch + re-check + per-user try/except) lives inside
    ``AuthService.run_deletion_sweep``; this loop just owns the schedule.
    """
    interval = max(60, config.deletion_sweeper_interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval)
            await auth_service.run_deletion_sweep()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("deletion_sweeper.error: %s", exc)


async def _flush_agent_heartbeats(db: Database, routing: RoutingTable) -> int:
    """One sweep: persist every online agent's last heartbeat in one commit.

    The rows are built synchronously from the routing snapshot before any
    await, so agents disconnecting mid-sweep cannot mutate the batch; an
    UPDATE for a since-deleted agent row is a harmless no-op.
    """
    conns = routing.list_online_agents()
    if not conns:
        return 0
    await db.bulk_touch_agents_last_seen(
        [(conn.agent_id, conn.last_heartbeat) for conn in conns]
    )
    return len(conns)


async def _heartbeat_flush_loop(
    db: Database, routing: RoutingTable, config: CentralServerConfig
) -> None:
    """Periodically batch-flush in-memory agent heartbeats to the DB.

    Heartbeats no longer touch the DB individually (one UPDATE+commit per
    agent per 15 s did not scale); this loop bounds ``agents.last_seen_at``
    staleness for ONLINE agents by the flush interval. In-memory routing
    state remains the authority for presence; offline timestamps stay exact
    because agent_ws flushes the final heartbeat on disconnect.
    """
    interval = max(1, config.heartbeat_flush_interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval)
            await _flush_agent_heartbeats(db, routing)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("heartbeat_flush.error: %s", exc)

"""FastAPI application factory for the Central Server."""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .agents.service import AgentService
from .auth.dependencies import get_api_user, get_dashboard_user
from .auth.email import build_email_sender
from .auth.rate_limit import ResendRateLimiter
from .auth.service import AuthService
from .config import CentralServerConfig
from .custom_agent_types.service import CustomAgentTypeService
from .db.database import Database
from .legal.loader import render_privacy, resolve_legal_dir
from .marketing.loader import (
    read_favicon,
    render_landing,
    render_robots,
    render_sitemap,
)
from .shortcuts.service import ShortcutService
from .static_files import CachedStaticFiles, resolve_static_dir
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

_OPENAPI_PATH = "/openapi.json"
_DOCS_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE")


def _generate_correlation_id() -> str:
    return secrets.token_hex(16)


async def _require_docs_admin(request: Request) -> None:
    """Allow only platform admins through to the OpenAPI routes.

    Accepts either an admin ``Authorization: Bearer`` token (for ``curl``) or
    the ``dashboard_token`` cookie an admin already holds after signing in at
    ``/dashboard/login`` — the same session Swagger UI and ReDoc send along
    with their same-origin fetch of the schema.

    Every rejection is a 404, never a 401/403, so an ordinary probe cannot
    tell a deployment with docs enabled-but-gated from one with
    ``ENABLE_API_DOCS`` off, where the routes do not exist at all.
    ``get_dashboard_user`` signals failure with a 303 redirect to the login
    page, so ``HTTPException`` covers the ordinary rejection path for both
    dependencies. Anything else fails closed the same way — a 500 here would
    itself disclose that the route exists — but is logged, since reaching it
    means an auth dependency broke rather than merely refused.

    ``_register_api_docs`` is what extends that 404 across the methods and
    spellings an ordinary probe uses; see its note on why a bare ``@app.get``
    is not enough. Two residuals stay observable and are accepted: a
    non-standard verb draws a 405, and a repeated trailing slash (``/docs//``)
    draws Starlette's 307, because ``redirect_slashes`` ``rstrip``s to the
    registered path and no set of explicit spellings can pre-empt it. So the
    guarantee is "no ordinary probe reveals the flag", not equivalence. What
    is absolute is that the schema itself never reaches a non-admin.
    """
    try:
        user = (
            await get_api_user(request)
            if request.headers.get("Authorization")
            else await get_dashboard_user(request)
        )
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    except Exception:
        logger.exception("api_docs.auth_error")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


async def _health_is_admin(request: Request) -> bool:
    """True iff the caller is an authenticated platform admin.

    Same dual Bearer/cookie resolution as ``_require_docs_admin``, but never
    raises: ``/api/health`` must keep answering 200 with the bare status for
    anonymous load-balancer, Docker, and deploy-script probes. Any auth
    failure — missing, invalid, or non-admin — just means the agent counts
    are omitted, not that the request is rejected.
    """
    try:
        user = (
            await get_api_user(request)
            if request.headers.get("Authorization")
            else await get_dashboard_user(request)
        )
    except HTTPException:
        return False
    return user.role == "admin"


def _register_api_docs(app: FastAPI) -> None:
    """Register admin-gated ``/openapi.json``, ``/docs`` and ``/redoc``.

    Only called when ``ENABLE_API_DOCS`` is set. The app is always constructed
    with ``docs_url=None, redoc_url=None, openapi_url=None``, so FastAPI's own
    unauthenticated doc routes are never registered in any environment.

    Each path is registered for every standard method and both trailing-slash
    spellings, because Starlette resolves method mismatches and slash
    redirects in the router, before dependencies run. A bare ``@app.get``
    would answer 405 to ``HEAD``/``POST`` and 307 to ``/docs/`` while the flag
    is on, versus 404 for all of them while it is off — either of which hands
    an anonymous caller the flag's value.
    """
    docs_dependencies = [Depends(_require_docs_admin)]

    async def openapi_schema() -> JSONResponse:
        return JSONResponse(app.openapi())

    async def swagger_ui() -> HTMLResponse:
        # oauth2_redirect_url must be None: FastAPI only registers
        # /docs/oauth2-redirect when docs_url is set, so the default would
        # point Swagger UI at a 404.
        return get_swagger_ui_html(
            openapi_url=_OPENAPI_PATH,
            title=f"{app.title} — API docs",
            oauth2_redirect_url=None,
        )

    async def redoc_ui() -> HTMLResponse:
        return get_redoc_html(
            openapi_url=_OPENAPI_PATH,
            title=f"{app.title} — API docs",
        )

    for path, endpoint in (
        (_OPENAPI_PATH, openapi_schema),
        ("/docs", swagger_ui),
        ("/redoc", redoc_ui),
    ):
        for spelling in (path, f"{path}/"):
            app.add_api_route(
                spelling,
                endpoint,
                methods=list(_DOCS_METHODS),
                include_in_schema=False,
                dependencies=docs_dependencies,
            )


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
        app.state.shortcut_service = ShortcutService(db, app.state.routing)
        app.state.custom_agent_type_service = CustomAgentTypeService(
            db, app.state.routing
        )

        from .relay.mobile_proxy import MobileProxy

        app.state.mobile_proxy = MobileProxy(app.state.routing)

        from .push import PushDispatcher

        app.state.push_dispatcher = PushDispatcher.from_config(db, config)

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
        await app.state.push_dispatcher.aclose()
        app.state.auth_service.close()
        await db.close()
        shutdown_logging()

    app = FastAPI(
        title="Central Server",
        version=__version__,
        lifespan=lifespan,
        # Default-deny: FastAPI would otherwise publish the full route,
        # parameter and model map to anonymous callers. _register_api_docs
        # puts them back behind an admin session when ENABLE_API_DOCS is set.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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

    # Same pre-render-once contract as the privacy policy above. `site_base_url`
    # is the marketing origin, deliberately not `public_base_url` — that one is
    # the relay origin embedded in QR pairing payloads (api.mobaide.com in
    # production), which would put the wrong host in canonical and OG URLs.
    _site_origin = config.site_base_url.rstrip("/")
    app.state.marketing_html = render_landing(_site_origin, __version__)
    app.state.marketing_robots = render_robots(_site_origin)
    app.state.marketing_sitemap = render_sitemap(_site_origin)

    _static_dir = resolve_static_dir()
    # Read once here rather than per request: /favicon.ico is anonymous and
    # crawler-facing, and a per-request stat would block the relay's event loop.
    app.state.marketing_favicon = read_favicon(_static_dir)
    if _static_dir.exists():
        app.mount(
            "/static",
            CachedStaticFiles(directory=str(_static_dir)),
            name="static",
        )

    from .agents.router import router as agents_router
    from .auth.router import router as auth_router
    from .custom_agent_types.router import router as custom_agent_types_router
    from .dashboard.router import router as dashboard_router
    from .devices.router import router as devices_router
    from .legal.router import router as legal_router
    from .manifest.router import router as manifest_router
    from .marketing.router import router as marketing_router
    from .pairing.router import router as pairing_router
    from .portal.router import router as portal_router
    from .relay.agent_ws import router as agent_ws_router
    from .relay.mobile_proxy import router as mobile_proxy_router
    from .relay.mobile_ws import router as mobile_ws_router
    from .relay.user_ws import router as user_ws_router
    from .shortcuts.router import router as shortcuts_router
    from .templates.router import router as templates_router

    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(agent_ws_router)
    app.include_router(mobile_ws_router)
    app.include_router(mobile_proxy_router)
    app.include_router(user_ws_router)
    app.include_router(pairing_router)
    app.include_router(devices_router)
    # The dashboard, portal, legal and marketing routers serve HTML and HTMX
    # fragments to browsers, not an API. Keeping them out of the schema means an
    # enabled admin docs page describes the actual JSON surface instead of the
    # browser-only one. include_in_schema has no effect on routing.
    app.include_router(dashboard_router, include_in_schema=False)
    app.include_router(portal_router, include_in_schema=False)
    app.include_router(legal_router, include_in_schema=False)
    app.include_router(marketing_router, include_in_schema=False)
    app.include_router(manifest_router)
    app.include_router(templates_router)
    app.include_router(shortcuts_router)
    app.include_router(custom_agent_types_router)

    if config.enable_api_docs:
        _register_api_docs(app)

    # Bare status is intentionally public for load-balancer probes. Agent
    # counts (install/concurrency numbers) require an admin bearer token or
    # dashboard session — see _health_is_admin.
    @app.get("/api/health", tags=["health"])
    async def health(request: Request):
        if not await _health_is_admin(request):
            return {"status": "ok"}
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

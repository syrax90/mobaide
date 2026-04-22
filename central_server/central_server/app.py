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

from .agents.service import AgentService
from .auth.email import EmailSender
from .auth.rate_limit import ResendRateLimiter
from .auth.service import AuthService
from .config import CentralServerConfig
from .db.database import Database
from .logging_setup import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
    shutdown_logging,
)
from .pairing.service import PairingService
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
        email_sender = EmailSender(config)
        resend_limiter = ResendRateLimiter(
            cooldown_seconds=config.email_verification_resend_cooldown_seconds,
            hourly_cap=config.email_verification_resend_hourly_cap,
        )
        app.state.email_sender = email_sender
        app.state.resend_limiter = resend_limiter
        app.state.auth_service = AuthService(
            config, db, email_sender=email_sender, resend_limiter=resend_limiter
        )
        await app.state.auth_service.seed_admin_user()
        app.state.pairing_service = PairingService(db)
        app.state.agent_service = AgentService(db, app.state.routing)

        from .relay.mobile_proxy import MobileProxy

        app.state.mobile_proxy = MobileProxy(app.state.routing)

        # Runs every 60 s; interval is not surfaced in the function signature.
        cleanup_task = asyncio.create_task(
            _token_cleanup_loop(db), name="token-cleanup"
        )

        yield

        logger.info("central_server.shutting_down")
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await db.close()
        shutdown_logging()

    app = FastAPI(
        title="Central Server",
        version="0.1.0",
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

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from .agents.router import router as agents_router
    from .auth.router import router as auth_router
    from .dashboard.router import router as dashboard_router
    from .pairing.router import router as pairing_router
    from .relay.agent_ws import router as agent_ws_router
    from .relay.mobile_proxy import router as mobile_proxy_router
    from .relay.mobile_ws import router as mobile_ws_router
    from .relay.user_ws import router as user_ws_router

    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(agent_ws_router)
    app.include_router(mobile_ws_router)
    app.include_router(mobile_proxy_router)
    app.include_router(user_ws_router)
    app.include_router(pairing_router)
    app.include_router(dashboard_router)

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

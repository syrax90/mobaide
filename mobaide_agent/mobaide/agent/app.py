from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mobaide import __version__
from mobaide.core import (
    AntigravityLauncher,
    ClaudeLauncher,
    CodexLauncher,
    FakePtyBackend,
    ProjectsStore,
    SessionManager,
    create_pty_backend,
)
from mobaide.core.cli_launcher import (
    await_model_cache_warmup,
    build_cli_capabilities,
    warm_cli_model_caches,
)
from mobaide.core.hook_settings import sweep_hooks_dir
from mobaide.core.macos_access import full_disk_access
from mobaide.linear.config import AutoScanConfig
from mobaide.linear.rate_limiter import LinearRateLimiter
from mobaide.linear.scanner import AutoScanSessionManager
from mobaide.linear.worktree import WorktreeManager
from mobaide.relay.config import RelayConfig
from mobaide.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)

from .auth.jwt_service import JwtService
from .auth.router import router as auth_router
from .auth.user_store import UserStore
from .autoscan.router import router as autoscan_router
from .autoscan.router import ws_router as autoscan_ws_router
from .codex.router import router as codex_router
from .config import AgentConfig
from .git.changes_jobs import job_registry
from .git.changes_models import GIT_CAPABILITIES
from .git.router import router as git_router
from .git.session_router import router as session_git_router
from .hooks.router import router as hooks_router
from .install_paths import (
    default_prefix,
    manifest_url_from_relay_server_url,
    read_trust_public_key,
)
from .logging import bind_request_context, clear_request_context, get_logger
from .logging_setup import configure_logging, shutdown_logging
from .projects.router import router as projects_router
from .sessions.router import router as sessions_router
from .events import EventBus
from .fs.models import FS_CAPABILITIES
from .fs.router import router as fs_router
from .update_check import (
    PERIODIC_POLL_INTERVAL_SECONDS,
    run_https_update_check,
    run_periodic_check_loop,
)
from .updater_router import router as updater_router
from .ws.event_fanout import SessionListBroadcaster
from .ws.events import router as ws_events_router
from .ws.session_io import (
    ensure_fanout,
    router as ws_session_io_router,
    stop_fanout,
)
from .ws.session_list import router as ws_session_list_router

if TYPE_CHECKING:
    from mobaide.agent.updater import InstallLayout

logger = get_logger(__name__)

# Endpoints excluded from per-request HTTP middleware logging (high-frequency, low-value).
_HTTP_LOG_SKIP_PATHS = frozenset({"/api/health"})


def _generate_correlation_id() -> str:
    """16-byte hex (W3C trace-id shape) — forward-compatible with OpenTelemetry."""
    return secrets.token_hex(16)


def _is_running_managed_release(layout: InstallLayout) -> bool:
    """True when the running interpreter lives under ``<prefix>/releases``.

    The installed agent runs as ``<prefix>/releases/<v>/venv/bin/python`` (the
    launcher execs that interpreter), so its ``sys.prefix`` resolves under
    ``layout.releases_dir``. A developer / source-checkout run uses an
    interpreter outside that tree. Both sides are ``resolve()``-d so a
    symlinked prefix (Homebrew ``/usr/local``) still compares correctly.
    """
    try:
        running = Path(sys.prefix).resolve()
        releases = layout.releases_dir.resolve()
    except OSError:
        return False
    return running.is_relative_to(releases)


def _build_updater(app: FastAPI):
    """Return a ``StagedBinaryUpdater`` bound to this agent's session
    manager, or ``None`` when the auto-updater must stay disabled.

    Disabled (fail-closed; relay manifests are recorded but never applied) when
    either:

    * the install layout has no ``<prefix>/trust/central_server.pub`` (a
      developer checkout with no pinned trust key); or
    * the running interpreter is not the prefix's managed release venv. This
      stops a dev / source run — which shares ``default_prefix()`` and keys the
      updater off ``__version__`` — from staging over and destroying the
      installed release when its version differs from the published manifest.
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
    if not _is_running_managed_release(layout):
        logger.info(
            "updater.disabled reason=unmanaged_interpreter sys_prefix=%s "
            "releases_dir=%s",
            sys.prefix, layout.releases_dir,
        )
        return None
    return StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=app.state.session_manager,
        current_version=__version__,
    )


def _resolve_manifest_url(relay_cfg: RelayConfig) -> str | None:
    """Derive the HTTPS manifest URL from the relay's server URL.

    Returns ``None`` (and logs at INFO) when derivation fails — for
    example a developer-mode ``ws://localhost`` config without the
    insecure-bootstrap escape hatch enabled. The HTTPS bootstrap path
    is then silently disabled; the relay path remains the sole update
    channel for that install.
    """
    try:
        return manifest_url_from_relay_server_url(relay_cfg.server_url)
    except ValueError as exc:
        logger.info(
            "update_check.disabled reason=%s relay_url=%s",
            exc, relay_cfg.server_url,
        )
        return None


def _resolve_manifest_url_with_hint(
    relay_cfg: RelayConfig, hint: str | None,
) -> str | None:
    """Combine the server-provided hint with the env-derived URL.

    The server's ``incompatible`` frame may include a ``manifest_url``
    hint — accepted only as a path beginning with a single ``/`` and
    resolved against the relay's HTTPS origin. Absolute URLs, protocol-
    relative (``//host/...``) hints, and any hint containing ASCII
    whitespace or NUL are rejected so a misbehaving server cannot
    redirect the bootstrap fetch to an arbitrary host. The whitespace
    guard matters because ``urljoin`` strips leading ASCII whitespace
    per the WHATWG URL spec before parsing — a hint like
    ``"/\\t/evil.example/path"`` would otherwise pass a naive
    ``startswith("/")`` + ``not startswith("//")`` check yet still
    resolve to ``https://evil.example/path``. The signature remains
    the trust anchor either way, so a rejected hint at worst falls
    through to the env-derived URL.
    """
    derived = _resolve_manifest_url(relay_cfg)
    if not hint:
        return derived
    if any(c.isspace() for c in hint) or "\x00" in hint:
        logger.warning(
            "relay.manifest_url_hint_rejected hint=%r (whitespace or NUL "
            "would be stripped by urljoin and could smuggle a protocol-"
            "relative URL past the path-only guard)",
            hint,
        )
        return derived
    if not hint.startswith("/") or hint.startswith("//"):
        logger.warning(
            "relay.manifest_url_hint_rejected hint=%r (must be path-only, "
            "starting with a single '/')",
            hint,
        )
        return derived
    if derived is None:
        return None
    from urllib.parse import urljoin

    return urljoin(derived, hint)


async def _start_relay_in_process(
    app: FastAPI, relay_cfg: RelayConfig,
) -> None:
    """Construct, start, and wire up the relay client + updater on ``app.state``.

    Single seam shared by the lifespan's cold-boot branch (agent boots
    with credentials already in ``.env``) and the in-process pairing
    transition in ``_on_paired`` (agent boots in pairing-pending state,
    receives the QR claim, then comes online without a process restart).
    Keeping both paths through one helper means the freshly-paired agent
    reaches the same operational state as one that booted fully
    configured — no "lifespan started the relay this way, post-pair
    started it that way" drift.

    Startup ordering:

    1. Build the staged-binary updater (returns ``None`` for
       developer-checkout installs that lack a pinned trust key — the
       auto-updater stays disabled, fail-closed).
    2. **Pre-connect HTTPS bootstrap** — when the updater exists and a
       manifest URL can be derived from ``RELAY_SERVER_URL``, fetch the
       signed manifest over HTTPS and apply it if newer than the
       running version. If an update is staged, the updater invokes
       ``sys.exit(EXIT_RESTART_REQUESTED)`` from within
       ``consider_manifest`` and the supervisor respawns into the
       fresh release; the relay client is never started on this run.
       This is the side-channel that breaks the chicken-and-egg loop
       when a protocol bump strands an older agent.
    3. Wire the relay-side manifest callback (in-band push from the
       running tunnel) and the protocol-incompatible callback (HTTPS
       fallback triggered by close-code-4002 rejection) onto the
       relay client, then ``start()`` it.
    4. Atomic ``app.state.*`` assignment + clear ``pairing_pending``.
    5. Spawn the 60-minute background HTTPS poll so the agent
       reconciles any manifest the relay missed.
    """
    from mobaide.relay.client import ALLOW_INSECURE_WS_ENV, RelayClient

    updater = _build_updater(app)
    manifest_url = _resolve_manifest_url(relay_cfg) if updater is not None else None

    if updater is not None and manifest_url is not None:
        outcome = await run_https_update_check(updater, manifest_url)
        # Outcome "staged" means consider_manifest has already invoked
        # sys.exit(75); under test injection sys.exit is monkeypatched so
        # we fall through and continue startup. Any other outcome
        # (no_change, deferred, signature_failed, unreachable) keeps the
        # cold-boot path going.
        logger.info(
            "agent.pre_connect_update_check outcome=%s url=%s",
            outcome, manifest_url,
        )

    # Allow a cleartext ws:// relay only under an explicit opt-in: MOBAIDE_ENV=test
    # (disposable local E2E) or MOBAIDE_AGENT_ALLOW_INSECURE_WS=1. Otherwise a
    # ws:// server_url is refused inside RelayClient(); wss:// is always secure.
    allow_insecure_ws = (
        app.state.config.is_test_mode
        or os.environ.get(ALLOW_INSECURE_WS_ENV) == "1"
    )
    relay_client = RelayClient(
        config=relay_cfg,
        session_manager=app.state.session_manager,
        event_bus=app.state.event_bus,
        broadcaster=app.state.broadcaster,
        app_state=app.state,
        allow_insecure_ws=allow_insecure_ws,
    )
    if updater is not None:
        relay_client.set_manifest_update_callback(updater.consider_manifest)

        async def _on_protocol_incompatible(hint: str | None) -> None:
            url = _resolve_manifest_url_with_hint(relay_cfg, hint)
            if url is None:
                logger.warning(
                    "relay.protocol_incompatible_no_manifest_url hint=%r",
                    hint,
                )
                return
            await run_https_update_check(updater, url, force=True)

        relay_client.set_protocol_incompatible_callback(_on_protocol_incompatible)

    # Only an installed release can remove itself. A test-mode run still
    # advertises the capability so a local stack can exercise the flow, but
    # handle_uninstall_request deletes nothing for it.
    from mobaide.agent.self_uninstall import handle_uninstall_request
    from mobaide.agent.updater import InstallLayout

    uninstall_layout = InstallLayout(prefix=default_prefix())
    installed = _is_running_managed_release(uninstall_layout)
    if installed or app.state.config.is_test_mode:

        async def _on_uninstall_requested() -> None:
            await handle_uninstall_request(
                uninstall_layout.prefix, installed=installed,
            )

        relay_client.set_uninstall_callback(_on_uninstall_requested)

    await relay_client.start()
    app.state.relay_client = relay_client
    app.state.updater = updater
    app.state.pairing_pending = False

    poll_task = getattr(app.state, "https_poll_task", None)
    if poll_task is not None and not poll_task.done():
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("agent.https_poll_task_cancel_failed: %s", exc)
    app.state.https_poll_task = None
    if updater is not None and manifest_url is not None:
        app.state.https_poll_task = asyncio.create_task(
            run_periodic_check_loop(
                updater, manifest_url,
                interval_seconds=PERIODIC_POLL_INTERVAL_SECONDS,
            ),
            name="https-update-poll",
        )


def _resolve_env_file() -> Path | None:
    """Return the canonical agent ``.env`` path if one exists.

    Two candidates: the install-prefix env ``<prefix>/mobaide_agent/.env`` and
    the developer-checkout env ``<package_root>/../.env`` (``__file__``'s
    ``parents[2]/.env``). The latter only exists in editable / dev installs
    where ``__file__`` resolves into the repo source tree; for wheel installs
    ``parents[2]`` is ``site-packages`` and it silently misses.

    Which one is tried first depends on whether ``MOBAIDE_AGENT_PREFIX`` is set
    in the agent's *process* environment:

    * **Prefix set** — the install layout wins: the prefix env first, then the
      developer-checkout fallback so an editable install with an unpopulated
      prefix still boots. The POSIX service supervisors export the prefix into
      the agent's environment (systemd ``Environment=``, launchd plist
      ``EnvironmentVariables``), so the installed agent always takes this branch.
    * **Prefix unset** — an uncontrolled ``python -m mobaide`` from a source
      tree. The checkout's own env wins over the OS-default install prefix, so
      a source-tree agent keeps its own identity instead of silently adopting a
      co-installed agent's ``<os-default-prefix>/mobaide_agent/.env`` — what
      makes a dev agent valid alongside the script-installed one. The Windows
      Scheduled Task install also lands here: it passes ``--prefix`` to the
      launcher but does not export ``MOBAIDE_AGENT_PREFIX`` to the agent
      process. That stays correct because every non-dev install is wheel-based
      and ships no ``<site-packages>/.env`` for the developer candidate to
      match, so resolution falls through to the install prefix exactly as
      before; only an editable/source checkout has a checkout ``.env`` to
      prefer. (Empty string counts as unset, mirroring ``default_prefix``'s
      ``if explicit:`` check.)

    Returning ``None`` when both candidates miss lets pydantic-settings fall
    back to process-environment vars (which is what tests rely on).
    """
    from mobaide.agent._env_writer import env_path_for_prefix

    prefix_env = env_path_for_prefix(default_prefix())
    dev_env = Path(__file__).resolve().parents[2] / ".env"

    if os.environ.get("MOBAIDE_AGENT_PREFIX"):
        search_order = (prefix_env, dev_env)
    else:
        search_order = (dev_env, prefix_env)

    for candidate in search_order:
        if candidate.exists():
            return candidate
    return None


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
        # macOS-only: ``False`` means no Full Disk Access, so protected-folder
        # reads pop a host-desktop TCC modal the remote client can't see (the
        # per-session startup-stall watchdog surfaces that). Logged once.
        fda = await asyncio.to_thread(full_disk_access)
        if fda is not None:
            logger.info("agent.macos_full_disk_access", granted=fda)
        # Clear hook settings files orphaned by a prior run (crashed sessions
        # never reach the SessionEnd delete). Scoped to this agent's port.
        sweep_hooks_dir(config.port)
        await app.state.session_manager.start_liveness_monitor()
        # Warm the per-CLI model-list caches off the request path: /api/version
        # reads the in-memory caches (waiting at most a short bound for this
        # task if a probe lands mid-warmup — see await_model_cache_warmup), so
        # a slow `agy models` / `codex debug models` can neither delay boot nor
        # stall requests.
        app.state.cli_models_warmup = asyncio.create_task(
            warm_cli_model_caches(app.state.cli_launchers),
            name="cli-models-warmup",
        )
        app.state.pairing_task = None
        if relay_cfg is not None and not pairing_pending:
            await _start_relay_in_process(app, relay_cfg)
        elif relay_cfg is not None and pairing_pending:
            app.state.relay_client = None
            app.state.updater = None
            app.state.https_poll_task = None
            from mobaide.agent.pairing_monitor import (
                default_layout,
                run_pairing_monitor,
            )
            from mobaide.agent.pairing_ws import PairingClaim

            # Bind a non-Optional alias so the closure below doesn't need
            # ``assert relay_cfg is not None`` on every attribute access —
            # the ``elif`` already proved it. Without this, Pyright loses
            # the narrowing across the nested function boundary.
            boot_relay_cfg: RelayConfig = relay_cfg

            async def _on_paired(claim: PairingClaim) -> None:
                # In-process state transition after a successful QR pair.
                # The .env write has already happened in
                # ``run_pairing_monitor`` before this callback runs
                # (persisted across restarts as a fallback). This
                # callback ALSO constructs and starts the relay client
                # in the running process so the agent reaches
                # operational state in ~1-2 seconds without any process
                # exit. ``app.state.pairing_pending`` flips to False
                # atomically with the relay-client assignment inside
                # ``_start_relay_in_process``, so ``/api/health`` and the
                # mobile app see the agent as paired immediately.
                #
                # Previously this scheduled ``sys.exit(75)`` and relied
                # on the supervisor (systemd / launchd / Windows service
                # host) to restart the agent so the next boot would read
                # the just-written .env and bring up the relay client.
                # That dependency chain — propagate exit 75 from a
                # call_later callback up through asyncio → uvicorn →
                # mobaide.run → mobaide.launcher subprocess wait →
                # supervisor — was fragile in practice: any link breaking
                # left the running process with stale empty credentials
                # in memory, no observable error, and no recovery short
                # of a manual restart. The auto-updater still uses
                # ``sys.exit(75)`` because a binary swap genuinely needs
                # a fresh process; pairing does not.
                try:
                    new_relay_cfg = RelayConfig(
                        # server_url is preserved from .env, mirroring
                        # write_credentials_to_env's "only backfill if
                        # blank" rule. If the operator customised
                        # RELAY_SERVER_URL, in-process startup must hit
                        # the same URL the next cold boot will use —
                        # otherwise the running process and a fresh
                        # process disagree on which relay to dial.
                        # claim.relay_url is informational here.
                        server_url=boot_relay_cfg.server_url,
                        agent_id=claim.agent_id,
                        agent_token=claim.agent_token,
                        heartbeat_interval=boot_relay_cfg.heartbeat_interval,
                        reconnect_base_delay=boot_relay_cfg.reconnect_base_delay,
                        reconnect_max_delay=boot_relay_cfg.reconnect_max_delay,
                    )
                    await _start_relay_in_process(app, new_relay_cfg)
                    logger.info(
                        "agent.relay_started_in_process agent_id=%s relay=%s",
                        claim.agent_id, boot_relay_cfg.server_url,
                    )
                except Exception as exc:
                    # In-process startup failed (rare). The .env is
                    # already correct, so the supervisor's next restart
                    # will see the new credentials and start the relay
                    # client normally. Fall back to ``sys.exit(75)`` so
                    # the operator does not have to wait for a manual
                    # restart. This is the legacy code path, now used
                    # only as a backup.
                    from mobaide.agent.updater import EXIT_RESTART_REQUESTED

                    logger.error(
                        "agent.in_process_relay_start_failed: %s; "
                        "requesting supervisor restart via exit code %d",
                        exc, EXIT_RESTART_REQUESTED,
                    )
                    asyncio.get_running_loop().call_later(
                        0.5, sys.exit, EXIT_RESTART_REQUESTED,
                    )

            layout = default_layout()
            pairing_task = asyncio.create_task(
                run_pairing_monitor(
                    layout=layout,
                    relay_server_url=relay_cfg.server_url,
                    current_agent_id=relay_cfg.agent_id or None,
                    on_paired=_on_paired,
                ),
                name="pairing-monitor",
            )

            def _pairing_task_done(task: "asyncio.Task[None]") -> None:
                # Surface unanticipated pairing-task failures within
                # seconds instead of waiting for shutdown's re-await
                # (and the asyncio GC "Task exception was never retrieved"
                # message, which on a long-running service may never fire).
                # ``run_pairing_monitor`` already has a catch-all
                # ``except Exception`` around its outer loop body, so this
                # callback only fires for: (1) intentional return (paired
                # successfully); (2) cancellation at shutdown; or (3) a
                # ``BaseException`` (system exit, ``KeyboardInterrupt``) we
                # deliberately do not swallow inside the loop. Case 3 uses
                # ``logger.error(..., exc_info=exc)`` to render the full
                # traceback into ``agent.err.log`` — ``logger.exception()``
                # cannot be used here because a done-callback runs outside
                # any active ``except`` block, so its implicit
                # ``exc_info=True`` would resolve to ``None`` instead of
                # the task's exception. Passing ``exc_info=exc`` explicitly
                # threads the task exception through the same formatter
                # used elsewhere in the file.
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is None:
                    logger.info("pairing_monitor.task_completed_normally")
                    return
                logger.error(
                    "pairing_monitor.task_died_unexpectedly exc_type=%s exc=%s",
                    type(exc).__name__, exc, exc_info=exc,
                )

            pairing_task.add_done_callback(_pairing_task_done)
            app.state.pairing_task = pairing_task
        else:
            app.state.relay_client = None
            app.state.updater = None
            app.state.https_poll_task = None
        yield
        logger.info("agent.shutting_down")
        models_warmup = getattr(app.state, "cli_models_warmup", None)
        if models_warmup is not None and not models_warmup.done():
            models_warmup.cancel()
            try:
                await models_warmup
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("agent.cli_models_warmup_failed: %s", exc)
        pairing_task = getattr(app.state, "pairing_task", None)
        if pairing_task is not None and not pairing_task.done():
            pairing_task.cancel()
            try:
                await pairing_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("agent.pairing_task_failed: %s", exc)
        https_poll_task = getattr(app.state, "https_poll_task", None)
        if https_poll_task is not None and not https_poll_task.done():
            https_poll_task.cancel()
            try:
                await https_poll_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("agent.https_poll_task_failed: %s", exc)
        # Relay client must stop first so it unsubscribes from fanouts/buses before they are torn down.
        relay_client = getattr(app.state, "relay_client", None)
        if relay_client is not None:
            await relay_client.stop()
        autoscan_mgr = getattr(app.state, "autoscan_manager", None)
        if autoscan_mgr is not None:
            await autoscan_mgr.shutdown()
        await job_registry.shutdown()
        for fanout in app.state.fanouts.values():
            fanout.stop()
        await app.state.session_manager.shutdown()
        shutdown_logging()

    # Default-deny on the doc routes: the agent binds every interface, so
    # FastAPI's stock /docs would publish the full session/project/git/codex
    # surface to the local network. ENABLE_API_DOCS opts a dev box back in.
    docs_enabled = config.enable_api_docs
    app = FastAPI(
        title="Mobaide Agent",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Strip secret inputs from validation errors so a malformed body cannot echo
    # the secret back to the caller; other fields keep `input`.
    _SECRET_FIELDS = frozenset({"linear_api_key", "api_key"})

    def _input_carries_secret(value: object) -> bool:
        """True if a validation error's ``input`` payload embeds a secret field.

        The ``loc`` check alone misses *body-level* errors: a malformed body
        (e.g. a JSON array posted to an object route) is rejected before field
        extraction, so Pydantic reports ``loc=("body",)`` with the whole raw body
        as ``input`` — carrying the secret under a key ``loc`` never names. Walk
        the parsed payload (plain dict/list from JSON) so those are scrubbed too.
        """
        if isinstance(value, dict):
            return any(
                key in _SECRET_FIELDS or _input_carries_secret(val)
                for key, val in value.items()
            )
        if isinstance(value, list):
            return any(_input_carries_secret(item) for item in value)
        return False

    @app.exception_handler(RequestValidationError)
    async def _scrub_validation_errors(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        scrubbed = []
        for err in exc.errors():
            loc = err.get("loc", ())
            if any(part in _SECRET_FIELDS for part in loc) or _input_carries_secret(
                err.get("input")
            ):
                err = {k: v for k, v in err.items() if k != "input"}
            ctx = err.get("ctx")
            if isinstance(ctx, dict):
                # A model_validator's `raise ValueError(...)` surfaces here as
                # the raw exception instance under ctx["error"], which
                # JSONResponse cannot serialize — stringify every ctx value.
                err = {**err, "ctx": {k: str(v) for k, v in ctx.items()}}
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
    prefix = auto_scan_cfg.branch_prefix or "feature/mobaide"
    worktree_mgr = WorktreeManager(
        worktree_base=auto_scan_cfg.worktree_base,
        branch_prefix=prefix,
        main_branch=auto_scan_cfg.main_branch,
    )
    app.state.worktree_manager = worktree_mgr
    # Per-agent secret baked into each session's hook URLs and re-checked by
    # /internal/hook so a stray local process can't spoof attention events.
    app.state.hook_token = secrets.token_urlsafe(32)
    app.state.claude_launcher = ClaudeLauncher(
        worktree_mgr=worktree_mgr,
        claude_bin_override=config.claude_bin,
        hook_port=config.port,
        hook_token=app.state.hook_token,
    )
    # Codex CLI launcher — shares the WorktreeManager so stored-project sessions
    # can create a git worktree (same as Claude's Standard mode); no attention
    # hooks (Codex has no ``--settings`` equivalent). Binary resolves lazily.
    app.state.codex_launcher = CodexLauncher(
        codex_bin_override=config.codex_bin,
        worktree_mgr=worktree_mgr,
    )
    # Antigravity CLI launcher — sibling of Codex; same WorktreeManager sharing,
    # no hooks and no auth probe (``agy`` signs in via Google/keyring with no
    # detectable marker; its login prompt renders in the PTY when needed).
    # Binary resolves lazily.
    app.state.antigravity_launcher = AntigravityLauncher(
        agy_bin_override=config.agy_bin,
        worktree_mgr=worktree_mgr,
    )
    # Registry keyed by CLI name. SessionManager picks a launcher per session;
    # the Auto Scan scanner stays Claude-only. build_cli_capabilities reads this
    # registry, so a new entry auto-surfaces in /api/version capabilities.
    app.state.cli_launchers = {
        "claude": app.state.claude_launcher,
        "codex": app.state.codex_launcher,
        "antigravity": app.state.antigravity_launcher,
    }

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
        codex_launcher=app.state.codex_launcher,
        antigravity_launcher=app.state.antigravity_launcher,
        worktree_mgr=worktree_mgr,
        event_bus=app.state.event_bus,
        on_session_created=_on_session_created,
        on_session_removed=_on_session_removed,
        # MOBAIDE_ENV=test → in-process echo backend (no real shells); the
        # default real backend is byte-for-byte unchanged in production.
        backend_factory=FakePtyBackend if config.is_test_mode else create_pty_backend,
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
    app.include_router(codex_router)
    app.include_router(hooks_router)
    app.include_router(projects_router)
    app.include_router(fs_router)
    app.include_router(git_router)
    app.include_router(session_git_router)
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
            # macOS: True/False = has/lacks Full Disk Access; None off macOS.
            # Offloaded — full_disk_access() does a blocking open(); keep it off
            # the event loop on this polled endpoint.
            "macos_full_disk_access": await asyncio.to_thread(full_disk_access),
        }
        if autoscan_mgr is not None:
            result["auto_scans_active"] = len(autoscan_mgr.list())
        return result

    @app.get("/api/version", tags=["version"])
    async def version_info() -> dict[str, object]:
        relay_client = getattr(app.state, "relay_client", None)
        manifest = relay_client.latest_manifest if relay_client else None
        # A probe that lands mid-warmup would advertise cold caches — on the
        # wire indistinguishable from an old agent without model enumeration
        # (see await_model_cache_warmup). Bounded, so a wedged OR legitimately
        # slow probe (each subprocess attempt may take up to 10s) cannot stall
        # the endpoint; if the warm task is STILL running after the wait, the
        # response says so via models_pending instead of letting clients treat
        # the cold shape as authoritative.
        warm_task = getattr(app.state, "cli_models_warmup", None)
        await await_model_cache_warmup(warm_task)
        return {
            "name": "mobaide_agent",
            "server_version": __version__,
            "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "min_client_version": MIN_CLIENT_PROTOCOL_VERSION,
            # Offload the binary probes (shutil.which + filesystem stats over the
            # npm fallback paths) so a slow/missing-CLI lookup can't block the
            # event loop — mirrors the full_disk_access offload in /api/health.
            "capabilities": {
                "cli": await asyncio.to_thread(
                    build_cli_capabilities,
                    app.state.cli_launchers,
                    models_pending=warm_task is not None and not warm_task.done(),
                ),
                "git": GIT_CAPABILITIES,
                "fs": FS_CAPABILITIES,
            },
            "latest_agent_version": (manifest or {}).get("version"),
        }

    return app

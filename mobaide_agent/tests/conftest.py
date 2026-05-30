from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.core.models import SessionConfig
from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.relay.config import RelayConfig

IS_WINDOWS = sys.platform == "win32"

skip_if_not_windows = pytest.mark.skipif(not IS_WINDOWS, reason="Windows only")
skip_if_not_linux = pytest.mark.skipif(IS_WINDOWS, reason="Linux/macOS only")


@pytest.fixture(autouse=True)
def _pin_claude_binary(monkeypatch):
    """Pin the resolver to the literal string ``"claude"`` for every test.

    Existing assertions in ``test_claude_launcher.py`` (and any future test
    that does not opt out) compare ``spec.argv[0] == "claude"``. The
    production resolver returns an absolute path; without this pin those
    tests would break on the developer's host depending on where ``claude``
    happens to be installed.

    Tests that need to exercise the real resolver (``test_claude_resolver.py``)
    capture the original function reference at module-import time, before
    this fixture has a chance to swap it.
    """
    from mobaide.core import claude_launcher

    monkeypatch.setattr(
        claude_launcher, "_resolve_claude_binary", lambda *_a, **_kw: "claude"
    )
    yield

TEST_JWT_SECRET = "test-secret-key-for-jwt-signing-do-not-use-in-prod"
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpass123"


def default_shell() -> list[str]:
    """Return the default shell command for the current platform."""
    if IS_WINDOWS:
        return ["cmd.exe"]
    return ["/bin/bash"]


def drain_text_preamble(ws) -> None:
    """Consume the ``server_info`` text frame that ``/ws/session/<id>``
    sends before any binary handshake frame.

    ``WebSocketTestSession.receive_bytes()`` does NOT skip text envelopes —
    it raises ``KeyError: 'bytes'`` if the next queued message is text. The
    binary-protocol tests in this package therefore need to drain the
    leading text frame first.

    Falls back to pushing the bytes through to the next ``receive_bytes()``
    call if the server somehow sent a binary frame first (would be a
    server-contract regression, but the helper stays robust).
    """
    first = ws.receive()
    if first.get("text") is not None:
        return
    deferred = first.get("bytes")
    if deferred is None:
        pytest.fail(f"Unexpected frame in preamble: {first!r}")
    original_receive_bytes = ws.receive_bytes
    pending = {"bytes": deferred}

    def _receive_bytes_with_pending() -> bytes:
        b = pending.pop("bytes", None)
        if b is not None:
            return b
        return original_receive_bytes()

    ws.receive_bytes = _receive_bytes_with_pending  # type: ignore[assignment]


def make_release(prefix: Path, version: str) -> Path:
    """Create a fake ``<prefix>/releases/<version>/`` with a venv interpreter.

    Shared by the launcher rollback and prune tests, which build the same
    on-disk release layout ``_resolve_python`` probes (``Scripts`` on Windows,
    ``bin`` elsewhere).
    """
    release = prefix / "releases" / version
    if IS_WINDOWS:
        bin_dir = release / "venv" / "Scripts"
        python = bin_dir / "python.exe"
    else:
        bin_dir = release / "venv" / "bin"
        python = bin_dir / "python"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nexec /bin/true\n", encoding="utf-8")
    return release


def release_names(prefix: Path) -> set[str]:
    """Return the directory names under ``<prefix>/releases/``."""
    return {p.name for p in (prefix / "releases").iterdir()}


@pytest.fixture
def session_config() -> SessionConfig:
    return SessionConfig(
        session_id="test-session",
        command=default_shell(),
        rows=24,
        cols=80,
        scrollback_size=100,
    )


@pytest.fixture
def short_lived_config() -> SessionConfig:
    """Config that spawns a process which exits immediately."""
    if IS_WINDOWS:
        cmd = ["cmd.exe", "/c", "echo hello"]
    else:
        cmd = ["/bin/bash", "-c", "echo hello"]
    return SessionConfig(
        session_id="short-lived",
        command=cmd,
        rows=24,
        cols=80,
        scrollback_size=100,
    )


# ---------- Agent / API fixtures ----------


@pytest.fixture
def agent_config(tmp_path) -> AgentConfig:
    """Create a test AgentConfig with a temp user store and projects store."""
    return AgentConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        projects_store_path=str(tmp_path / "projects.json"),
        max_sessions=4,
        log_level="DEBUG",
    )


@pytest.fixture
async def app(agent_config):
    """Create a FastAPI app instance for testing, with cleanup."""
    application = create_app(config=agent_config)
    # Seed a test user
    application.state.user_store.add_user(TEST_USERNAME, TEST_PASSWORD)
    yield application
    # Cleanup: stop all fanouts and shutdown session manager
    for fanout in list(application.state.fanouts.values()):
        fanout.stop()
    try:
        await asyncio.wait_for(
            application.state.session_manager.shutdown(),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        # Force-kill any remaining PTY processes
        import signal
        for session in list(application.state.session_manager._sessions.values()):
            try:
                pid = session.pid
                if pid:
                    os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass


@pytest.fixture
async def client(app):
    """Async HTTP client for REST endpoint testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client) -> dict[str, str]:
    """Authenticate and return Authorization headers."""
    resp = await client.post(
        "/api/auth/token",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def access_token(app) -> str:
    """Return a valid access token string for WebSocket testing."""
    return app.state.jwt_service.create_access_token(TEST_USERNAME)


# ---------- RelayClient mock collaborators ----------
#
# Shared across ``test_relay_client.py`` and
# ``test_relay_client_autoscan.py``. Each test file composes its own
# ``client`` / ``relay_client`` fixture on top of these to avoid colliding
# with this conftest's existing async HTTP ``client`` fixture.


@pytest.fixture
def relay_config() -> RelayConfig:
    return RelayConfig(
        server_url="wss://relay.test.io",
        agent_id="agent-42",
        agent_token="tok-secret",
        heartbeat_interval=15,
        reconnect_base_delay=0.01,
        reconnect_max_delay=0.05,
    )


@pytest.fixture
def mock_session_manager():
    mgr = AsyncMock()
    mgr.list_sessions.return_value = []
    mgr.get_session.return_value = None
    return mgr


@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    q = asyncio.Queue()
    bus.subscribe.return_value = q
    bus.unsubscribe = MagicMock()
    return bus


@pytest.fixture
def mock_broadcaster():
    bc = MagicMock()
    q = asyncio.Queue()
    bc.subscribe.return_value = q
    bc.unsubscribe = MagicMock()
    return bc


@pytest.fixture
def mock_app_state():
    """Default ``app_state`` carrying an ``autoscan_manager`` stub.

    Unifies the two prior variants: ``test_relay_client.py`` used a bare
    ``state.fanouts = {}`` and ``test_relay_client_autoscan.py`` also wired
    in ``state.autoscan_manager``. The richer shape is backward-compatible
    for the pre-existing tests since they never read ``autoscan_manager``.
    """
    state = MagicMock()
    state.fanouts = {}
    state.autoscan_manager = MagicMock(name="autoscan_manager")
    return state

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from claude_mobile.core.models import SessionConfig
from claude_mobile.server.app import create_app
from claude_mobile.server.config import ServerConfig

IS_WINDOWS = sys.platform == "win32"

skip_if_not_windows = pytest.mark.skipif(not IS_WINDOWS, reason="Windows only")
skip_if_not_linux = pytest.mark.skipif(IS_WINDOWS, reason="Linux/macOS only")

TEST_JWT_SECRET = "test-secret-key-for-jwt-signing-do-not-use-in-prod"
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpass123"


def default_shell() -> list[str]:
    """Return the default shell command for the current platform."""
    if IS_WINDOWS:
        return ["cmd.exe"]
    return ["/bin/bash"]


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


# ---------- Server / API fixtures ----------


@pytest.fixture
def server_config(tmp_path) -> ServerConfig:
    """Create a test ServerConfig with a temp user store."""
    return ServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        max_sessions=4,
        log_level="DEBUG",
    )


@pytest.fixture
async def app(server_config):
    """Create a FastAPI app instance for testing, with cleanup."""
    application = create_app(config=server_config)
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

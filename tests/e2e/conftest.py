"""Subprocess-orchestration fixtures for the cross-platform E2E harness.

Boots the real central server + real mobaide agent as subprocesses on ephemeral
loopback ports (via pytest-xprocess), provisions an owner user + agent through
the real REST flow, and gates readiness on ``agents_online >= 1`` before yielding
a configured ``MockMobileClient``. Teardown is guaranteed (process-tree kill +
tmp-dir removal) on pass, fail, or Ctrl-C, leaving no orphans.

Run from the repo root with both packages installed editable:
    python -m pytest tests/e2e/ -v
"""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import psutil
import pytest
from xprocess import ProcessStarter

from mock_mobile_client import MockMobileClient

# The headless client uses the `websockets` asyncio client, which hangs under the
# Windows Proactor loop; force the Selector policy before any asyncio.run() in a test.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

E2E_EMAIL = "e2e-harness@test.local"
E2E_PASSWORD = "E2eHarnessPass123!"

# Seeded via ADMIN_SEED_EMAIL/ADMIN_SEED_PASSWORD below, purely so the harness
# can authenticate the admin-gated /api/health counts for the readiness poll
# (see wait_agents_online). Never used as the mobile-owner identity.
E2E_ADMIN_EMAIL = "e2e-harness-admin@test.local"
E2E_ADMIN_PASSWORD = "E2eHarnessAdminPass123!"


@dataclass
class SpawnedAgent:
    """An extra agent subprocess a test owns outright (see ``spawn_agent``)."""

    agent_id: str
    agent_token: str
    prefix: Path
    log_path: Path

    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")


@dataclass
class Harness:
    """What the ``e2e_stack`` fixture hands to a test."""

    server_base: str
    server_port: int
    agent_id: str
    access_token: str
    admin_token: str
    client: MockMobileClient
    tmp: Path


def _free_port() -> int:
    """Reserve an ephemeral loopback port and release it for the subprocess to bind.

    Deliberately does NOT set SO_REUSEADDR (avoids the bind-then-report port-theft race).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _force_kill_tree(pid: int | None) -> None:
    """psutil fallback tree-kill for any child that escaped xprocess's terminate()."""
    if not pid or pid == os.getpid():
        return
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True)
    procs.append(parent)
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def _terminate(xprocess, name: str) -> None:
    """Terminate a managed process and its tree; never raises (finalizer-safe)."""
    try:
        info = xprocess.getinfo(name)
    except Exception:
        return
    pid = getattr(info, "pid", None)
    try:
        info.terminate(timeout=15)
    except Exception:
        pass
    _force_kill_tree(pid)


def _make_starter(*, args, env, cwd, ready):
    """Build a ProcessStarter subclass that gates readiness on an HTTP health probe.

    Attributes are defined in the class body (not post-assigned) so the abstract
    ``args`` member is satisfied and the subclass is instantiable.
    """
    _args = list(args)
    _env = dict(env)
    _cwd = str(cwd)

    class _Starter(ProcessStarter):
        args = _args
        env = _env
        popen_kwargs = {"cwd": _cwd}
        pattern = None  # readiness comes from startup_check, not log scraping
        timeout = 60
        terminate_on_interrupt = True

        def startup_check(self):
            return ready()

    return _Starter


def _server_ready(port: int):
    def check() -> bool:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    return check


def _agent_ready(port: int):
    def check() -> bool:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.5)
            return r.status_code == 200 and r.json().get("status") == "ok"
        except Exception:
            return False

    return check


@pytest.fixture(scope="session")
def e2e_stack(request, xprocess):
    """Session-scoped: real server + agent subprocesses + provisioned mobile client."""
    tmp = Path(tempfile.mkdtemp(prefix="cla78-e2e-"))
    request.addfinalizer(lambda: shutil.rmtree(tmp, ignore_errors=True))

    jwt_secret = secrets.token_urlsafe(32)
    server_port = _free_port()
    agent_port = _free_port()
    server_name = f"cla78_server_{server_port}"
    agent_name = f"cla78_agent_{agent_port}"
    server_dir = tmp / "server"
    agent_dir = tmp / "agent"
    server_dir.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)

    # Register terminations up-front so a failed ensure() cannot orphan a process.
    request.addfinalizer(lambda: _terminate(xprocess, server_name))

    server_env = {
        **os.environ,
        "MOBAIDE_ENV": "test",
        "JWT_SECRET_KEY": jwt_secret,
        "EMAIL_VERIFICATION_REQUIRED": "false",
        "ADMIN_SEED_EMAIL": E2E_ADMIN_EMAIL,
        "ADMIN_SEED_PASSWORD": E2E_ADMIN_PASSWORD,
        "HOST": "127.0.0.1",
        "PORT": str(server_port),
        "DB_PATH": str(server_dir / "central.db"),
        "MANIFEST_STORE_PATH": str(server_dir / "manifest.json"),
        "LOG_FORMAT": "console",
        "LOG_LEVEL": "INFO",
        "PYTHONUNBUFFERED": "1",
    }
    server_starter = _make_starter(
        args=[sys.executable, "-u", "-m", "central_server"],
        env=server_env,
        cwd=tmp,
        ready=_server_ready(server_port),
    )
    xprocess.ensure(server_name, server_starter)

    # Provision the owner user + agent over the real REST flow, BEFORE the agent boots
    # (the agent needs its RELAY_AGENT_ID / RELAY_AGENT_TOKEN at startup).
    client = MockMobileClient(f"http://127.0.0.1:{server_port}")
    request.addfinalizer(client.close)
    access_token = client.register_and_login(E2E_EMAIL, E2E_PASSWORD)
    agent_id, agent_token = client.create_agent("e2e-agent")

    request.addfinalizer(lambda: _terminate(xprocess, agent_name))
    agent_env = {
        **os.environ,
        "MOBAIDE_ENV": "test",
        "JWT_SECRET_KEY": jwt_secret,
        "HOST": "127.0.0.1",
        "PORT": str(agent_port),
        "RELAY_SERVER_URL": f"ws://127.0.0.1:{server_port}",
        "RELAY_AGENT_ID": agent_id,
        "RELAY_AGENT_TOKEN": agent_token,
        "MOBAIDE_AGENT_ALLOW_INSECURE_WS": "1",
        "MOBAIDE_AGENT_PREFIX": str(agent_dir),
        # Voice against the generated fake model (no 670 MB download); an
        # inherited MOBAIDE_VOICE_DIR must not point it at a real install.
        "MOBAIDE_VOICE_FAKE": "1",
        "MOBAIDE_VOICE_DIR": "",
        "LOG_FORMAT": "console",
        "LOG_LEVEL": "INFO",
        "PYTHONUNBUFFERED": "1",
    }
    agent_starter = _make_starter(
        args=[sys.executable, "-u", "-m", "mobaide"],
        env=agent_env,
        cwd=tmp,
        ready=_agent_ready(agent_port),
    )
    xprocess.ensure(agent_name, agent_starter)

    # The correct cross-process gate: wait for the relay handshake, not bare HTTP 200.
    # agents_online is admin-gated, so poll with the seeded admin's token, not the
    # mobile-owner's (which the agent-ownership endpoints reject as admin anyway).
    admin_token = client.login(E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD)
    client.wait_agents_online(1, timeout=45, token=admin_token)

    yield Harness(
        server_base=f"http://127.0.0.1:{server_port}",
        server_port=server_port,
        agent_id=agent_id,
        access_token=access_token,
        admin_token=admin_token,
        client=client,
        tmp=tmp,
    )


@pytest.fixture
def spawn_agent(request, xprocess, e2e_stack):
    """Factory for a dedicated agent subprocess on the shared relay.

    For tests that remove or break their agent: the session-scoped
    ``e2e_stack`` agent is shared by every other test and must stay online.
    """

    def spawn(name: str, *, extra_env: dict[str, str] | None = None) -> SpawnedAgent:
        agent_id, agent_token = e2e_stack.client.create_agent(name)
        agent_port = _free_port()
        process_name = f"cla78_extra_agent_{agent_port}"
        prefix = e2e_stack.tmp / process_name
        prefix.mkdir(parents=True, exist_ok=True)
        request.addfinalizer(lambda: _terminate(xprocess, process_name))
        env = {
            **os.environ,
            "MOBAIDE_ENV": "test",
            "JWT_SECRET_KEY": secrets.token_urlsafe(32),
            "HOST": "127.0.0.1",
            "PORT": str(agent_port),
            "RELAY_SERVER_URL": f"ws://127.0.0.1:{e2e_stack.server_port}",
            "RELAY_AGENT_ID": agent_id,
            "RELAY_AGENT_TOKEN": agent_token,
            "MOBAIDE_AGENT_ALLOW_INSECURE_WS": "1",
            "MOBAIDE_AGENT_PREFIX": str(prefix),
            "LOG_FORMAT": "console",
            "LOG_LEVEL": "INFO",
            "PYTHONUNBUFFERED": "1",
            **(extra_env or {}),
        }
        starter = _make_starter(
            args=[sys.executable, "-u", "-m", "mobaide"],
            env=env,
            cwd=e2e_stack.tmp,
            ready=_agent_ready(agent_port),
        )
        _pid, log_path = xprocess.ensure(process_name, starter)
        return SpawnedAgent(
            agent_id=agent_id,
            agent_token=agent_token,
            prefix=prefix,
            log_path=Path(log_path),
        )

    return spawn

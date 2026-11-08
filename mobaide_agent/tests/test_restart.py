"""Cover ``mobaide.agent.restart`` and its ``_run_agent`` exit-code contract.

The Windows supervisor respawns the agent only on exit code 75, and Task
Scheduler never restarts a task that exited by itself. A bare ``sys.exit(75)``
from the updater does not produce that code everywhere: during lifespan
startup uvicorn turns it into its own exit code 3, and from a FastAPI
background task uvicorn swallows it and keeps serving. The real-uvicorn tests
below pin that both paths now end the process with 75.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI

import mobaide.__main__ as _main
import mobaide.agent.app as _app_mod
from mobaide.agent import restart
from mobaide.agent.updater import EXIT_RESTART_REQUESTED


class _FakeTimer:
    instances: list["_FakeTimer"] = []

    def __init__(self, interval, function, args=()) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.started = False
        _FakeTimer.instances.append(self)

    def start(self) -> None:
        self.started = True


@pytest.fixture(autouse=True)
def _isolated_restart_state(monkeypatch):
    monkeypatch.setattr(restart, "_server", None)
    monkeypatch.setattr(restart, "_requested_code", None)
    _FakeTimer.instances = []
    monkeypatch.setattr(restart.threading, "Timer", _FakeTimer)


def test_without_a_server_it_falls_back_to_sys_exit() -> None:
    with pytest.raises(SystemExit) as excinfo:
        restart.request_restart()
    assert excinfo.value.code == EXIT_RESTART_REQUESTED


def test_with_a_server_it_stops_the_server_and_arms_one_watchdog() -> None:
    server = SimpleNamespace(should_exit=False)
    restart.bind_server(server)

    restart.request_restart()
    restart.request_restart()

    assert server.should_exit is True
    assert restart.requested_exit_code() == EXIT_RESTART_REQUESTED
    assert restart.restart_requested() is True
    assert len(_FakeTimer.instances) == 1
    watchdog = _FakeTimer.instances[0]
    assert watchdog.started and watchdog.daemon
    assert watchdog.function is restart._force_exit
    assert watchdog.args == (EXIT_RESTART_REQUESTED,)


def test_watchdog_hard_exits_with_the_requested_code(monkeypatch) -> None:
    codes: list[int] = []
    monkeypatch.setattr(restart.os, "_exit", codes.append)
    restart._force_exit(EXIT_RESTART_REQUESTED)
    assert codes == [EXIT_RESTART_REQUESTED]


def test_build_updater_wires_request_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_app_mod, "default_prefix", lambda: tmp_path)
    monkeypatch.setattr(_app_mod, "read_trust_public_key", lambda layout: "key")
    monkeypatch.setattr(_app_mod, "_is_running_managed_release", lambda layout: True)
    app = SimpleNamespace(state=SimpleNamespace(session_manager=None, voice=None))

    updater = _app_mod._build_updater(app)

    assert updater is not None
    assert updater._exit_func is restart.request_restart


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stub_app(port: int, lifespan) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.config = SimpleNamespace(host="127.0.0.1", port=port)
    return app


@pytest.fixture(params=["win32", sys.platform])
def platform_branch(request, monkeypatch):
    if request.param == "win32" and sys.platform != "win32":
        # Drive the Windows Runner branch on a POSIX host; the branch uses
        # only portable asyncio APIs (Runner + SelectorEventLoop).
        monkeypatch.setattr(_main.sys, "platform", "win32")
    return request.param


def test_restart_during_lifespan_startup_exits_with_75(
    monkeypatch, platform_branch,
) -> None:
    """The Windows bug: the pre-connect update check stages a release while
    the lifespan is still starting. The process must exit 75, not 3 or 0."""
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(app):
        restart.request_restart()
        events.append("startup")
        yield
        events.append("shutdown")

    app = _stub_app(_free_port(), lifespan)
    monkeypatch.setattr(_app_mod, "create_app", lambda: app)

    with pytest.raises(SystemExit) as excinfo:
        _main._run_agent()

    assert excinfo.value.code == EXIT_RESTART_REQUESTED
    assert events == ["startup", "shutdown"]
    assert restart._server is None


def test_restart_from_background_task_exits_with_75(
    monkeypatch, platform_branch,
) -> None:
    """The "Install now" path: ``request_restart`` runs as a FastAPI
    background task in a worker thread, after the response is sent."""
    port = _free_port()
    responses: list[int] = []

    async def _post_when_up() -> None:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    resp = await client.post(f"http://127.0.0.1:{port}/restart")
                except httpx.TransportError:
                    await asyncio.sleep(0.05)
                    continue
                responses.append(resp.status_code)
                return

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(_post_when_up())
        yield
        await task

    app = _stub_app(port, lifespan)

    @app.post("/restart")
    async def _restart(background_tasks: BackgroundTasks) -> dict[str, str]:
        background_tasks.add_task(restart.request_restart)
        return {"outcome": "staged"}

    monkeypatch.setattr(_app_mod, "create_app", lambda: app)

    with pytest.raises(SystemExit) as excinfo:
        _main._run_agent()

    assert excinfo.value.code == EXIT_RESTART_REQUESTED
    assert responses == [200]


def test_run_agent_returns_normally_without_a_restart(
    monkeypatch, platform_branch,
) -> None:
    @asynccontextmanager
    async def lifespan(app):
        asyncio.get_running_loop().call_later(0.2, _stop)
        yield

    def _stop() -> None:
        restart._server.should_exit = True

    app = _stub_app(_free_port(), lifespan)
    monkeypatch.setattr(_app_mod, "create_app", lambda: app)

    _main._run_agent()

    assert restart.requested_exit_code() is None

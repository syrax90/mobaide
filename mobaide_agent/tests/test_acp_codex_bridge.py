"""The Codex app-server bridge (``mobaide/acp/codex_bridge.py``): where it
looks for the shared app-server's control socket, when a socket counts as
answering, the fallback to a private ``codex app-server``, and the relay of
newline-delimited JSON between stdio and the socket's WebSocket (run as the
adapter runs it: ``python -I codex_bridge.py <codex>``)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest
from websockets.asyncio.server import unix_serve

from mobaide.acp import codex_bridge
from tests.conftest import skip_if_not_linux

pytestmark = pytest.mark.timeout(60)

BRIDGE = codex_bridge.__file__


@pytest.fixture
def codex_home():
    """A short folder: a Unix socket path must stay under ~100 bytes, and
    the pytest ``tmp_path`` on macOS is longer than that."""
    folder = Path(tempfile.mkdtemp(prefix="mb-codex-", dir="/tmp" if os.name != "nt" else None))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _socket_path(codex_home: Path) -> str:
    path = codex_home / "app-server-control" / "app-server-control.sock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


class TestControlSocket:
    def test_codex_home_of_the_env(self, tmp_path):
        path = codex_bridge.control_socket({"CODEX_HOME": str(tmp_path)})

        assert path == os.path.join(tmp_path, "app-server-control", "app-server-control.sock")

    def test_default_is_the_codex_folder_in_home(self):
        path = codex_bridge.control_socket({})

        assert path == os.path.join(
            os.path.expanduser("~"), ".codex", "app-server-control", "app-server-control.sock"
        )


@skip_if_not_linux
class TestSocketReachable:
    def test_missing_path(self, codex_home):
        assert codex_bridge.socket_reachable(str(codex_home / "nothing.sock")) is False

    def test_a_regular_file(self, codex_home):
        path = Path(_socket_path(codex_home))
        path.write_text("", encoding="utf-8")

        assert codex_bridge.socket_reachable(str(path)) is False

    def test_a_socket_file_nobody_listens_on(self, codex_home):
        """What a crashed app-server leaves behind."""
        path = _socket_path(codex_home)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(path)
        stale.close()

        assert os.path.exists(path)
        assert codex_bridge.socket_reachable(path) is False

    def test_a_listening_socket(self, codex_home):
        path = _socket_path(codex_home)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        try:
            assert codex_bridge.socket_reachable(path) is True
        finally:
            server.close()


class TestMain:
    @pytest.fixture
    def execs(self, monkeypatch) -> list[tuple[str, list[str]]]:
        calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(codex_bridge.os, "execv", lambda path, argv: calls.append((path, argv)))
        return calls

    def test_usage(self, execs, capsys):
        assert codex_bridge.main(["codex_bridge.py"]) == 2
        assert execs == []
        assert "usage" in capsys.readouterr().err

    def test_no_shared_app_server_runs_a_private_one(self, execs, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))

        codex_bridge.main(["codex_bridge.py", "/opt/codex/bin/codex"])

        assert execs == [("/opt/codex/bin/codex", ["/opt/codex/bin/codex", "app-server"])]

    def test_a_socket_that_does_not_take_the_connection_runs_a_private_one(
        self, execs, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        monkeypatch.setattr(codex_bridge, "socket_reachable", lambda _path, timeout=1.0: True)

        async def no_connection(_path: str) -> None:
            return None

        monkeypatch.setattr(codex_bridge, "_relay", no_connection)

        codex_bridge.main(["codex_bridge.py", "/opt/codex/bin/codex"])

        assert execs == [("/opt/codex/bin/codex", ["/opt/codex/bin/codex", "app-server"])]
        assert "starting a private one" in capsys.readouterr().err

    def test_a_python_without_websockets_runs_a_private_one(
        self, execs, tmp_path, monkeypatch, capsys
    ):
        """The bridge runs under the host's ``python -I``, which may lack the package."""
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        monkeypatch.setattr(codex_bridge, "socket_reachable", lambda _path, timeout=1.0: True)
        monkeypatch.setitem(sys.modules, "websockets.asyncio.client", None)

        codex_bridge.main(["codex_bridge.py", "/opt/codex/bin/codex"])

        assert execs == [("/opt/codex/bin/codex", ["/opt/codex/bin/codex", "app-server"])]
        assert "starting a private one" in capsys.readouterr().err


async def _bridge(codex_home: Path) -> asyncio.subprocess.Process:
    # A codex that does not exist: a fallback to a private app-server fails.
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        BRIDGE,
        str(codex_home / "no-codex"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CODEX_HOME": str(codex_home)},
    )


@skip_if_not_linux
class TestRelay:
    async def test_lines_go_to_the_socket_and_answers_come_back_as_lines(self, codex_home):
        received: list[str] = []

        async def echo(ws) -> None:
            async for message in ws:
                received.append(message)
                await ws.send(json.dumps({"echo": json.loads(message)}))

        async with unix_serve(echo, _socket_path(codex_home)):
            proc = await _bridge(codex_home)
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(b'{"id": 1, "method": "initialize"}\n\n{"id": 2, "method": "x"}\n')
            await proc.stdin.drain()

            first = await asyncio.wait_for(proc.stdout.readline(), 20)
            second = await asyncio.wait_for(proc.stdout.readline(), 20)
            proc.stdin.close()
            code = await asyncio.wait_for(proc.wait(), 20)

        assert json.loads(first) == {"echo": {"id": 1, "method": "initialize"}}
        assert json.loads(second) == {"echo": {"id": 2, "method": "x"}}
        # The blank line is not sent.
        assert received == ['{"id": 1, "method": "initialize"}', '{"id": 2, "method": "x"}']
        assert code == 0

    async def test_the_bridge_ends_when_the_shared_app_server_goes_away(self, codex_home):
        async def close_after_one(ws) -> None:
            await ws.recv()
            await ws.close()

        async with unix_serve(close_after_one, _socket_path(codex_home)):
            proc = await _bridge(codex_home)
            assert proc.stdin is not None
            proc.stdin.write(b'{"id": 1}\n')
            await proc.stdin.drain()

            await asyncio.wait_for(proc.wait(), 20)

        assert proc.returncode is not None

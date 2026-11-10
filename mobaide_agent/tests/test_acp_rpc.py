"""``AcpConnection.close``: the stdout reader reaps the process and reports
its exit; ``close`` returns once that exit is handled, and kills a process
that outlives the grace period. Every caller waits for the same close, and
commands the agent left running in its process group die with it."""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import psutil
import pytest

from mobaide.acp.rpc import AcpConnection
from tests.conftest import skip_if_not_linux

pytestmark = pytest.mark.timeout(30)

EXITS_ON_EOF = "import sys\nfor _ in sys.stdin:\n    pass\n"
OUTLIVES_EOF = "import sys, time\nfor _ in sys.stdin:\n    pass\ntime.sleep(60)\n"
# Starts a child that holds none of the agent's pipes, reports its pid as a
# notification, then exits cleanly on EOF: the child is left in the group.
LEAVES_A_BACKGROUND_CHILD = """
import json, subprocess, sys
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "child", "params": {"pid": child.pid}}) + "\\n")
sys.stdout.flush()
for _ in sys.stdin:
    pass
"""


async def _no_requests(method: str, params: Any) -> Any:
    raise AssertionError(f"unexpected request {method}")


def _connection(
    script: str,
    exits: list[int | None],
    on_notification: Any = lambda _method, _params: None,
) -> AcpConnection:
    return AcpConnection(
        [sys.executable, "-c", script],
        cwd=None,
        env=None,
        on_notification=on_notification,
        on_request=_no_requests,
        on_exit=exits.append,
        name="acp-test",
    )


class TestClose:
    async def test_returns_once_the_process_exits_on_closed_stdin(self):
        exits: list[int | None] = []
        conn = _connection(EXITS_ON_EOF, exits)
        await conn.start()

        await conn.close(grace=10.0)

        assert exits == [0]
        assert conn.returncode == 0
        assert conn.alive is False

    @skip_if_not_linux
    async def test_kills_a_process_that_outlives_the_grace(self):
        exits: list[int | None] = []
        conn = _connection(OUTLIVES_EOF, exits)
        await conn.start()
        pid = conn.pid

        await conn.close(grace=0.2)

        assert exits == [-9]
        assert conn.alive is False
        assert not psutil.pid_exists(pid)

    @skip_if_not_linux
    async def test_a_second_caller_returns_only_once_the_process_is_gone(self):
        exits: list[int | None] = []
        conn = _connection(OUTLIVES_EOF, exits)
        await conn.start()
        pid = conn.pid

        first = asyncio.create_task(conn.close(grace=0.2))
        await asyncio.sleep(0)
        await conn.close(grace=60.0)

        assert exits == [-9]
        assert not psutil.pid_exists(pid)
        await first

    @skip_if_not_linux
    async def test_cancelling_one_caller_does_not_stop_the_close(self):
        exits: list[int | None] = []
        conn = _connection(OUTLIVES_EOF, exits)
        await conn.start()
        pid = conn.pid
        first = asyncio.create_task(conn.close(grace=0.2))
        await asyncio.sleep(0)

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        # A close of its own would wait out this grace before the kill.
        await asyncio.wait_for(conn.close(grace=60.0), timeout=10.0)

        assert exits == [-9]
        assert conn.alive is False
        assert not psutil.pid_exists(pid)

    @skip_if_not_linux
    async def test_kills_a_background_child_left_in_the_group_after_a_clean_exit(self):
        exits: list[int | None] = []
        reported: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        def on_notification(method: str, params: Any) -> None:
            if method == "child" and not reported.done():
                reported.set_result(params["pid"])

        conn = _connection(LEAVES_A_BACKGROUND_CHILD, exits, on_notification)
        await conn.start()
        child_pid = await asyncio.wait_for(reported, timeout=10.0)
        try:
            assert psutil.pid_exists(child_pid)

            await conn.close(grace=10.0)

            assert exits == [0]
            assert _gone(child_pid)
        finally:
            _kill_if_alive(child_pid)


def _gone(pid: int) -> bool:
    try:
        psutil.Process(pid).wait(timeout=5)
    except psutil.NoSuchProcess:
        return True
    except psutil.TimeoutExpired:
        return False
    return not psutil.pid_exists(pid)


def _kill_if_alive(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass

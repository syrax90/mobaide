"""``AcpConnection.close``: the stdout reader reaps the process and reports
its exit; ``close`` returns once that exit is handled, and kills a process
that outlives the grace period. Every caller waits for the same close, and
commands the agent left running in its process group die with it.

``$/cancel_request``: the agent cancels one of its own open requests; the
handler is cancelled and the agent gets ``-32800``."""
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
    on_request: Any = _no_requests,
) -> AcpConnection:
    return AcpConnection(
        [sys.executable, "-c", script],
        cwd=None,
        env=None,
        on_notification=on_notification,
        on_request=on_request,
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


# Every message without a method (a reply to the agent's request) is sent
# back as a ``reply`` notification. The client's ``hold`` handler sends a
# ``held`` notification, so the agent cancels only a request in progress.
CANCELS_ITS_REQUESTS = r"""
import json, sys

def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()

def cancel(params):
    send({"method": "$/cancel_request", "params": params})

def next_message():
    msg = json.loads(sys.stdin.readline())
    if "method" not in msg:
        send({"method": "reply", "params": msg})
    return msg

send({"id": 2, "method": "quick", "params": {}})
next_message()
cancel({"requestId": 2})
send({"id": 1, "method": "hold", "params": {}})
send({"id": "s", "method": "hold", "params": {}})
held = 0
while held < 2:
    held += next_message().get("method") == "held"
for params in ({"requestId": True}, {"requestId": "1"}, {"requestId": 99},
               {"requestId": [1]}, {}, [1]):
    cancel(params)
cancel({"requestId": "s"})
next_message()
cancel({"requestId": 1})
next_message()
for _ in sys.stdin:
    pass
"""

ECHOES_REPLIES = r"""
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "method" not in msg:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "reply", "params": msg}) + "\n")
        sys.stdout.flush()
"""

CANCELLED = {"code": -32800, "message": "Request cancelled"}


class TestCancelRequest:
    async def test_cancels_only_the_named_open_request(self):
        exits: list[int | None] = []
        notifications: list[str] = []
        replies: list[dict[str, Any]] = []
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        conn: AcpConnection

        def on_notification(method: str, params: Any) -> None:
            notifications.append(method)
            if method == "reply":
                replies.append(params)
                if len(replies) == 3 and not done.done():
                    done.set_result(None)

        async def on_request(method: str, params: Any) -> Any:
            if method == "quick":
                return {"ok": True}
            conn.notify("held", {})
            await asyncio.get_running_loop().create_future()

        conn = _connection(CANCELS_ITS_REQUESTS, exits, on_notification, on_request)
        await conn.start()
        try:
            await asyncio.wait_for(done, timeout=10.0)
        finally:
            await conn.close(grace=10.0)

        # ``True``, ``"1"`` and the malformed cancels left request 1 open:
        # "s" is the first to be cancelled.
        assert replies == [
            {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}},
            {"jsonrpc": "2.0", "id": "s", "error": CANCELLED},
            {"jsonrpc": "2.0", "id": 1, "error": CANCELLED},
        ]
        assert "$/cancel_request" not in notifications

    async def test_a_cancel_right_behind_its_request_still_gets_a_reply(self):
        # Both lines reach the loop before the handler task has started.
        exits: list[int | None] = []
        replies: list[dict[str, Any]] = []
        replied: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def on_notification(method: str, params: Any) -> None:
            if method == "reply":
                replies.append(params)
                if not replied.done():
                    replied.set_result(None)

        async def on_request(method: str, params: Any) -> Any:
            await asyncio.get_running_loop().create_future()

        conn = _connection(ECHOES_REPLIES, exits, on_notification, on_request)
        await conn.start()
        try:
            conn._dispatch({"jsonrpc": "2.0", "id": 5, "method": "hold", "params": {}})
            conn._dispatch(
                {"jsonrpc": "2.0", "method": "$/cancel_request", "params": {"requestId": 5}}
            )
            await asyncio.wait_for(replied, timeout=10.0)
        finally:
            await conn.close(grace=10.0)

        assert replies == [{"jsonrpc": "2.0", "id": 5, "error": CANCELLED}]


# Answers the first request, then sends a notification in the same write:
# both lines reach the client together.
NOTIFIES_RIGHT_AFTER_THE_REPLY = r"""
import json, sys
msg = json.loads(sys.stdin.readline())
sys.stdout.write(
    json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}) + "\n"
    + json.dumps({"jsonrpc": "2.0", "method": "after", "params": {}}) + "\n"
)
sys.stdout.flush()
for _ in sys.stdin:
    pass
"""


class TestReplyHook:
    async def test_runs_before_a_notification_sent_right_after_the_reply(self):
        exits: list[int | None] = []
        events: list[str] = []
        notified: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def on_notification(method: str, params: Any) -> None:
            events.append(method)
            if not notified.done():
                notified.set_result(None)

        conn = _connection(NOTIFIES_RIGHT_AFTER_THE_REPLY, exits, on_notification)
        await conn.start()
        try:
            result = await conn.request(
                "session/load", {}, timeout=10.0, on_reply=lambda: events.append("reply")
            )
            await asyncio.wait_for(notified, timeout=10.0)
        finally:
            await conn.close(grace=10.0)

        assert result == {}
        assert events == ["reply", "after"]


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

"""Joins the Codex ACP adapter to the Codex app-server that already runs on the PC.

The adapter starts ``$CODEX_PATH app-server`` and speaks newline-delimited
JSON-RPC to it. ``codex`` in a terminal keeps its sessions in a shared
app-server instead (the daemon behind ``codex agents``). While that one runs,
Mobaide points ``CODEX_PATH`` at a wrapper that starts this bridge: it relays
each line to the shared app-server's control socket (JSON-RPC over a
WebSocket), so a chat session and a terminal on the PC work on the same live
session. When the socket does not answer, the bridge runs a private
app-server, exactly as the adapter would have.

The shared app-server reads the Codex login once, when it starts: a
``codex login`` run next to it writes ``auth.json``, but the app-server (and
every chat that joins it) stays signed out. So while it runs, a sign-in goes
through it instead (``account/login/start``), which also saves the login for
every other Codex on the PC.

It runs as ``python -I codex_bridge.py <codex>``, or as
``python -I codex_bridge.py <codex> login --device-auth`` for a ChatGPT
sign-in: only the standard library and ``websockets``, no ``mobaide`` imports.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import Callable, Mapping
from typing import Any

CONTROL_SOCKET = ("app-server-control", "app-server-control.sock")
MAX_LINE_BYTES = 64 * 1024 * 1024
CONNECT_TIMEOUT = 5.0
DEVICE_LOGIN_ARGS = ("login", "--device-auth")
API_KEY_LOGIN_TIMEOUT = 60.0


def control_socket(env: Mapping[str, str]) -> str:
    home = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(home, *CONTROL_SOCKET)


def socket_reachable(path: str, timeout: float = 1.0) -> bool:
    """Whether something accepts connections on the Unix socket ``path``."""
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(path):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


async def _connect(path: str) -> Any | None:
    """A WebSocket to the shared app-server, or None when it does not answer."""
    try:
        from websockets.asyncio.client import unix_connect

        return await asyncio.wait_for(
            unix_connect(
                path,
                uri="ws://localhost/",
                max_size=None,
                compression=None,
                ping_interval=None,
            ),
            CONNECT_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 - any failure to connect means: no shared app-server
        return None


async def _relay(path: str) -> int | None:
    """Relay stdio to the socket; None when it could not connect at all."""
    ws = await _connect(path)
    if ws is None:
        return None
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_LINE_BYTES)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    out = sys.stdout.buffer

    async def upstream() -> None:
        while True:
            line = await reader.readline()
            if not line:
                return
            line = line.strip()
            if line:
                await ws.send(line.decode("utf-8", "replace"))

    async def downstream() -> None:
        async for message in ws:
            data = message.encode("utf-8") if isinstance(message, str) else message
            out.write(data + b"\n")
            out.flush()

    tasks = {asyncio.create_task(upstream()), asyncio.create_task(downstream())}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # stdin closing is the adapter's normal shutdown; the socket closing
        # first means the shared app-server went away.
        failed = any(t.exception() is not None for t in done if not t.cancelled())
        return 1 if failed else 0
    finally:
        await ws.close()


async def _login(
    ws: Any, params: dict[str, Any], on_started: Callable[[dict[str, Any]], None]
) -> tuple[bool, str]:
    """Run ``account/login/start`` on the app-server and wait for its
    ``account/login/completed``: ``(signed in, error text)``."""
    await ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "mobaide", "version": "1"}},
    }))
    await ws.send(json.dumps({"jsonrpc": "2.0", "method": "initialized"}))
    await ws.send(json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "account/login/start", "params": params,
    }))
    started = False
    login_id: str | None = None
    completions: list[dict[str, Any]] = []
    async for message in ws:
        try:
            frame = json.loads(message)
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        if frame.get("method") == "account/login/completed":
            completions.append(frame.get("params") or {})
        elif "method" not in frame and frame.get("id") in (1, 2) and "error" in frame:
            error = frame["error"] if isinstance(frame["error"], dict) else {}
            return False, str(error.get("message") or "The Codex app-server refused the sign-in.")
        elif "method" not in frame and frame.get("id") == 2:
            result = frame.get("result") if isinstance(frame.get("result"), dict) else {}
            login_id = result.get("loginId")
            started = True
            on_started(result)
        if started:
            # An API key login has no login id and may complete before its answer.
            for done in completions:
                if done.get("loginId") == login_id:
                    return bool(done.get("success")), str(done.get("error") or "")
    return False, "The Codex app-server closed the connection."


async def _device_login(path: str) -> int | None:
    """A ChatGPT device-code sign-in through the shared app-server, printed
    as ``codex login --device-auth`` prints it; None when it does not answer."""
    ws = await _connect(path)
    if ws is None:
        return None

    def show(result: dict[str, Any]) -> None:
        sys.stdout.write(
            "Open this page and sign in to ChatGPT:\n"
            f"{result.get('verificationUrl', '')}\n\n"
            f"Enter this one-time code: {result.get('userCode', '')}\n"
        )
        sys.stdout.flush()

    try:
        ok, error = await _login(ws, {"type": "chatgptDeviceCode"}, show)
    except Exception as exc:  # noqa: BLE001 - a lost connection fails the sign-in
        ok, error = False, f"The Codex app-server closed the connection ({exc})."
    finally:
        await ws.close()
    if ok:
        sys.stdout.write("Signed in to ChatGPT.\n")
        return 0
    sys.stderr.write(f"{error or 'Sign-in did not complete.'}\n")
    return 1


async def login_with_api_key(
    path: str, api_key: str, timeout: float = API_KEY_LOGIN_TIMEOUT
) -> tuple[bool, str] | None:
    """Save an API key login through the shared app-server at ``path``:
    ``(signed in, error text)``, or None when it does not answer."""
    ws = await _connect(path)
    if ws is None:
        return None
    try:
        return await asyncio.wait_for(
            _login(ws, {"type": "apiKey", "apiKey": api_key}, lambda _result: None), timeout
        )
    except asyncio.TimeoutError:
        return False, "The Codex app-server did not finish the sign-in."
    except Exception as exc:  # noqa: BLE001 - a lost connection fails the sign-in
        return False, f"The Codex app-server closed the connection ({exc})."
    finally:
        await ws.close()


def main(argv: list[str]) -> int:
    if len(argv) == 2 + len(DEVICE_LOGIN_ARGS) and tuple(argv[2:]) == DEVICE_LOGIN_ARGS:
        codex = argv[1]
        path = control_socket(os.environ)
        if socket_reachable(path):
            code = asyncio.run(_device_login(path))
            if code is not None:
                return code
        os.execv(codex, [codex, *DEVICE_LOGIN_ARGS])
    if len(argv) != 2:
        sys.stderr.write("usage: codex_bridge.py <codex> [login --device-auth]\n")
        return 2
    codex = argv[1]
    path = control_socket(os.environ)
    if socket_reachable(path):
        code = asyncio.run(_relay(path))
        if code is not None:
            return code
        sys.stderr.write("mobaide: the shared Codex app-server did not answer; starting a private one\n")
    os.execv(codex, [codex, "app-server"])


if __name__ == "__main__":
    sys.exit(main(sys.argv))

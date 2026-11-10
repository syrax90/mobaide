"""Joins the Codex ACP adapter to the Codex app-server that already runs on the PC.

The adapter starts ``$CODEX_PATH app-server`` and speaks newline-delimited
JSON-RPC to it. ``codex`` in a terminal keeps its sessions in a shared
app-server instead (the daemon behind ``codex agents``). While that one runs,
Mobaide points ``CODEX_PATH`` at a wrapper that starts this bridge: it relays
each line to the shared app-server's control socket (JSON-RPC over a
WebSocket), so a chat session and a terminal on the PC work on the same live
session. When the socket does not answer, the bridge runs a private
app-server, exactly as the adapter would have.

It runs as ``python -I codex_bridge.py <codex>``: only the standard library
and ``websockets``, no ``mobaide`` imports.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from collections.abc import Mapping

CONTROL_SOCKET = ("app-server-control", "app-server-control.sock")
MAX_LINE_BYTES = 64 * 1024 * 1024
CONNECT_TIMEOUT = 5.0


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


async def _relay(path: str) -> int | None:
    """Relay stdio to the socket; None when it could not connect at all."""
    try:
        from websockets.asyncio.client import unix_connect

        ws = await asyncio.wait_for(
            unix_connect(
                path,
                uri="ws://localhost/",
                max_size=None,
                compression=None,
                ping_interval=None,
            ),
            CONNECT_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 - any failure to connect means: run a private one
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: codex_bridge.py <codex>\n")
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

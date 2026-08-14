"""Pre-accept Origin gate for every WebSocket endpoint.

Standard anti-CSWSH hygiene for the browser client: every WebSocket
authenticates on a ``?token=`` bearer, so a cross-site page cannot ride an
existing session — this gate is defense in depth, not the primary control.

That stays true only as long as no WS endpoint reads ``websocket.cookies``.
A session cookie now exists on the SPA origin (``auth/session_cookie.py``) and
a handshake carries it like any other same-site request; the moment one of
these endpoints authenticated off it, this gate would become the *primary*
CSWSH control — and it deliberately allows any loopback origin on any port,
which is nowhere near strong enough for that job. Keep WS auth on the bearer.

The rules:

* Absent ``Origin`` is always allowed — Android (OkHttp), iOS (URLSession)
  and the agent (``websockets.connect`` without ``origin=``) send none, and
  the header only carries meaning when a browser sets it.
* Loopback origins (``localhost`` / ``127.0.0.1`` / ``[::1]``, any port,
  http or https) are structurally allowed so Vite dev/preview servers work
  with zero env configuration.
* Everything else must match ``config.ws_allowed_origins`` (pre-normalized
  at parse time; see ``config._env_origin_list``).

Rejection closes the socket *before* ``accept()``, which uvicorn turns into
an HTTP 403 handshake denial — browsers never see a WS close code, but
Starlette's TestClient surfaces it as ``WebSocketDisconnect(4403)``.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket

from .config import normalize_origin

logger = logging.getLogger(__name__)

# Distinct from the relay's 4001/4003/4010/4011/4012 taxonomy
# (relay/routing.py) so tests can assert the rejection cause precisely.
WS_CLOSE_ORIGIN_FORBIDDEN = 4403

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})


def _is_loopback(normalized: str) -> bool:
    host = normalized.split("://", 1)[1]
    head, colon, port = host.rpartition(":")
    # isascii() keeps port-stripping semantics identical to
    # config.normalize_origin (see the guard comment there).
    if colon and port.isascii() and port.isdigit():
        host = head
    return host in _LOOPBACK_HOSTS


async def enforce_ws_origin(websocket: WebSocket) -> bool:
    """Return True to proceed with the handshake; False after rejecting.

    Must be called before ``websocket.accept()``. Reads the allow-list from
    ``websocket.app.state.config``, which is set in ``create_app`` outside
    the lifespan, so the gate works under every test fixture.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    normalized = normalize_origin(origin)
    if normalized is not None and (
        normalized in websocket.app.state.config.ws_allowed_origins
        or _is_loopback(normalized)
    ):
        return True
    path = websocket.url.path
    # The pairing route embeds the single-use claimable nonce in the path;
    # log only its 8-char prefix, matching the pairing router's own
    # nonce[:8] logging convention, so the credential never lands in
    # operational logs while still claimable.
    if path.startswith("/ws/pairing/"):
        path = "/ws/pairing/" + path[len("/ws/pairing/"):][:8] + "…"
    logger.warning("ws_origin.rejected origin=%s path=%s", origin, path)
    await websocket.close(
        code=WS_CLOSE_ORIGIN_FORBIDDEN, reason="origin_not_allowed",
    )
    return False

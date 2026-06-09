"""Local-only endpoint that turns Claude Code hooks into attention state.

The agent spawns each interactive ``claude`` with a per-session ``--settings``
file (see :mod:`mobaide.core.hook_settings`) whose ``http`` hooks POST here. The
``session_id`` and a per-agent ``token`` are query params baked into the hook URL
at spawn, so every hook routes to exactly the right :class:`TerminalSession`
even under concurrent sessions sharing a cwd.

Security: the agent binds ``0.0.0.0``, so this endpoint is guarded by a loopback
source check **and** the per-agent token. It carries no auth dependency on
purpose — the only intended caller is the local ``claude`` child process, which
cannot present a user JWT. Handlers stay non-blocking (look-up + in-memory
enqueue, then 200) so a ``Stop`` hook — which blocks Claude on a non-zero/late
response — never stalls the CLI.
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Request, Response, status

from mobaide.core.hook_settings import remove_session_hooks

from ..logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# request.client.host values that count as same-host. The agent binds IPv4
# 0.0.0.0, so the local child connects as 127.0.0.1; the IPv6 forms are
# defensive for hosts that resolve loopback differently.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

# Claude Code ``hook_event_name`` -> attention reason. ``Notification`` is
# already scoped to permission prompts by the hook matcher.
_EVENT_TO_ATTENTION = {
    "Notification": "awaiting_permission",
    "Stop": "turn_finished",
    "SessionEnd": "ended",
}


@router.post("/hook")
async def receive_hook(
    request: Request, session_id: str = "", token: str = "",
) -> Response:
    """Apply a Claude Code hook to the addressed session's attention state."""
    client = request.client
    if client is None or client.host not in _LOOPBACK_HOSTS:
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    expected = getattr(request.app.state, "hook_token", None)
    # Short-circuit on an unset token so the digest call never sees None, then
    # constant-time compare (repo convention for secret comparison). Compare as
    # bytes so a non-ASCII token query param fails closed instead of raising.
    if not expected or not secrets.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}
    attention = _EVENT_TO_ATTENTION.get(body.get("hook_event_name", ""))
    if attention is None or not session_id:
        return Response(status_code=status.HTTP_200_OK)

    session = await request.app.state.session_manager.get_session(session_id)
    if session is None:
        # Late hook fired after liveness cleanup removed the session — normal.
        return Response(status_code=status.HTTP_200_OK)

    if attention == "ended":
        reason = str(body.get("reason") or "other")
        session.set_attention("ended", exit_reason=reason)
        remove_session_hooks(session_id, request.app.state.config.port)
        logger.info("hook.session_ended", session_id=session_id, reason=reason)
    else:
        session.set_attention(attention)
        logger.info("hook.attention", session_id=session_id, attention=attention)
    return Response(status_code=status.HTTP_200_OK)

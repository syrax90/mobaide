"""Local-only endpoint that turns Claude Code hooks into attention state.

The agent spawns each interactive ``claude`` with a per-session ``--settings``
file (see :mod:`mobaide.core.hook_settings`) whose ``http`` hooks POST here. The
``session_id``, a per-agent ``token`` and the gate-facing ``hook`` kind are query
params baked into the hook URL at spawn, so every hook routes to exactly the
right :class:`TerminalSession` even under concurrent sessions sharing a cwd, and
classifies without the handler having to parse the body.

Routing is all this layer does — whether a hook becomes a user-visible alert is
:mod:`mobaide.core.attention_gate`'s decision.

Security: the agent binds ``0.0.0.0``, so this endpoint is guarded by a loopback
source check **and** the per-agent token. It carries no auth dependency on
purpose — the only intended caller is the local ``claude`` child process, which
cannot present a user JWT. Handlers stay non-blocking (look-up + in-memory
enqueue, then 200) so a ``Stop`` or ``PreToolUse`` hook — which blocks Claude
until it is answered — never stalls the CLI.
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Request, Response, status

from mobaide.core.attention_gate import (
    HookKind,
    resolve_hook_kind,
    summarize_hook_payload,
)
from mobaide.core.hook_settings import remove_session_hooks

from ..logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# request.client.host values that count as same-host. The agent binds IPv4
# 0.0.0.0, so the local child connects as 127.0.0.1; the IPv6 forms are
# defensive for hosts that resolve loopback differently.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


@router.post("/hook")
async def receive_hook(
    request: Request, session_id: str = "", token: str = "", hook: str = "",
) -> Response:
    """Route a Claude Code hook to the addressed session's attention gate."""
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
        parsed = await request.json()
    except Exception:
        parsed = None
    # A hook body is always a JSON object, but the gate reads it field by field,
    # so anything else is normalized away rather than allowed to raise here.
    body: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
    event_name = body.get("hook_event_name", "")
    kind = resolve_hook_kind(hook, event_name if isinstance(event_name, str) else "")
    if kind is None or not session_id:
        return Response(status_code=status.HTTP_200_OK)

    session = await request.app.state.session_manager.get_session(session_id)
    if session is None:
        # Late hook fired after liveness cleanup removed the session — normal.
        return Response(status_code=status.HTTP_200_OK)

    session.on_claude_hook(kind, body)
    summary = summarize_hook_payload(body)
    if kind is HookKind.END:
        remove_session_hooks(session_id, request.app.state.config.port)
        logger.info(
            "hook.session_ended",
            session_id=session_id,
            reason=str(body.get("reason") or "other"),
            **summary,
        )
    elif kind is HookKind.TOOL:
        # One per tool call: too frequent for the info log.
        logger.debug(
            "hook.received", session_id=session_id, hook=kind.value, **summary,
        )
    else:
        logger.info(
            "hook.received", session_id=session_id, hook=kind.value, **summary,
        )
    return Response(status_code=status.HTTP_200_OK)

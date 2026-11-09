"""Chat-session pieces shared by the direct WebSocket and the relay tunnel.

Both transports carry chat sessions on the per-session socket, next to the
PTY frame types: ``'c'`` frames (client → agent) hold one JSON command and
``'a'`` frames (agent → client) hold one JSON timeline frame.
"""
from __future__ import annotations

import re
from typing import Any

from .catalog import ChatCatalog, ChatCatalogError

FRAME_CHAT = b"a"
FRAME_CHAT_COMMAND = b"c"

# Shown by a client that predates chat sessions and opens one in its
# terminal screen. Sent inside the PTY replay markers it already handles.
LEGACY_CLIENT_NOTICE = (
    b"\r\n\x1b[1mThis session uses the chat view.\x1b[0m\r\n"
    b"Update the Mobaide app to open it.\r\n"
)

_CHAT_SESSIONS_ROUTE = re.compile(r"^/api/chat/([a-z0-9_]{1,32})/sessions$")
CHAT_RESUME_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"
CHAT_RESUME_ID_MAX = 200
_CHAT_RESUME_ID = re.compile(CHAT_RESUME_ID_PATTERN)


def is_valid_chat_resume_id(value: str) -> bool:
    return len(value) <= CHAT_RESUME_ID_MAX and _CHAT_RESUME_ID.fullmatch(value) is not None


def is_chat_session(session: Any) -> bool:
    return getattr(session, "is_chat", False) is True


def chat_request_error(
    ui: str | None, kind: Any, chat_resume_id: str | None, manager: Any
) -> str | None:
    """Why a create-session request cannot start in the chat UI, or ``None``."""
    if ui != "chat":
        if chat_resume_id:
            return "chat_resume_id needs ui=chat"
        return None
    kind_key = getattr(kind, "value", kind)
    if kind_key not in manager.chat_agents:
        return f"The chat view is not available for {kind_key} sessions"
    return None


def match_chat_sessions_route(path: str) -> str | None:
    """The agent kind of a ``GET /api/chat/{kind}/sessions`` path."""
    m = _CHAT_SESSIONS_ROUTE.match(path)
    return m.group(1) if m else None


async def list_chat_sessions(
    catalog: ChatCatalog | None,
    manager: Any,
    kind: str,
    *,
    cwd: str | None,
    cursor: str | None,
) -> tuple[int, dict[str, Any]]:
    """``GET /api/chat/{kind}/sessions`` for both transports.

    Each row names the open Mobaide chat session that already shows it
    (``open_session_id``), so a client opens that one instead of a copy.
    """
    if catalog is None:
        return 404, {"detail": "Chat sessions are not available on this agent"}
    try:
        page = await catalog.list_sessions(kind, cwd=cwd or None, cursor=cursor or None)
    except ChatCatalogError as exc:
        return exc.status, {"detail": exc.detail}
    for row in page["sessions"]:
        open_session = manager.find_chat_session(kind, row["session_id"])
        if open_session is not None:
            row["open_session_id"] = open_session.session_id
            # The lock another app sees is this session's own.
            row["open_elsewhere"] = False
    return 200, page

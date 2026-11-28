"""Chat-session pieces shared by the direct WebSocket and the relay tunnel.

Both transports carry chat sessions on the per-session socket, next to the
PTY frame types: ``'c'`` frames (client → agent) hold one JSON command and
``'a'`` frames (agent → client) hold one JSON timeline frame.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .catalog import OPEN_IN_MOBAIDE_DETAIL, ChatCatalog, ChatCatalogError
# Re-exported for the create routes; defined below this module so that
# ``session`` (which this module imports) can check a forked session's id.
from .normalize import CHAT_RESUME_ID_MAX as CHAT_RESUME_ID_MAX
from .normalize import CHAT_RESUME_ID_PATTERN as CHAT_RESUME_ID_PATTERN
from .normalize import is_valid_chat_resume_id as is_valid_chat_resume_id
from .session import ChatCommandError, RequestGoneError

logger = logging.getLogger(__name__)

FRAME_CHAT = b"a"
FRAME_CHAT_COMMAND = b"c"

# Shown by a client that predates chat sessions and opens one in its
# terminal screen. Sent inside the PTY replay markers it already handles.
LEGACY_CLIENT_NOTICE = (
    b"\r\n\x1b[1mThis session uses the chat view.\x1b[0m\r\n"
    b"Update the Mobaide app to open it.\r\n"
)

_CHAT_SESSIONS_ROUTE = re.compile(r"^/api/chat/([a-z0-9_]{1,32})/sessions$")
# Any id reaches the shared validation, which answers 400 for a bad one.
_CHAT_SESSION_ROUTE = re.compile(r"^/api/chat/([a-z0-9_]{1,32})/sessions/(.+)$", re.DOTALL)
_CHAT_RESPOND_ROUTE = re.compile(r"^/api/sessions/([^/]+)/chat/respond$")


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


def match_chat_session_route(path: str) -> tuple[str, str] | None:
    """The agent kind and stored session id of a
    ``DELETE /api/chat/{kind}/sessions/{session_id}`` path."""
    m = _CHAT_SESSION_ROUTE.match(path)
    return (m.group(1), m.group(2)) if m else None


def match_chat_respond_route(path: str) -> str | None:
    """The session id of a ``POST /api/sessions/{session_id}/chat/respond`` path."""
    m = _CHAT_RESPOND_ROUTE.match(path)
    return m.group(1) if m else None


async def answer_chat_request(
    manager: Any, session_id: str, request_id: str, choice: str
) -> tuple[int, dict[str, Any]]:
    """Answer a pending permission request, for both transports.

    404: no such chat session; 409: the request is not pending; 422: a
    notification cannot answer it.
    """
    session = await manager.get_session(session_id)
    if session is None or not is_chat_session(session):
        return 404, {"detail": "Chat session not found"}
    try:
        session.answer_request(request_id, choice)
    except RequestGoneError as exc:
        return 409, {"detail": str(exc)}
    except ChatCommandError as exc:
        return 422, {"detail": str(exc)}
    logger.info("chat.request_answered session=%s choice=%s", session_id, choice)
    return 200, {"answered": True}


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


async def delete_chat_session(
    catalog: ChatCatalog | None, manager: Any, kind: str, session_id: str
) -> tuple[int, dict[str, Any] | None]:
    """``DELETE /api/chat/{kind}/sessions/{session_id}`` for both transports.

    A conversation that a Mobaide chat session shows is never deleted: the
    agent leaves that case undefined, and the open chat would lose its
    history.
    """
    if catalog is None:
        return 404, {"detail": "Chat sessions are not available on this agent"}
    if not session_id or not is_valid_chat_resume_id(session_id):
        return 400, {"detail": "Invalid session id"}

    def open_in_mobaide() -> bool:
        return manager.find_chat_session(kind, session_id) is not None

    if open_in_mobaide():
        return 409, {"detail": OPEN_IN_MOBAIDE_DETAIL}
    try:
        await catalog.delete_session(kind, session_id, open_in_mobaide=open_in_mobaide)
    except ChatCatalogError as exc:
        return exc.status, {"detail": exc.detail}
    return 204, None

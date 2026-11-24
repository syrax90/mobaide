from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..auth.dependencies import WebSocketAuthError, ws_authenticate
from ..events import EventBus
from .session_list import _serialize_sessions

logger = logging.getLogger(__name__)

router = APIRouter()


def _serialize_workspaces(app) -> list[dict]:
    store = getattr(app.state, "workspaces_store", None)
    if store is None:
        return []
    return store.list_public()


def _add_optional(
    snapshots: list[tuple[str, Any]], event_type: str, build: Callable[[], Any], failure: str
) -> None:
    """An optional topic whose snapshot fails to build is logged and skipped."""
    try:
        snapshots.append((event_type, build()))
    except Exception:
        logger.exception(failure)


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    """Unified agent-push event channel.

    On connect we send one snapshot envelope per topic so the client can
    seed every reactive repository, then forward published events forever.
    """
    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError:
        return

    bus: EventBus | None = getattr(websocket.app.state, "event_bus", None)
    if bus is None:
        await websocket.close(code=1011, reason="Event bus not configured")
        return

    manager = websocket.app.state.session_manager
    autoscan_mgr = getattr(websocket.app.state, "autoscan_manager", None)
    voice = getattr(websocket.app.state, "voice", None)
    agent_settings = getattr(websocket.app.state, "agent_settings", None)

    # Subscribed, then every snapshot built with no await in between, as on
    # the relay path: each queued event is newer than every snapshot, so a
    # change made while the snapshots go out follows them and is not lost.
    q = bus.subscribe()
    try:
        snapshots: list[tuple[str, Any]] = [
            ("sessions.changed", {"sessions": _serialize_sessions(manager)}),
            ("projects.changed", {"projects": _serialize_workspaces(websocket.app)}),
        ]
        if autoscan_mgr is not None:
            _add_optional(
                snapshots, "autoscan.list.changed", autoscan_mgr.list_response,
                "events_ws.autoscan_list_snapshot_failed",
            )
        if voice is not None:
            _add_optional(
                snapshots, "voice.changed", lambda: voice.status().model_dump(mode="json"),
                "events_ws.voice_snapshot_failed",
            )
        if agent_settings is not None:
            _add_optional(
                snapshots, "agent_settings.changed",
                lambda: agent_settings.status().model_dump(mode="json"),
                "events_ws.agent_settings_snapshot_failed",
            )
        for event_type, payload in snapshots:
            await websocket.send_json({"type": event_type, "payload": payload})
        while True:
            ev = await q.get()
            await websocket.send_json({"type": ev.type, "payload": ev.payload})
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("events_ws.stream_failed")
    finally:
        bus.unsubscribe(q)
        logger.info("events_ws.disconnected user=%s", username)

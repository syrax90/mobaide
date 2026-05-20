"""Tests for RelayClient internals — unit-level, no real WebSocket server.

Covers: _state_frame helper, _authenticate, _handle_control dispatch,
_handle_session_input routing, _cancel_all_subtasks, exponential backoff
in _connect_loop, and _send_heartbeat.
"""
from __future__ import annotations

import asyncio
import json
import struct
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mobaide.relay.client import (
    FRAME_EXECUTE,
    FRAME_INPUT,
    FRAME_OUTPUT,
    FRAME_REPLAY,
    FRAME_RESIZE,
    FRAME_STATE,
    RelayClient,
    _EVENT_TO_STATUS,
    _STATE_MAP,
    _state_frame,
)
from mobaide.relay.envelope import (
    CH_CONTROL,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    decode,
    encode,
)
from mobaide.core import ClaudeBinaryNotFoundError
from mobaide.core.models import SessionKind, SessionState


# ``relay_config``, ``mock_session_manager``, ``mock_event_bus``,
# ``mock_broadcaster``, and ``mock_app_state`` are defined in
# ``tests/conftest.py``. The ``client`` fixture stays local so it doesn't
# clash with the conftest's async HTTP ``client`` fixture.


@pytest.fixture
def client(
    relay_config,
    mock_session_manager,
    mock_event_bus,
    mock_broadcaster,
    mock_app_state,
):
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


# -- _state_frame helper -------------------------------------------------------


class TestStateFrame:
    def test_state_frame_running_with_pid(self):
        frame = _state_frame("running", pid=1234)
        assert frame[:1] == FRAME_STATE
        payload = json.loads(frame[1:])
        assert payload["status"] == "running"
        assert payload["pid"] == 1234

    def test_state_frame_stopped_no_pid(self):
        frame = _state_frame("stopped")
        assert frame[:1] == FRAME_STATE
        payload = json.loads(frame[1:])
        assert payload["status"] == "stopped"
        assert "pid" not in payload

    def test_state_frame_all_statuses(self):
        for status in ("starting", "running", "stopping", "stopped", "crashed"):
            frame = _state_frame(status)
            payload = json.loads(frame[1:])
            assert payload["status"] == status


# -- Mapping constants ----------------------------------------------------------


class TestMappingConstants:
    def test_event_to_status_map(self):
        assert _EVENT_TO_STATUS["started"] == "running"
        assert _EVENT_TO_STATUS["stopped"] == "stopped"
        assert _EVENT_TO_STATUS["error"] == "crashed"

    def test_state_map_covers_all_session_states(self):
        for state in SessionState:
            assert state in _STATE_MAP, f"SessionState.{state.name} missing from _STATE_MAP"


# -- _authenticate --------------------------------------------------------------


class TestAuthenticate:
    async def test_authenticate_success(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(
            return_value=json.dumps({"type": "auth_ok"})
        )
        await client._authenticate(ws)

        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "auth"
        assert sent["agent_id"] == "agent-42"
        assert sent["token"] == "tok-secret"

    async def test_authenticate_failure_raises(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(
            return_value=json.dumps({"type": "auth_error", "reason": "bad token"})
        )
        with pytest.raises(ConnectionError, match="auth failed"):
            await client._authenticate(ws)

    async def test_authenticate_binary_response(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(
            return_value=json.dumps({"type": "auth_ok"}).encode("utf-8")
        )
        # Should not raise — binary auth_ok is decoded
        await client._authenticate(ws)


# -- _handle_control ------------------------------------------------------------


class TestHandleControl:
    async def test_ping_sends_heartbeat(self, client):
        ws = AsyncMock()
        payload = json.dumps({"type": "ping"}).encode()
        await client._handle_control(payload, ws)

        ws.send.assert_called_once()
        sent_bytes = ws.send.call_args[0][0]
        ch, sid, pl = decode(sent_bytes)
        assert ch == CH_CONTROL
        assert sid == ""
        msg = json.loads(pl)
        assert msg["type"] == "heartbeat"

    async def test_create_session_success(self, client, mock_session_manager):
        mock_session = MagicMock()
        mock_session.session_id = "new-sess"
        mock_session.pid = 9999
        mock_session_manager.create_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "new-sess",
            "command": ["/bin/zsh"],
            "rows": 30,
            "cols": 120,
        }
        payload = json.dumps(ctrl).encode()

        with patch.object(client, "_bridge_session"):
            await client._handle_control(payload, ws)

        # Verify response was sent
        ws.send.assert_called_once()
        resp_bytes = ws.send.call_args[0][0]
        ch, _, resp_pl = decode(resp_bytes)
        assert ch == CH_CONTROL
        resp = json.loads(resp_pl)
        assert resp["type"] == "session_created"
        assert resp["session_id"] == "new-sess"
        assert resp["pid"] == 9999

    async def test_create_session_failure(self, client, mock_session_manager):
        mock_session_manager.create_session = AsyncMock(
            side_effect=RuntimeError("out of resources")
        )
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "fail-sess"}
        payload = json.dumps(ctrl).encode()

        await client._handle_control(payload, ws)

        ws.send.assert_called_once()
        resp_bytes = ws.send.call_args[0][0]
        ch, _, resp_pl = decode(resp_bytes)
        resp = json.loads(resp_pl)
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "fail-sess"
        assert "out of resources" in resp["error"]

    async def test_create_session_forwards_claude_options(
        self, client, mock_session_manager,
    ):
        """The ``create_session`` control message must parse ``claude_options``
        and hand them to ``SessionConfig`` — otherwise the worktree toggle
        (and every other option) is silently dropped when a central relay
        uses this path instead of the proxy_request path."""
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.models import SessionConfig

        async def _capture(config: SessionConfig):
            _capture.config = config  # type: ignore[attr-defined]
            s = MagicMock()
            s.session_id = "sid"
            s.pid = 1
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "sid",
            "claude_options": {
                "worktree": True,
                "branch_name": "feature/relay-ctrl",
                "plan_mode": True,
            },
        }
        payload = json.dumps(ctrl).encode()

        with patch.object(client, "_bridge_session"):
            await client._handle_control(payload, ws)

        cfg: SessionConfig = _capture.config  # type: ignore[attr-defined]
        assert isinstance(cfg.claude_options, ClaudeOptions)
        assert cfg.claude_options.worktree is True
        assert cfg.claude_options.branch_name == "feature/relay-ctrl"
        assert cfg.claude_options.plan_mode is True

    async def test_create_session_returns_failure_on_worktree_error(
        self, client, mock_session_manager,
    ):
        """When the launcher raises ``WorktreeCreationError``, the relay must
        respond with ``session_create_failed`` carrying the git stderr so the
        mobile client sees the real cause instead of a silent project-root
        fallback."""
        from mobaide.linear.worktree import WorktreeCreationError

        mock_session_manager.create_session = AsyncMock(
            side_effect=WorktreeCreationError(
                branch="feature/broken", path="/bad", stderr="fatal: whoops",
            )
        )

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "wt-fail",
            "claude_options": {"worktree": True, "branch_name": "feature/broken"},
        }
        payload = json.dumps(ctrl).encode()

        await client._handle_control(payload, ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert "feature/broken" in resp["error"]
        assert "whoops" in resp["error"]

    async def test_stop_session_success(self, client, mock_session_manager):
        mock_session_manager.stop_session = AsyncMock()
        ws = AsyncMock()
        ctrl = {"type": "stop_session", "session_id": "s1", "force": True}
        payload = json.dumps(ctrl).encode()

        await client._handle_control(payload, ws)

        mock_session_manager.stop_session.assert_awaited_once_with(
            "s1", force=True
        )
        resp_bytes = ws.send.call_args[0][0]
        resp = json.loads(decode(resp_bytes)[2])
        assert resp["type"] == "session_stopped"

    async def test_stop_session_not_found(self, client, mock_session_manager):
        mock_session_manager.stop_session = AsyncMock(side_effect=KeyError("s2"))
        ws = AsyncMock()
        ctrl = {"type": "stop_session", "session_id": "s2"}
        payload = json.dumps(ctrl).encode()

        await client._handle_control(payload, ws)

        resp_bytes = ws.send.call_args[0][0]
        resp = json.loads(decode(resp_bytes)[2])
        assert resp["type"] == "session_stop_failed"
        assert "not found" in resp["error"].lower()

    async def test_malformed_json_is_silently_ignored(self, client):
        ws = AsyncMock()
        await client._handle_control(b"not json {{", ws)
        ws.send.assert_not_called()


# -- _handle_mobile_attach / _handle_mobile_detach -----------------------------


class TestHandleMobileAttach:
    """The ``mobile_session_attach`` handler is what unsticks a mobile's
    keyboard on exit-and-reopen in relay mode. It must (1) re-send the full
    PTY handshake every time it fires — without any per-session idempotency
    cache — and (2) no-op safely when the session is unknown.
    """

    async def test_attach_sends_full_handshake_in_order(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 42
        mock_session.get_ring = MagicMock(return_value=b"hello world")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "s1"}
        ).encode()
        await client._handle_control(payload, ws)

        assert ws.send.await_count == 3
        sent = [ws.send.call_args_list[i][0][0] for i in range(3)]
        decoded = [decode(b) for b in sent]

        for ch, sid, _ in decoded:
            assert ch == CH_SESSION_IO
            assert sid == "s1"

        assert decoded[0][2][:1] == FRAME_STATE
        state_payload = json.loads(decoded[0][2][1:])
        assert state_payload["status"] == "running"
        assert state_payload["pid"] == 42

        assert decoded[1][2] == FRAME_REPLAY

        assert decoded[2][2][:1] == FRAME_OUTPUT
        assert decoded[2][2][1:] == b"hello world"

    async def test_attach_for_unknown_session_sends_stopped_state_frame(
        self, client, mock_session_manager
    ):
        """When the session is unknown (agent restart, liveness-cleanup, stale
        mobile preference), the attach handler must send a synthetic
        ``stopped`` state frame so the mobile's UI leaves the ``Connecting``
        banner instead of hanging.

        Wire shape: exactly one ``ws.send`` carrying a CH_SESSION_IO envelope
        whose payload is ``FRAME_STATE + JSON{"status":"stopped"}`` (no
        ``pid`` field — the session has no process).
        """
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "ghost"}
        ).encode()

        await client._handle_control(payload, ws)

        ws.send.assert_awaited_once()
        sent = ws.send.call_args[0][0]
        channel, sid, pl = decode(sent)
        assert channel == CH_SESSION_IO
        assert sid == "ghost"
        assert pl[:1] == FRAME_STATE
        state = json.loads(pl[1:])
        assert state == {"status": "stopped"}

    async def test_attach_for_unknown_session_does_not_crash_when_send_fails(
        self, client, mock_session_manager
    ):
        """``ws.send`` failure (WS half-closed race) must be swallowed so the
        relay reader loop keeps dispatching control frames for other
        sessions.  The handler logs at debug and returns cleanly.
        """
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()
        ws.send = AsyncMock(side_effect=ConnectionError("WS closed"))
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "ghost"}
        ).encode()

        await client._handle_control(payload, ws)

        ws.send.assert_awaited_once()

    async def test_attach_without_session_id_is_silent_noop(
        self, client, mock_session_manager
    ):
        ws = AsyncMock()
        payload = json.dumps({"type": "mobile_session_attach"}).encode()

        await client._handle_control(payload, ws)

        ws.send.assert_not_called()
        mock_session_manager.get_session.assert_not_awaited()

    async def test_attach_is_repeatable_no_idempotency_cache(
        self, client, mock_session_manager
    ):
        """Regression guard for the bug this fix addresses: the old
        ``_handshake_sent`` cache silently skipped the handshake on a
        mobile reattach, leaving the keyboard grayed out. The new path
        must send a fresh handshake for every attach.
        """
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 42
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "s1"}
        ).encode()

        await client._handle_control(payload, ws)
        await client._handle_control(payload, ws)
        await client._handle_control(payload, ws)

        assert ws.send.await_count == 9

    async def test_detach_is_silent_noop(self, client, mock_session_manager):
        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_detach", "session_id": "s1"}
        ).encode()

        await client._handle_control(payload, ws)

        ws.send.assert_not_called()
        mock_session_manager.get_session.assert_not_awaited()


# -- _handle_request_events_snapshot ------------------------------------------


class TestHandleRequestEventsSnapshot:
    """The ``request_events_snapshot`` control message is what unblocks the
    "sessions closed after app restart" bug in relay mode.  The central
    server sends it every time a mobile opens ``/relay/ws/events/{aid}`` or
    ``/relay/ws/sessions/{aid}``; the handler re-publishes the current
    ``sessions.changed`` + ``projects.changed`` snapshots on ``event_bus``
    (picked up by ``_forward_events`` → CH_EVENTS → mobile) and also sends
    a direct ``CH_SESSIONS_LIST`` envelope for legacy sessions-list
    channel subscribers.
    """

    async def test_publishes_sessions_changed_on_event_bus(
        self, client, mock_session_manager, mock_event_bus
    ):
        """The primary path: a ``sessions.changed`` envelope is published on
        ``event_bus`` so ``_forward_events`` can forward it through the
        tunnel to the newly-connected mobile.  Without this, the mobile's
        reactive store stays empty until the next real state change.
        """
        sess_info = MagicMock()
        sess_info.session_id = "s-42"
        sess_info.state = SessionState.RUNNING
        sess_info.created_at = datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)
        sess_info.pid = 9000
        sess_info.command = ["/bin/bash"]
        sess_info.cwd = "/tmp"
        sess_info.title = None
        sess_info.project_name = None
        sess_info.kind = SessionKind.CLAUDE
        mock_session_manager.list_sessions = MagicMock(return_value=[sess_info])

        ws = AsyncMock()
        payload = json.dumps({"type": "request_events_snapshot"}).encode()

        await client._handle_control(payload, ws)

        sessions_calls = [
            c for c in mock_event_bus.publish.call_args_list
            if c[0][0] == "sessions.changed"
        ]
        assert len(sessions_calls) == 1
        body = sessions_calls[0][0][1]
        assert body == {
            "sessions": [
                {
                    "session_id": "s-42",
                    "state": "running",
                    "created_at": "2026-04-11T10:00:00+00:00",
                    "pid": 9000,
                    "command": ["/bin/bash"],
                    "cwd": "/tmp",
                    "title": None,
                    "project_name": None,
                    "kind": "claude",
                },
            ],
        }

    async def test_publishes_projects_changed_on_event_bus(
        self, client, mock_session_manager, mock_event_bus, mock_app_state
    ):
        """When ``app_state.projects_store`` is present, the handler must also
        republish the projects snapshot so a mobile whose only recent event
        is on the projects stream still rebuilds its local store on
        reconnect.
        """
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        projects_store = MagicMock()
        projects_store.list_public = MagicMock(
            return_value=[{"name": "proj-a", "path": "/p/a"}]
        )
        mock_app_state.projects_store = projects_store

        ws = AsyncMock()
        payload = json.dumps({"type": "request_events_snapshot"}).encode()

        await client._handle_control(payload, ws)

        projects_calls = [
            c for c in mock_event_bus.publish.call_args_list
            if c[0][0] == "projects.changed"
        ]
        assert len(projects_calls) == 1
        assert projects_calls[0][0][1] == {
            "projects": [{"name": "proj-a", "path": "/p/a"}],
        }

    async def test_skips_projects_publish_when_store_missing(
        self, client, mock_session_manager, mock_event_bus, mock_app_state
    ):
        """``projects_store`` is optional on ``app_state``. When absent the
        handler must still publish ``sessions.changed`` and NOT raise or
        emit a malformed ``projects.changed`` — otherwise central-server
        deployments without a projects feature break the events channel.
        """
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        # Explicitly remove the optional attribute so ``getattr(..., None)``
        # branches into the skip path.
        if hasattr(mock_app_state, "projects_store"):
            del mock_app_state.projects_store

        ws = AsyncMock()
        payload = json.dumps({"type": "request_events_snapshot"}).encode()

        await client._handle_control(payload, ws)

        published_types = [c[0][0] for c in mock_event_bus.publish.call_args_list]
        assert "sessions.changed" in published_types
        assert "projects.changed" not in published_types

    async def test_sends_direct_sessions_list_envelope_on_tunnel(
        self, client, mock_session_manager
    ):
        """Legacy sessions-list subscribers bypass ``event_bus``. The handler
        must send a direct ``CH_SESSIONS_LIST`` envelope on the tunnel
        carrying the same payload, encoded as orjson bytes.  Without it,
        mobiles still on the legacy ``/relay/ws/sessions/{aid}`` channel
        never get their initial list either.
        """
        sess_info = MagicMock()
        sess_info.session_id = "s-1"
        sess_info.state = SessionState.STARTING
        sess_info.created_at = datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)
        sess_info.pid = None
        sess_info.command = ["/bin/sh"]
        sess_info.cwd = None
        # Without explicit assignment MagicMock generates non-serializable
        # objects that cause orjson.dumps to raise, silently dropping the send.
        sess_info.title = None
        sess_info.project_name = None
        sess_info.kind = SessionKind.CLAUDE
        mock_session_manager.list_sessions = MagicMock(return_value=[sess_info])

        ws = AsyncMock()
        payload = json.dumps({"type": "request_events_snapshot"}).encode()

        await client._handle_control(payload, ws)

        sessions_list_sends = []
        for call in ws.send.call_args_list:
            sent = call[0][0]
            try:
                channel, sid, pl = decode(sent)
            except ValueError:
                continue
            if channel == CH_SESSIONS_LIST and sid == "":
                sessions_list_sends.append(json.loads(pl))
        assert len(sessions_list_sends) == 1
        assert sessions_list_sends[0] == [
            {
                "session_id": "s-1",
                "state": "starting",
                "created_at": "2026-04-11T10:00:00+00:00",
                "pid": None,
                "command": ["/bin/sh"],
                "cwd": None,
                "title": None,
                "project_name": None,
                "kind": "claude",
            },
        ]

    async def test_event_bus_publish_failure_does_not_abort_snapshot(
        self, client, mock_session_manager, mock_event_bus
    ):
        """If ``event_bus.publish`` raises (bus shut down, queue full), the
        handler must still complete the other arms of the snapshot so the
        failure mode degrades gracefully — losing a snapshot on one
        channel but delivering it on every other.
        """
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        mock_event_bus.publish = MagicMock(
            side_effect=[RuntimeError("bus dead"), None]
        )

        ws = AsyncMock()
        payload = json.dumps({"type": "request_events_snapshot"}).encode()

        # Must not raise.
        await client._handle_control(payload, ws)

        # Direct CH_SESSIONS_LIST envelope still went out despite the
        # event_bus failure above.
        sessions_list_sends = [
            c for c in ws.send.call_args_list
            if decode(c[0][0])[0] == CH_SESSIONS_LIST
        ]
        assert len(sessions_list_sends) == 1

    async def test_empty_session_list_still_publishes_and_sends(
        self, client, mock_session_manager, mock_event_bus
    ):
        """Happy path with no sessions: still publish ``sessions.changed``
        with an empty list AND send the direct envelope, so a mobile whose
        token survived across a full session-list clear still gets the
        correct empty-state update (otherwise it would show stale cached
        sessions).
        """
        mock_session_manager.list_sessions = MagicMock(return_value=[])

        ws = AsyncMock()
        payload = json.dumps({"type": "request_events_snapshot"}).encode()

        await client._handle_control(payload, ws)

        sessions_publishes = [
            c for c in mock_event_bus.publish.call_args_list
            if c[0][0] == "sessions.changed"
        ]
        assert len(sessions_publishes) == 1
        assert sessions_publishes[0][0][1] == {"sessions": []}

        sessions_list_sends = [
            c for c in ws.send.call_args_list
            if decode(c[0][0])[0] == CH_SESSIONS_LIST
        ]
        assert len(sessions_list_sends) == 1
        assert json.loads(decode(sessions_list_sends[0][0][0])[2]) == []


# -- _handle_create_session: OSError/FileNotFoundError branches ----------------


class TestHandleCreateSessionPathErrors:
    """Regression guards for WARN-01: the relay's ``_handle_create_session``
    must not render ``FileNotFoundError``/``OSError`` via ``str(exc)``.

    ``str(OSError)`` embeds ``repr(filename)`` — on Windows that doubles
    backslashes once, JSON-encoding doubles them a second time, and the
    mobile client parses one JSON layer leaving the user with ``D:\\\\...``.
    The specialized branches build the error from ``exc.filename`` +
    ``exc.strerror`` directly, so the on-the-wire payload contains exactly
    one JSON-escape per literal backslash (2 chars per separator) — never 4.

    The existing ``TestHandleControl.test_create_session_failure`` covers
    only the generic ``Exception`` fallthrough (``RuntimeError``); none of
    the OSError-family branches were exercised before.
    """

    async def test_file_not_found_uses_filename_not_str_exc(
        self, client, mock_session_manager
    ):
        """``FileNotFoundError`` with a Windows-backslash ``filename``: the
        response ``error`` must equal ``"Project path not found: <raw>"``
        with a single layer of escaping on the wire, proving the handler
        used ``f"{exc.filename}"`` — not ``str(exc)`` which would embed
        ``repr(filename)`` and produce the 4-backslash symptom.
        """
        mock_session_manager.create_session = AsyncMock(
            side_effect=FileNotFoundError(
                2, "No such file or directory", r"D:\raw\backslash\dir",
            )
        )
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "bad-path-sess"}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        ws.send.assert_called_once()
        sent_bytes = ws.send.call_args[0][0]
        _, _, payload = decode(sent_bytes)
        resp = json.loads(payload)

        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "bad-path-sess"
        # After JSON parsing, the raw backslash string is recovered verbatim.
        assert resp["error"] == r"Project path not found: D:\raw\backslash\dir"

        # On-the-wire JSON source: one literal backslash must encode as
        # ``\\`` (2 characters). ``repr(filename)`` + JSON would produce
        # ``\\\\`` (4 characters) — that sequence must NOT appear.
        wire_json = payload.decode("utf-8")
        assert r"D:\\raw\\backslash\\dir" in wire_json
        assert r"D:\\\\raw\\\\backslash\\\\dir" not in wire_json

    async def test_file_not_found_without_filename_uses_fallback(
        self, client, mock_session_manager
    ):
        """When ``exc.filename`` is ``None`` the handler must not blow up —
        it falls back to a readable string instead of emitting ``None``."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=FileNotFoundError("missing cwd"),  # no filename arg
        )
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "no-fn-sess"}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        # Must not render the literal string "None" into the user-facing
        # error — the fallback text must appear instead.
        assert "None" not in resp["error"]
        assert "project path" in resp["error"]

    async def test_non_fnf_oserror_uses_strerror_and_filename(
        self, client, mock_session_manager
    ):
        """``PermissionError`` (OSError subclass that is NOT
        ``FileNotFoundError``) hits the broader ``except OSError`` branch
        and produces ``"<strerror>: <filename>"`` — again via direct
        f-string interpolation, not ``str(exc)``.
        """
        mock_session_manager.create_session = AsyncMock(
            side_effect=PermissionError(13, "Permission denied", r"D:\locked\dir"),
        )
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "denied-sess"}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        sent_bytes = ws.send.call_args[0][0]
        _, _, payload = decode(sent_bytes)
        resp = json.loads(payload)

        assert resp["type"] == "session_create_failed"
        assert resp["error"] == r"Permission denied: D:\locked\dir"

        # Same wire-form guard as the FileNotFoundError case.
        wire_json = payload.decode("utf-8")
        assert r"Permission denied: D:\\locked\\dir" in wire_json
        assert r"D:\\\\locked\\\\dir" not in wire_json

    async def test_oserror_without_filename_uses_strerror_only(
        self, client, mock_session_manager
    ):
        """OSError with no ``filename``: detail should be ``strerror``
        alone — no dangling ``": "`` separator, no empty filename segment.
        """
        exc = OSError(5, "I/O error")  # filename is None
        mock_session_manager.create_session = AsyncMock(side_effect=exc)
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "io-sess"}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["error"] == "I/O error"  # no trailing ": "

    async def test_claude_cli_missing_emits_claude_cli_missing_discriminator(
        self, client, mock_session_manager
    ):
        """``ClaudeBinaryNotFoundError`` from the spawn path must be mapped
        to ``session_create_failed`` with ``claude_cli_missing: true`` so
        the mobile client can render an "install Claude CLI" affordance
        instead of the generic "project path not found" toast."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=ClaudeBinaryNotFoundError(
                searched_paths=["PATH:claude", "/home/test/.npm-global/bin/claude"]
            ),
        )
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "no-cli-sess"}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "no-cli-sess"
        assert resp["claude_cli_missing"] is True
        assert resp["error"].startswith("Claude CLI not found:")
        assert "npm install -g @anthropic-ai/claude-code" in resp["error"]
        assert "MOBAIDE_CLAUDE_BIN" in resp["error"]
        assert resp["searched_paths"] == [
            "PATH:claude",
            "/home/test/.npm-global/bin/claude",
        ]


# -- _handle_proxy_request: OSError/FileNotFoundError branches -----------------


class TestHandleProxyRequestPathErrors:
    """Regression guards for WARN-01 on the proxy path.

    When a mobile client reaches the agent via the central relay server
    (``POST /api/sessions`` proxied through a ``proxy_request`` control
    frame), the same 4-backslash symptom could resurface in the
    ``proxy_response.body.detail`` field. The new ``FileNotFoundError``
    and ``OSError`` branches in ``_handle_proxy_request`` protect this
    second path; prior to WARN-01 the method had zero test coverage at
    all.
    """

    async def test_proxy_post_sessions_file_not_found_returns_400(
        self, client, mock_session_manager
    ):
        """``POST /api/sessions`` proxied through the relay with a missing
        cwd yields status=400 and a clean detail mirroring the REST route.
        """
        mock_session_manager.create_session = AsyncMock(
            side_effect=FileNotFoundError(
                2, "No such file or directory", r"D:\raw\ghost\dir",
            )
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-1",
            "method": "POST",
            "path": "/api/sessions",
            "body": {},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        sent_bytes = ws.send.call_args[0][0]
        _, _, payload = decode(sent_bytes)
        resp = json.loads(payload)

        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-1"
        assert resp["status"] == 400
        assert resp["body"]["detail"] == (
            r"Project path not found: D:\raw\ghost\dir"
        )

        # Wire-form guard: exactly one layer of backslash escaping.
        wire_json = payload.decode("utf-8")
        assert r"D:\\raw\\ghost\\dir" in wire_json
        assert r"D:\\\\raw\\\\ghost\\\\dir" not in wire_json

    async def test_proxy_post_sessions_permission_error_returns_400(
        self, client, mock_session_manager
    ):
        """Non-FileNotFoundError ``OSError`` on the proxy path goes through
        the broader ``except OSError`` branch and still returns 400 with a
        clean detail (strerror + filename, f-string, no ``repr``)."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=PermissionError(13, "Permission denied", r"D:\locked"),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-2",
            "method": "POST",
            "path": "/api/sessions",
            "body": {},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        sent_bytes = ws.send.call_args[0][0]
        _, _, payload = decode(sent_bytes)
        resp = json.loads(payload)

        assert resp["type"] == "proxy_response"
        assert resp["status"] == 400
        assert resp["body"]["detail"] == r"Permission denied: D:\locked"

        wire_json = payload.decode("utf-8")
        assert r"Permission denied: D:\\locked" in wire_json
        assert r"D:\\\\locked" not in wire_json

    async def test_proxy_claude_cli_missing_returns_503_with_discriminator(
        self, client, mock_session_manager
    ):
        """``POST /api/sessions`` proxied through the relay must map
        ``ClaudeBinaryNotFoundError`` to status=503 (not 400 / not 500) and
        include ``claude_cli_missing: true`` in the body so the mobile
        client can render a specialized error UI."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=ClaudeBinaryNotFoundError(
                searched_paths=["PATH:claude", "/usr/local/bin/claude"]
            ),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-cli-missing",
            "method": "POST",
            "path": "/api/sessions",
            "body": {},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-cli-missing"
        assert resp["status"] == 503
        assert resp["body"]["claude_cli_missing"] is True
        assert resp["body"]["detail"].startswith("Claude CLI not found:")
        assert "npm install -g @anthropic-ai/claude-code" in resp["body"]["detail"]
        assert resp["body"]["searched_paths"] == [
            "PATH:claude",
            "/usr/local/bin/claude",
        ]

    async def test_proxy_generic_exception_still_returns_500(
        self, client, mock_session_manager
    ):
        """Regression guard: non-OSError failures still fall through to the
        existing ``except Exception`` branch and produce status=500 — the
        new OSError branches must not accidentally swallow generic errors.
        """
        mock_session_manager.create_session = AsyncMock(
            side_effect=RuntimeError("boom"),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-3",
            "method": "POST",
            "path": "/api/sessions",
            "body": {},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["status"] == 500
        assert "boom" in resp["body"]["detail"]


# -- _handle_session_input ------------------------------------------------------


class TestHandleSessionInput:
    async def test_input_frame_writes_to_session(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = FRAME_INPUT + b"echo hello\r"
        await client._handle_session_input("s1", payload, ws)

        mock_session.write.assert_awaited_once_with(b"echo hello\r")

    async def test_resize_frame_resizes_session(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = FRAME_RESIZE + struct.pack(">HH", 50, 132)
        await client._handle_session_input("s1", payload, ws)

        mock_session.resize.assert_awaited_once_with(50, 132)

    async def test_execute_frame_sends_ctrl_c_then_data(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = FRAME_EXECUTE + b"ls -la\r"

        with patch("mobaide.relay.client.asyncio.sleep", new_callable=AsyncMock):
            await client._handle_session_input("s1", payload, ws)

        calls = mock_session.write.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0] == b"\x03"
        assert calls[1][0][0] == b"ls -la\r"

    async def test_empty_payload_is_noop(self, client, mock_session_manager):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        await client._handle_session_input("s1", b"", ws)

        mock_session.write.assert_not_awaited()
        mock_session.resize.assert_not_awaited()

    async def test_unknown_session_triggers_auto_create(
        self, client, mock_session_manager
    ):
        # First call: session not found, second call: after creation
        mock_new_session = AsyncMock()
        mock_new_session.session_id = "unknown-1"
        mock_new_session.state = SessionState.RUNNING
        mock_new_session.pid = 200
        mock_new_session.get_ring = MagicMock(return_value=b"")

        mock_session_manager.get_session = AsyncMock(return_value=None)
        mock_session_manager.create_session = AsyncMock(
            return_value=mock_new_session
        )

        ws = AsyncMock()

        with patch.object(client, "_bridge_session"):
            await client._handle_session_input(
                "unknown-1", FRAME_INPUT + b"hi", ws
            )

        mock_session_manager.create_session.assert_awaited_once()

    async def test_auto_create_sends_handshake_inline(
        self, client, mock_session_manager
    ):
        """Regression guard: once ``_handshake_sent`` is gone, the auto-create
        path must send the state/replay/ring handshake itself. Without it,
        a mobile whose very first relay frame targets an unknown session
        (the common "agent bounced, mobile is ahead of the session set"
        race) would never receive a state frame and its keyboard would
        stay grayed out — the exact bug this fix exists to prevent.
        """
        mock_new_session = AsyncMock()
        mock_new_session.session_id = "unknown-1"
        mock_new_session.state = SessionState.RUNNING
        mock_new_session.pid = 200
        mock_new_session.get_ring = MagicMock(return_value=b"cached-output")

        mock_session_manager.get_session = AsyncMock(return_value=None)
        mock_session_manager.create_session = AsyncMock(
            return_value=mock_new_session
        )

        ws = AsyncMock()

        with patch.object(client, "_bridge_session"):
            await client._handle_session_input(
                "unknown-1", FRAME_INPUT + b"hi", ws
            )

        handshake_sends = [
            call[0][0]
            for call in ws.send.call_args_list
        ]
        decoded = [decode(b) for b in handshake_sends]
        sid_frames = [d for d in decoded if d[0] == CH_SESSION_IO]
        assert len(sid_frames) >= 3, (
            f"expected state + replay + ring after auto-create, "
            f"got {len(sid_frames)} session-io frames"
        )

        first_three = sid_frames[:3]
        for ch, sid, _ in first_three:
            assert ch == CH_SESSION_IO
            assert sid == "unknown-1"

        assert first_three[0][2][:1] == FRAME_STATE
        state_payload = json.loads(first_three[0][2][1:])
        assert state_payload["status"] == "running"
        assert state_payload["pid"] == 200

        assert first_three[1][2] == FRAME_REPLAY

        assert first_three[2][2][:1] == FRAME_OUTPUT
        assert first_three[2][2][1:] == b"cached-output"


# -- _send_heartbeat ------------------------------------------------------------


class TestSendHeartbeat:
    async def test_heartbeat_sends_control_envelope(self, client):
        ws = AsyncMock()
        await client._send_heartbeat(ws)

        ws.send.assert_called_once()
        sent = ws.send.call_args[0][0]
        ch, sid, pl = decode(sent)
        assert ch == CH_CONTROL
        assert sid == ""
        msg = json.loads(pl)
        assert msg["type"] == "heartbeat"


# -- _cancel_all_subtasks -------------------------------------------------------


class TestCancelAllSubtasks:
    async def test_cancels_all_per_connection_tasks(self, client):
        # Simulate active subtasks
        async def _forever():
            await asyncio.sleep(999)

        client._heartbeat_task = asyncio.create_task(_forever())
        client._event_fwd_task = asyncio.create_task(_forever())
        client._sesslist_fwd_task = asyncio.create_task(_forever())
        client._session_watch_task = asyncio.create_task(_forever())

        bridge_task = asyncio.create_task(_forever())
        client._bridges["sess-1"] = bridge_task

        all_tasks = [
            client._heartbeat_task,
            client._event_fwd_task,
            client._sesslist_fwd_task,
            client._session_watch_task,
            bridge_task,
        ]

        client._cancel_all_subtasks()

        assert len(client._bridges) == 0

        for t in all_tasks:
            with pytest.raises(asyncio.CancelledError):
                await t
            assert t.cancelled()


# -- _handle_text ---------------------------------------------------------------


class TestHandleText:
    async def test_ping_text_sends_heartbeat(self, client):
        ws = AsyncMock()
        await client._handle_text(json.dumps({"type": "ping"}), ws)
        ws.send.assert_called_once()

    async def test_pairing_token_is_logged_no_send(self, client):
        ws = AsyncMock()
        msg = json.dumps({"type": "pairing_token", "expires_at": "2026-01-01"})
        await client._handle_text(msg, ws)
        ws.send.assert_not_called()

    async def test_malformed_json_text_is_ignored(self, client):
        ws = AsyncMock()
        await client._handle_text("not valid json", ws)
        ws.send.assert_not_called()


# -- start / stop lifecycle -----------------------------------------------------


class TestStartStop:
    async def test_start_creates_background_task(self, client):
        with patch.object(client, "_connect_loop", new_callable=AsyncMock):
            await client.start()
            assert client._task is not None
            assert not client._task.done()
            await client.stop()

    async def test_stop_sets_stopping_flag(self, client):
        with patch.object(client, "_connect_loop", new_callable=AsyncMock):
            await client.start()
            await client.stop()
            assert client._stopping is True

    async def test_stop_closes_websocket(self, client):
        ws = AsyncMock()
        client._ws = ws
        client._stopping = False

        with patch.object(client, "_connect_loop", new_callable=AsyncMock):
            await client.start()
            await client.stop()

        ws.close.assert_awaited_once()

    async def test_stop_without_start_is_safe(self, client):
        await client.stop()
        assert client._stopping is True


class TestProxyAutoscanStart:
    """Regression guards for the Auto Scan relay-mode bug: the proxy handler
    for ``POST /api/autoscan/start`` had been extracting ``claude_options``
    as a raw dict and forwarding it to ``AutoScanSessionManager.start``,
    causing ``'dict' object has no attribute 'plan_mode'`` deep in the
    scan loop. The fix mirrors the direct REST route: validate the body
    via ``StartScanRequest.model_validate(...)`` and call
    ``.to_options()`` to get a real ``ClaudeOptions`` dataclass.

    Also locks down the error-code contract, which the previous handler
    collapsed to a blanket 409: 404 (ValueError), 409
    (AutoScanAlreadyRunning), 422 (Pydantic ValidationError), and 429
    (AutoScanCapacityReached) must all round-trip distinctly so the
    mobile client sees the same statuses over relay as it does direct.
    Every assertion mirrors ``test_autoscan_router.py::TestStartScan``.
    """

    async def test_happy_path_forwards_claude_options_as_dataclass(
        self, client, mock_app_state,
    ):
        """A body with full ``claude_options`` must reach ``mgr.start`` as a
        ``ClaudeOptions`` dataclass, not a dict — this is the exact
        regression boundary."""
        from mobaide.core.claude_launcher import ClaudeOptions

        fake_inst = MagicMock()
        fake_inst.to_dto.return_value = {
            "project_name": "proj",
            "status": "running",
        }
        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock(return_value=fake_inst)

        ws = AsyncMock()
        body = {
            "project_name": "proj",
            "claude_options": {
                "plan_mode": True,
                "no_memory": False,
                "skip_permissions": False,
                "initial_prompt": "hello",
                "worktree": True,
                "branch_name": "feature/x",
                "identifier": "CLA-7",
                "title": "Some title",
                "max_effort": False,
            },
        }

        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", body, None, ws,
        )

        assert result["status"] == 200
        assert result["body"] == {"project_name": "proj", "status": "running"}

        mgr.start.assert_awaited_once()
        call = mgr.start.await_args
        assert call is not None
        assert call.args == ("proj",)
        opts = call.kwargs["client_options"]
        # The whole point of the bug fix: a real dataclass, not a dict.
        assert isinstance(opts, ClaudeOptions)
        assert not isinstance(opts, dict)
        assert opts.plan_mode is True
        assert opts.no_memory is False
        assert opts.skip_permissions is False
        assert opts.initial_prompt == "hello"
        assert opts.worktree is True
        assert opts.branch_name == "feature/x"
        assert opts.identifier == "CLA-7"
        assert opts.title == "Some title"
        assert opts.max_effort is False

    async def test_happy_path_without_claude_options_passes_none(
        self, client, mock_app_state,
    ):
        """When the body omits ``claude_options``, the handler must forward
        ``client_options=None`` — not an empty dict, which would re-trigger
        the original AttributeError class of bugs."""
        fake_inst = MagicMock()
        fake_inst.to_dto.return_value = {"project_name": "p", "status": "running"}
        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock(return_value=fake_inst)

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", {"project_name": "p"}, None, ws,
        )

        assert result["status"] == 200
        mgr.start.assert_awaited_once_with("p", client_options=None)

    async def test_invalid_claude_options_returns_422_and_never_calls_mgr(
        self, client, mock_app_state,
    ):
        """A body whose ``claude_options.plan_mode`` can't coerce to bool
        (Pydantic rejects arbitrary strings like ``"not_a_bool"`` — note
        that ``"yes"``/``"true"``/``"1"`` DO parse to ``True`` under the
        default bool validator, so we pick a value that genuinely fails)
        must surface as a 422 Pydantic error, and ``mgr.start`` must never
        be awaited. Previously the handler would have reached the domain
        layer with broken data."""
        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock()

        ws = AsyncMock()
        body = {
            "project_name": "p",
            "claude_options": {"plan_mode": "not_a_bool"},
        }

        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", body, None, ws,
        )

        assert result["status"] == 422
        assert "detail" in result["body"]
        assert isinstance(result["body"]["detail"], list)
        mgr.start.assert_not_awaited()

    async def test_missing_project_name_returns_422(
        self, client, mock_app_state,
    ):
        """``project_name`` is required — an empty body yields 422."""
        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", {}, None, ws,
        )

        assert result["status"] == 422
        mgr.start.assert_not_awaited()

    async def test_wrong_type_project_name_returns_422(
        self, client, mock_app_state,
    ):
        """``project_name: 123`` (non-string) is a validation error — 422,
        and the handler never reaches the domain layer."""
        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", {"project_name": 123}, None, ws,
        )

        assert result["status"] == 422
        mgr.start.assert_not_awaited()

    async def test_already_running_returns_409(self, client, mock_app_state):
        """``AutoScanAlreadyRunning`` from ``mgr.start`` must surface as 409
        — previously every exception collapsed to 409, so this test also
        guarantees the happy-path conflict case still maps correctly."""
        from mobaide.linear.scanner import AutoScanAlreadyRunning

        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock(side_effect=AutoScanAlreadyRunning("already going"))

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", {"project_name": "p"}, None, ws,
        )

        assert result["status"] == 409
        assert "already going" in result["body"]["detail"]

    async def test_capacity_reached_returns_429(self, client, mock_app_state):
        """``AutoScanCapacityReached`` must surface as 429. Previously the
        generic ``except Exception`` collapsed this to 409, hiding
        rate-limit signals from the mobile client."""
        from mobaide.linear.scanner import AutoScanCapacityReached

        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock(side_effect=AutoScanCapacityReached("at cap"))

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", {"project_name": "p"}, None, ws,
        )

        assert result["status"] == 429
        assert "at cap" in result["body"]["detail"]

    async def test_value_error_returns_404(self, client, mock_app_state):
        """``ValueError`` (unknown project) must surface as 404 — previously
        collapsed to 409, making it indistinguishable from the real
        ``AutoScanAlreadyRunning`` conflict case."""
        mgr = mock_app_state.autoscan_manager
        mgr.start = AsyncMock(side_effect=ValueError("unknown project"))

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/autoscan/start", {"project_name": "nope"}, None, ws,
        )

        assert result["status"] == 404
        assert "unknown project" in result["body"]["detail"]

    async def test_no_manager_returns_503(
        self, relay_config, mock_session_manager, mock_event_bus,
        mock_broadcaster,
    ):
        """When ``app_state`` has no ``autoscan_manager`` (misconfigured
        deployment), the handler returns 503 before any parsing — and
        crucially, does NOT try to call ``mgr.start`` on ``None``."""
        from mobaide.relay.client import RelayClient

        state = MagicMock(spec=[])  # no attributes at all
        cli = RelayClient(
            config=relay_config,
            session_manager=mock_session_manager,
            event_bus=mock_event_bus,
            broadcaster=mock_broadcaster,
            app_state=state,
        )

        ws = AsyncMock()
        result = await cli._dispatch_proxy(
            "POST", "/api/autoscan/start", {"project_name": "p"}, None, ws,
        )

        assert result["status"] == 503
        assert "not available" in result["body"]["detail"].lower()


class TestProxyCreateSessionClaudeOptions:
    """Regression guards for the latent relay ``POST /api/sessions`` bug:
    the proxy handler previously hand-parsed the body and silently
    dropped ``claude_options`` on the floor — a user creating a session
    via relay with plan_mode/no_memory/etc selected never had them
    reach ``SessionConfig``.

    The fix validates through ``CreateSessionRequest`` (same model the
    direct REST route uses) and forwards ``claude_options.to_options()``
    into ``SessionConfig``. These tests assert the handed-off config, not
    just the HTTP status, because the original bug was silent.
    """

    async def test_claude_options_forwarded_to_session_config(
        self, client, mock_session_manager,
    ):
        """The ``SessionConfig`` passed to ``create_session`` must carry a
        real ``ClaudeOptions`` populated from the body. This is the full
        latent-bug regression assertion — previously ``config.claude_options``
        would have been ``None`` and the selections vanished silently."""
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.models import SessionConfig, SessionState
        from datetime import datetime, timezone

        fake_info = MagicMock()
        fake_info.session_id = "sid-12"
        fake_info.state = SessionState.RUNNING
        fake_info.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fake_info.pid = 4242
        fake_info.command = ["/bin/bash"]
        fake_info.cwd = "/tmp"

        fake_session = MagicMock()
        fake_session.to_info.return_value = fake_info
        mock_session_manager.create_session = AsyncMock(return_value=fake_session)

        ws = AsyncMock()
        body = {
            "project_name": "my-proj",
            "claude_options": {
                "plan_mode": True,
                "no_memory": True,
                "skip_permissions": False,
                "initial_prompt": "hi",
                "worktree": True,
                "branch_name": "feature/relay",
                "identifier": "CLA-8",
                "title": "Relay fix",
                "max_effort": False,
            },
            "rows": 30,
            "cols": 120,
        }

        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy(
                "POST", "/api/sessions", body, None, ws,
            )

        assert result["status"] == 201
        assert result["body"]["session_id"] == "sid-12"

        mock_session_manager.create_session.assert_awaited_once()
        call = mock_session_manager.create_session.await_args
        assert call is not None
        config = call.args[0]
        assert isinstance(config, SessionConfig)
        assert config.project_name == "my-proj"
        assert config.rows == 30
        assert config.cols == 120

        opts = config.claude_options
        assert isinstance(opts, ClaudeOptions)
        assert opts.plan_mode is True
        assert opts.no_memory is True
        assert opts.skip_permissions is False
        assert opts.initial_prompt == "hi"
        assert opts.worktree is True
        assert opts.branch_name == "feature/relay"
        assert opts.identifier == "CLA-8"
        assert opts.title == "Relay fix"
        assert opts.max_effort is False

    async def test_no_claude_options_passes_none_in_config(
        self, client, mock_session_manager,
    ):
        """A body without ``claude_options`` must produce a ``SessionConfig``
        whose ``claude_options`` is ``None`` — never ``{}``, never a bare
        ``ClaudeOptionsModel`` instance."""
        from mobaide.core.models import SessionState
        from datetime import datetime, timezone

        fake_info = MagicMock()
        fake_info.session_id = "sid-none"
        fake_info.state = SessionState.RUNNING
        fake_info.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fake_info.pid = 1
        fake_info.command = ["/bin/bash"]
        fake_info.cwd = None

        fake_session = MagicMock()
        fake_session.to_info.return_value = fake_info
        mock_session_manager.create_session = AsyncMock(return_value=fake_session)

        ws = AsyncMock()
        body = {"project_name": "my-proj"}

        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy(
                "POST", "/api/sessions", body, None, ws,
            )

        assert result["status"] == 201
        call = mock_session_manager.create_session.await_args
        assert call is not None
        config = call.args[0]
        assert config.claude_options is None

    async def test_malformed_body_returns_422_and_never_creates_session(
        self, client, mock_session_manager,
    ):
        """A body with a wrong-typed field (e.g. ``rows`` as a string)
        must return 422 via Pydantic validation, and ``create_session``
        must never be awaited."""
        mock_session_manager.create_session = AsyncMock()

        ws = AsyncMock()
        body = {"rows": "not-an-int"}

        result = await client._dispatch_proxy(
            "POST", "/api/sessions", body, None, ws,
        )

        assert result["status"] == 422
        assert "detail" in result["body"]
        assert isinstance(result["body"]["detail"], list)
        mock_session_manager.create_session.assert_not_awaited()

    async def test_invalid_claude_options_returns_422(
        self, client, mock_session_manager,
    ):
        """Malformed nested ``claude_options`` also yields 422 — the
        validation is deep, not just top-level. Parallels the
        ``TestProxyAutoscanStart`` 422 case."""
        mock_session_manager.create_session = AsyncMock()

        ws = AsyncMock()
        body = {"claude_options": {"plan_mode": "not_a_bool"}}

        result = await client._dispatch_proxy(
            "POST", "/api/sessions", body, None, ws,
        )

        assert result["status"] == 422
        mock_session_manager.create_session.assert_not_awaited()


class TestProxyGitItems:
    """Regression guards for the missing relay route bug: the FastAPI git
    router (``/api/projects/{name}/git/items`` GET, ``items:delete`` POST,
    added in CLA-46) was registered for direct HTTP but the relay's
    ``_dispatch_proxy`` allowlist was never extended. Mobile clients
    (which always route through the relay) hit "Unknown proxy route"
    404s on every Git button tap.

    These tests lock down the relay-path contract: shape parity with the
    direct HTTP path, project-resolution semantics (404 / 400), Pydantic
    422 on malformed bodies, and — most importantly — the nested
    ``detail.busy_by`` shape on 409 responses, which the mobile
    busy-conflict dialog parses.
    """

    def _make_project(self, tmp_path, name="proj", with_git=True):
        from unittest.mock import MagicMock
        if with_git:
            (tmp_path / ".git").mkdir(exist_ok=True)
        project = MagicMock()
        project.name = name
        project.path = str(tmp_path)
        return project

    async def test_get_items_happy_path(
        self, client, mock_app_state, tmp_path,
    ):
        """Happy path: 200 with a Pydantic-serialized items list. Body
        shape must round-trip through ``GitItemsResponse.model_dump`` so
        it matches the FastAPI router's response byte-for-byte."""
        from mobaide.agent.git.models import GitItemRead

        project = self._make_project(tmp_path, name="proj")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()

        items = [GitItemRead(kind="branch_only", branch="main")]
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.list_git_items",
            new=AsyncMock(return_value=items),
        ) as mock_list:
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/git/items", None, None, ws,
            )

        assert result["status"] == 200
        assert result["body"]["items"][0]["kind"] == "branch_only"
        assert result["body"]["items"][0]["branch"] == "main"
        mock_list.assert_awaited_once()

    async def test_get_items_unknown_project_returns_404(
        self, client, mock_app_state,
    ):
        """``store.get`` returning ``None`` must surface as 404 with the
        same detail string as the FastAPI router (``router.py:84-87``)."""
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=None)
        mock_app_state.worktree_manager = MagicMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "GET", "/api/projects/missing/git/items", None, None, ws,
        )

        assert result["status"] == 404
        assert "not found" in result["body"]["detail"]

    async def test_get_items_non_git_project_returns_400(
        self, client, mock_app_state, tmp_path,
    ):
        """A project whose ``.path`` lacks a ``.git`` entry must surface
        as 400 (matches ``router.py:89-93``); ``.git`` may be a directory
        or a file (worktrees), so we use ``Path.exists`` not ``is_dir``."""
        project = self._make_project(tmp_path, name="proj", with_git=False)
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "GET", "/api/projects/proj/git/items", None, None, ws,
        )

        assert result["status"] == 400
        assert "not a git repository" in result["body"]["detail"]

    async def test_post_delete_happy_path(
        self, client, mock_app_state, tmp_path,
    ):
        """POST ``items:delete`` with a single branch delete returns the
        Pydantic-serialized ``GitDeleteResponse`` body."""
        from mobaide.agent.git.models import (
            GitDeleteResponse,
            GitDeleteResultItem,
        )

        project = self._make_project(tmp_path, name="proj")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()
        mock_app_state.session_manager = MagicMock()
        mock_app_state.event_bus = MagicMock()

        resp = GitDeleteResponse(results=[
            GitDeleteResultItem(kind="branch_only", target="x", ok=True),
        ])
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.delete_git_items",
            new=AsyncMock(return_value=resp),
        ) as mock_delete:
            result = await client._dispatch_proxy(
                "POST",
                "/api/projects/proj/git/items:delete",
                {"items": [{"kind": "branch_only", "branch": "x"}]},
                None,
                ws,
            )

        assert result["status"] == 200
        assert result["body"]["results"][0]["ok"] is True
        assert result["body"]["results"][0]["target"] == "x"
        mock_delete.assert_awaited_once()
        await_args = mock_delete.await_args
        assert await_args is not None
        assert await_args.kwargs["project_name"] == "proj"
        assert await_args.kwargs["actor"] is None

    async def test_post_delete_busy_returns_409_with_nested_detail(
        self, client, mock_app_state, tmp_path,
    ):
        """**Load-bearing test for the 409 nesting.** The FastAPI router
        does ``HTTPException(detail=BusyByResponse(...).model_dump())``,
        which FastAPI serializes as ``{"detail": {"busy_by": [...]}}``.
        The relay must produce the exact same shape — the mobile client
        reads ``body.detail.busy_by``, NOT ``body.busy_by``. Putting the
        ``busy_by`` array at top level is the most likely regression in
        this fix and would silently break the busy-conflict dialog."""
        from mobaide.agent.git.models import BusyHolder
        from mobaide.agent.git.service import GitItemBusyError

        project = self._make_project(tmp_path, name="proj")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()
        mock_app_state.session_manager = MagicMock()
        mock_app_state.event_bus = MagicMock()

        busy_by = [
            BusyHolder(
                kind="session",
                session_id="s1",
                cwd="/p",
                worktree_path=str(tmp_path / "wt"),
            ),
        ]
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.delete_git_items",
            new=AsyncMock(side_effect=GitItemBusyError(busy_by=busy_by)),
        ):
            result = await client._dispatch_proxy(
                "POST",
                "/api/projects/proj/git/items:delete",
                {"items": [{"kind": "branch_with_worktree",
                            "branch": "x",
                            "worktree_path": str(tmp_path / "wt")}]},
                None,
                ws,
            )

        assert result["status"] == 409
        # Nested under "detail" — guards against the most-likely regression.
        assert "detail" in result["body"]
        assert "busy_by" in result["body"]["detail"]
        assert result["body"]["detail"]["busy_by"][0]["kind"] == "session"
        assert result["body"]["detail"]["busy_by"][0]["session_id"] == "s1"
        # Negative assertion: must NOT be at top level.
        assert "busy_by" not in result["body"]

    async def test_post_delete_malformed_returns_422(
        self, client, mock_app_state, tmp_path,
    ):
        """A body whose ``items[].kind`` isn't a valid ``ItemKind``
        literal yields 422 from Pydantic, and the service must never be
        invoked. Mirrors how ``TestProxyAutoscanStart`` validates."""
        project = self._make_project(tmp_path, name="proj")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()

        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.delete_git_items",
            new=AsyncMock(),
        ) as mock_delete:
            result = await client._dispatch_proxy(
                "POST",
                "/api/projects/proj/git/items:delete",
                {"items": [{"kind": "not_a_kind"}]},
                None,
                ws,
            )

        assert result["status"] == 422
        assert isinstance(result["body"]["detail"], list)
        mock_delete.assert_not_awaited()

    async def test_unsupported_method_falls_through_to_unknown_route(
        self, client, mock_app_state, tmp_path,
    ):
        """A path that matches the git regex but with a method other than
        GET / POST should NOT short-circuit with a git-flavored error —
        ``_dispatch_git`` returns ``None`` so the catch-all "Unknown
        proxy route" 404 fires. This documents the existing dispatcher
        contract: it is not method-aware per route, only path-aware."""
        project = self._make_project(tmp_path, name="proj")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "PUT", "/api/projects/proj/git/items", None, None, ws,
        )

        assert result["status"] == 404
        assert "Unknown proxy route" in result["body"]["detail"]

    async def test_url_decoded_space_in_project_name(
        self, client, mock_app_state, tmp_path,
    ):
        """The central server's ``{path:path}`` route URL-decodes path
        params before forwarding through the relay envelope, so a
        project named ``Mobaide Project`` arrives at ``_dispatch_proxy``
        with a literal space (the symptom in the original bug report).
        The regex ``[^/]+`` accepts spaces, and the captured name must
        be passed *as-is* to ``store.get`` (do NOT call
        ``urllib.parse.unquote`` — that would double-decode legit ``%xx``
        bytes inside names)."""
        from mobaide.agent.git.models import GitItemRead

        project = self._make_project(tmp_path, name="Mobaide Project")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()

        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.list_git_items",
            new=AsyncMock(return_value=[GitItemRead(kind="branch_only",
                                                    branch="main")]),
        ):
            result = await client._dispatch_proxy(
                "GET",
                "/api/projects/Mobaide Project/git/items",
                None, None, ws,
            )

        assert result["status"] == 200
        mock_app_state.projects_store.get.assert_called_once_with(
            "Mobaide Project",
        )


class TestProxyLinearDiscoveryRelayParity:
    """Regression guards for the new Linear discovery endpoints
    (``GET /api/projects/{name}/linear/labels`` and ``/states``): they must
    reach the relay's ``_dispatch_proxy`` allowlist. Mobile clients connect
    through the central server in production, and a missing relay route
    would surface as ``404 "Unknown proxy route"`` on every edit-sheet
    open — the identical regression class a prior git-router gap exposed.

    These tests pin the relay-path contract: project-resolution semantics
    (404 / 400), upstream Linear failure mapping (502), and a happy-path
    that confirms the discovery client was actually called with the project's
    stored API key.
    """

    def _make_project(self, name="proj", api_key: str | None = "lin_secret"):
        project = MagicMock()
        project.name = name
        project.linear_api_key = api_key
        return project

    async def test_labels_unknown_project_returns_404(
        self, client, mock_app_state,
    ):
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=None)

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "GET", "/api/projects/missing/linear/labels", None, None, ws,
        )

        assert result["status"] == 404
        assert "not found" in result["body"]["detail"]

    async def test_states_unknown_project_returns_404(
        self, client, mock_app_state,
    ):
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=None)

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "GET", "/api/projects/missing/linear/states", None, None, ws,
        )

        assert result["status"] == 404

    async def test_labels_no_api_key_returns_400(
        self, client, mock_app_state,
    ):
        project = self._make_project(api_key=None)
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "GET", "/api/projects/proj/linear/labels", None, None, ws,
        )

        assert result["status"] == 400
        assert "linear_api_key" in result["body"]["detail"]

    async def test_labels_happy_path_uses_stored_api_key(
        self, client, mock_app_state,
    ):
        project = self._make_project(api_key="lin_test_key")
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)

        captured = {}

        class _Stub:
            def __init__(self, api_key, rate_limiter=None):
                captured["api_key"] = api_key

            async def get_labels(self):
                return [{"name": "AutoPipeline", "color": "#ff0000"}]

        ws = AsyncMock()
        with patch("mobaide.relay.client.LinearApiClient", _Stub):
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/linear/labels", None, None, ws,
            )

        assert result["status"] == 200
        assert captured["api_key"] == "lin_test_key"
        assert result["body"] == [{"name": "AutoPipeline", "color": "#ff0000"}]

    async def test_states_happy_path_returns_workflow_states(
        self, client, mock_app_state,
    ):
        project = self._make_project()
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)

        class _Stub:
            def __init__(self, *args, **kwargs):
                pass

            async def get_workflow_states(self):
                return [
                    {"name": "Proposals", "type": "backlog"},
                    {"name": "Done", "type": "completed"},
                ]

        ws = AsyncMock()
        with patch("mobaide.relay.client.LinearApiClient", _Stub):
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/linear/states", None, None, ws,
            )

        assert result["status"] == 200
        names = [s["name"] for s in result["body"]]
        assert names == ["Proposals", "Done"]

    async def test_labels_upstream_failure_returns_502(
        self, client, mock_app_state,
    ):
        project = self._make_project()
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)

        class _Boom:
            def __init__(self, *args, **kwargs):
                pass

            async def get_labels(self):
                raise RuntimeError("linear is down")

        ws = AsyncMock()
        with patch("mobaide.relay.client.LinearApiClient", _Boom):
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/linear/labels", None, None, ws,
            )

        assert result["status"] == 502
        # The internal exception detail is not echoed to the client.
        body_detail = result["body"]["detail"]
        assert "linear is down" not in body_detail
        assert "Failed to fetch" in body_detail

    async def test_post_to_discovery_endpoint_falls_through(
        self, client, mock_app_state,
    ):
        # Method-mismatch: POST should NOT match the GET-only dispatcher,
        # falling through to the catch-all 404 "Unknown proxy route".
        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/projects/proj/linear/labels", None, None, ws,
        )

        assert result["status"] == 404
        assert "Unknown proxy route" in result["body"]["detail"]


class TestProxySessionsWireShape:
    """Regression guards for the wire-shape consolidation: both
    ``GET /api/sessions`` and ``POST /api/sessions`` proxy responses must
    be built through :func:`session_to_wire_dict` so the 8-field canonical
    wire shape (including ``title`` and ``project_name``) is present.

    Without these assertions a future developer could revert the call site
    back to an inline 6-field dict literal, silently dropping the new
    fields the Android client relies on to render friendly session labels.
    """

    async def test_get_sessions_body_carries_title_and_project_name(
        self, client, mock_session_manager,
    ):
        """``GET /api/sessions`` must return a list of canonical wire dicts."""
        from mobaide.core.models import SessionInfo, SessionState

        info_a = SessionInfo(
            session_id="sid-a",
            state=SessionState.RUNNING,
            created_at=datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc),
            pid=111,
            command=["claude"],
            cwd="/work/a",
            title="* Refactoring parser",
            project_name="alpha",
        )
        info_b = SessionInfo(
            session_id="sid-b",
            state=SessionState.STOPPED,
            created_at=datetime(2026, 5, 19, 12, 5, 0, tzinfo=timezone.utc),
            pid=None,
            command=["claude"],
            cwd=None,
            title=None,
            project_name=None,
        )
        mock_session_manager.list_sessions = MagicMock(
            return_value=[info_a, info_b],
        )

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "GET", "/api/sessions", None, None, ws,
        )

        assert result["status"] == 200
        body = result["body"]
        assert isinstance(body, list)
        assert len(body) == 2

        a, b = body
        # Canonical 9-key shape, in any order — locks down session_to_wire_dict.
        expected_keys = {
            "session_id", "state", "created_at", "pid",
            "command", "cwd", "title", "project_name", "kind",
        }
        assert set(a.keys()) == expected_keys
        assert set(b.keys()) == expected_keys

        # The two new fields the Android list UI depends on.
        assert a["title"] == "* Refactoring parser"
        assert a["project_name"] == "alpha"
        # Untitled session: title/project_name carried explicitly as None.
        assert b["title"] is None
        assert b["project_name"] is None

        # SessionInfo defaults kind=CLAUDE so existing fixtures (no explicit kind) wire as "claude".
        assert a["kind"] == "claude"
        assert b["kind"] == "claude"

        # Spot-check the rest survived the helper round-trip unchanged.
        assert a["session_id"] == "sid-a"
        assert a["state"] == "running"
        assert a["pid"] == 111
        assert a["command"] == ["claude"]
        assert a["cwd"] == "/work/a"
        assert a["created_at"].startswith("2026-05-19T12:00:00")

    async def test_post_sessions_body_carries_title_and_project_name(
        self, client, mock_session_manager,
    ):
        """``POST /api/sessions`` 201 body must be a canonical wire dict."""
        from mobaide.core.models import SessionInfo, SessionState

        created_info = SessionInfo(
            session_id="sid-new",
            state=SessionState.RUNNING,
            created_at=datetime(2026, 5, 19, 13, 0, 0, tzinfo=timezone.utc),
            pid=4242,
            command=["claude"],
            cwd="/work/new",
            title=None,  # title arrives later via OSC; the create response can have None
            project_name="my-proj",
        )
        fake_session = MagicMock()
        fake_session.to_info.return_value = created_info
        mock_session_manager.create_session = AsyncMock(return_value=fake_session)

        ws = AsyncMock()
        body_in = {"project_name": "my-proj", "rows": 30, "cols": 120}

        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy(
                "POST", "/api/sessions", body_in, None, ws,
            )

        assert result["status"] == 201
        body = result["body"]
        assert isinstance(body, dict)

        # Canonical 9-key shape — the consolidation contract.
        assert set(body.keys()) == {
            "session_id", "state", "created_at", "pid",
            "command", "cwd", "title", "project_name", "kind",
        }
        # The two new fields are present (even when title is None at create).
        assert body["title"] is None
        assert body["project_name"] == "my-proj"

        # Spot-check the surrounding fields wire correctly.
        assert body["session_id"] == "sid-new"
        assert body["state"] == "running"
        assert body["pid"] == 4242
        # SessionInfo defaults kind=CLAUDE.
        assert body["kind"] == "claude"

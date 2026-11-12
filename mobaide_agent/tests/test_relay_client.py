"""Tests for RelayClient internals — unit-level, no real WebSocket server.

Covers: _state_frame helper, _authenticate, _handle_control dispatch,
_handle_session_input routing, _cancel_all_subtasks, exponential backoff
in _connect_loop, and _send_heartbeat.
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mobaide.relay.client import (
    FRAME_EXECUTE,
    FRAME_INPUT,
    FRAME_OUTPUT,
    FRAME_REPLAY,
    FRAME_REPLAY_END,
    FRAME_RESIZE,
    FRAME_STATE,
    FRAME_SUBMIT,
    UNKNOWN_SESSION_NOTIFY_CAP,
    RelayClient,
    _STATE_MAP,
    _attention_user_event,
    _state_frame,
)
from mobaide.relay.envelope import (
    CH_CONTROL,
    CH_EVENTS,
    CH_SESSION_IO,
    CH_SESSIONS_LIST,
    decode,
    encode,
)
from mobaide.agent.events.bus import AgentEvent
from mobaide.agent.git.changes_service import (
    GitBadRequestError,
    GitNotFoundError,
    GitRefusedError,
)
from mobaide.core import (
    ClaudeBinaryNotFoundError,
    CodexAuthRequiredError,
    CodexBinaryNotFoundError,
)
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


def _mock_iter_ring_chunks(ring: bytes, chunk_size: int | None = None):
    """Build a ``side_effect`` callable for mocked ``iter_ring_chunks``.

    Returns a fresh iterator on every call so tests that exercise multiple
    handshakes (e.g. ``test_attach_is_repeatable_no_idempotency_cache``)
    aren't held back by an exhausted generator from the previous call.
    """
    from mobaide.core.session import REPLAY_CHUNK_BYTES

    def factory(*_args, **_kwargs):
        cs = chunk_size or REPLAY_CHUNK_BYTES
        return iter(ring[i : i + cs] for i in range(0, len(ring), cs))

    return factory


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


# -- Mapping constants ----------------------------------------------------------


class TestMappingConstants:
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
        mock_session.iter_ring_chunks = MagicMock(
            side_effect=_mock_iter_ring_chunks(b"hello world"),
        )
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "s1"}
        ).encode()
        await client._handle_control(payload, ws)

        # Chunked replay handshake (protocol v3+):
        # state → replay → output(chunk_0)…output(chunk_N) → replay_end
        # The 11-byte ring fits in a single 64 KB chunk, so N=1.
        assert ws.send.await_count == 4
        sent = [ws.send.call_args_list[i][0][0] for i in range(4)]
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

        assert decoded[3][2] == FRAME_REPLAY_END

    async def test_attach_sends_multi_chunk_handshake_when_ring_exceeds_chunk_size(
        self, client, mock_session_manager,
    ):
        """Relay-mirror chunking parity with the direct WS handshake: when
        the session's ring exceeds ``REPLAY_CHUNK_BYTES`` the attach handler
        must emit **two or more** ``FRAME_OUTPUT`` envelopes between the
        ``FRAME_REPLAY`` marker and the ``FRAME_REPLAY_END`` terminator, and
        their concatenation must be byte-equal to the original ring.

        The single-chunk path is already covered by
        ``test_attach_sends_full_handshake_in_order``; this guards the
        relay-tunnel mirror against drifting from
        ``mobaide.agent.ws.session_io`` when the chunk count is > 1.
        """
        from mobaide.core.session import REPLAY_CHUNK_BYTES

        # 64 KiB + 1 — boundary case from the plan; unique bytes modulo 256
        # so a misorder or head-trim would not pass the byte-equal check.
        ring = bytes((i & 0xFF) for i in range(REPLAY_CHUNK_BYTES + 1))

        mock_session = AsyncMock()
        mock_session.session_id = "s-multi"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 99
        mock_session.iter_ring_chunks = MagicMock(
            side_effect=_mock_iter_ring_chunks(ring),
        )
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "s-multi"}
        ).encode()
        await client._handle_control(payload, ws)

        sent = [ws.send.call_args_list[i][0][0] for i in range(ws.send.await_count)]
        decoded = [decode(b) for b in sent]
        for ch, sid, _ in decoded:
            assert ch == CH_SESSION_IO
            assert sid == "s-multi"

        # Wire sequence: STATE → REPLAY → N × OUTPUT → REPLAY_END, N ≥ 2.
        assert decoded[0][2][:1] == FRAME_STATE
        assert decoded[1][2] == FRAME_REPLAY
        assert decoded[-1][2] == FRAME_REPLAY_END

        output_payloads = [d[2][1:] for d in decoded[2:-1]]
        assert all(d[2][:1] == FRAME_OUTPUT for d in decoded[2:-1])
        assert len(output_payloads) >= 2, (
            f"ring of size REPLAY_CHUNK_BYTES + 1 must emit ≥ 2 output "
            f"chunks; got {len(output_payloads)}"
        )
        for chunk in output_payloads[:-1]:
            assert len(chunk) == REPLAY_CHUNK_BYTES
        assert len(output_payloads[-1]) <= REPLAY_CHUNK_BYTES
        assert b"".join(output_payloads) == ring

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
        mock_session.iter_ring_chunks = MagicMock(
            side_effect=_mock_iter_ring_chunks(b""),
        )
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = json.dumps(
            {"type": "mobile_session_attach", "session_id": "s1"}
        ).encode()

        await client._handle_control(payload, ws)
        await client._handle_control(payload, ws)
        await client._handle_control(payload, ws)

        # 3 attaches × 4 handshake sends each (state + replay + output(chunk)
        # + replay_end). Empty ring still emits one output frame between the
        # markers, so the per-attach count is fixed.
        assert ws.send.await_count == 12

    async def test_unknown_attach_answers_after_input_path_notified(
        self, client, mock_session_manager
    ):
        """Both paths send the same frame through one helper, so the attach
        must not inherit the input path's once-per-id set: the reopen that
        follows a burst of dropped frames is exactly when the client needs
        the answer.
        """
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        await client._handle_session_input("ghost", FRAME_INPUT + b"hi", ws)
        ws.send.reset_mock()

        await client._handle_control(
            json.dumps(
                {"type": "mobile_session_attach", "session_id": "ghost"}
            ).encode(),
            ws,
        )

        ws.send.assert_awaited_once()
        channel, sid, pl = decode(ws.send.call_args[0][0])
        assert channel == CH_SESSION_IO
        assert sid == "ghost"
        assert json.loads(pl[1:]) == {"status": "stopped"}


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
        sess_info.kind_label = None
        sess_info.attention = None
        sess_info.exit_reason = None
        sess_info.ui = "terminal"
        sess_info.activity = None
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
                    "kind_label": None,
                    "attention": None,
                    "exit_reason": None,
                    "ui": "terminal",
                    "activity": None,
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
        sess_info.kind_label = None
        sess_info.attention = None
        sess_info.exit_reason = None
        sess_info.ui = "terminal"
        sess_info.activity = None
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
                "kind_label": None,
                "attention": None,
                "exit_reason": None,
                "ui": "terminal",
                "activity": None,
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

    async def test_codex_auth_required_emits_codex_auth_required_marker(
        self, client, mock_session_manager
    ):
        """``CodexAuthRequiredError`` from the spawn path maps to
        ``session_create_failed`` with ``codex_auth_required: true`` top-level
        (parallel to ``<cli>_cli_missing``) so the mobile client opens a login
        prompt rather than the generic error toast."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=CodexAuthRequiredError(),
        )
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "no-auth-sess"}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "no-auth-sess"
        assert resp["codex_auth_required"] is True
        assert isinstance(resp["error"], str) and resp["error"]


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

    async def test_proxy_codex_auth_required_returns_409_with_marker(
        self, client, mock_session_manager
    ):
        """``POST /api/sessions`` proxied through the relay must map
        ``CodexAuthRequiredError`` to status=409 (not 401 / not 503 / not 500)
        with ``codex_auth_required: true`` in the body so the mobile client
        renders the login prompt — 409 (not 401) so the Android AuthInterceptor
        doesn't refresh-retry and swallow the marker."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=CodexAuthRequiredError(),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-auth",
            "method": "POST",
            "path": "/api/sessions",
            "body": {},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-auth"
        assert resp["status"] == 409
        assert resp["body"]["codex_auth_required"] is True
        assert isinstance(resp["body"]["detail"], str) and resp["body"]["detail"]


# -- _dispatch_proxy: POST /api/codex/login ------------------------------------


class TestDispatchProxyCodexLogin:
    """``POST /api/codex/login`` proxied through the relay mirrors the direct
    HTTP handler: 503 when Codex isn't configured, 422 on a malformed body
    (with the raw key scrubbed), 200 on success, 401 on login failure.
    """

    def _login_ctrl(self, body, request_id="req-login"):
        return {
            "type": "proxy_request",
            "request_id": request_id,
            "method": "POST",
            "path": "/api/codex/login",
            "body": body,
        }

    async def test_malformed_body_returns_422_and_scrubs_key(
        self, client, mock_app_state
    ):
        """A blank ``api_key`` trips ``CodexLoginRequest`` validation; the 422
        body must not echo the raw key (it is in ``_SECRET_VALIDATION_FIELDS``)."""
        mock_app_state.codex_launcher = MagicMock()
        ws = AsyncMock()

        await client._handle_control(
            json.dumps(self._login_ctrl({"api_key": ""})).encode(), ws,
        )

        _, _, payload = decode(ws.send.call_args[0][0])
        resp = json.loads(payload)
        assert resp["status"] == 422
        wire = payload.decode("utf-8")
        assert '"input": ""' not in wire
        assert '"input":""' not in wire

    async def test_body_level_malformed_body_scrubs_nested_key(
        self, client, mock_app_state
    ):
        """A whole-body type error (a JSON array, not an object) is reported with
        the entire raw body as ``input`` — the field-name scrub can't see the
        nested ``api_key``, so the relay's payload walk must strip it (mirrors the
        agent app.py guarantee)."""
        mock_app_state.codex_launcher = MagicMock()
        ws = AsyncMock()

        await client._handle_control(
            json.dumps(
                self._login_ctrl([{"api_key": "sk-relay-body-leak-123456"}]),
            ).encode(),
            ws,
        )

        _, _, payload = decode(ws.send.call_args[0][0])
        resp = json.loads(payload)
        assert resp["status"] == 422
        assert "sk-relay-body-leak-123456" not in payload.decode("utf-8")

    async def test_successful_login_returns_200_authenticated(
        self, client, mock_app_state, monkeypatch
    ):
        launcher = MagicMock()
        launcher.resolve_binary.return_value = "codex"
        mock_app_state.codex_launcher = launcher

        async def _ok(api_key, *, codex_bin, env, **_kw):
            return True, ""

        monkeypatch.setattr("mobaide.relay.client.codex_login_with_api_key", _ok)
        ws = AsyncMock()

        await client._handle_control(
            json.dumps(self._login_ctrl({"api_key": "sk-good"})).encode(), ws,
        )

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["status"] == 200
        assert resp["body"]["authenticated"] is True

    async def test_failed_login_returns_400_with_sanitized_detail(
        self, client, mock_app_state, monkeypatch
    ):
        # 400 (not 401): mirrors the REST router so the Android AuthInterceptor
        # doesn't refresh-retry on a rejected key and re-run the subprocess.
        launcher = MagicMock()
        launcher.resolve_binary.return_value = "codex"
        mock_app_state.codex_launcher = launcher

        async def _fail(api_key, *, codex_bin, env, **_kw):
            return False, "rejected ***"

        monkeypatch.setattr("mobaide.relay.client.codex_login_with_api_key", _fail)
        ws = AsyncMock()

        await client._handle_control(
            json.dumps(self._login_ctrl({"api_key": "sk-bad-9999999999"})).encode(),
            ws,
        )

        _, _, payload = decode(ws.send.call_args[0][0])
        resp = json.loads(payload)
        assert resp["status"] == 400
        assert resp["body"]["detail"].startswith("Codex login failed:")
        assert "sk-bad-9999999999" not in payload.decode("utf-8")

    async def test_binary_not_found_propagates_to_503(self, client, mock_app_state):
        """``resolve_binary`` raising ``CodexBinaryNotFoundError`` propagates to
        ``_handle_proxy_request``'s 503 handler (same as ``POST /api/sessions``)."""
        launcher = MagicMock()
        launcher.resolve_binary.side_effect = CodexBinaryNotFoundError(
            searched_paths=["PATH:codex"],
        )
        mock_app_state.codex_launcher = launcher
        ws = AsyncMock()

        await client._handle_control(
            json.dumps(self._login_ctrl({"api_key": "sk-x"})).encode(), ws,
        )

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["status"] == 503
        assert resp["body"]["codex_cli_missing"] is True


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

    async def test_execute_frame_calls_write_execute(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = FRAME_EXECUTE + b"ls -la"

        await client._handle_session_input("s1", payload, ws)

        mock_session.write_execute.assert_awaited_once_with(b"ls -la")

    async def test_submit_frame_calls_write_submit(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        payload = FRAME_SUBMIT + b"echo hi"

        await client._handle_session_input("s1", payload, ws)

        mock_session.write_submit.assert_awaited_once_with(b"echo hi")

    async def test_submit_frame_empty_payload_ignored(
        self, client, mock_session_manager
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        await client._handle_session_input("s1", FRAME_SUBMIT, ws)

        mock_session.write_submit.assert_not_awaited()
        mock_session.write.assert_not_awaited()


class TestUnknownSessionInput:
    """A CH_SESSION_IO frame naming a session this agent does not have is
    dropped, never treated as a request to spawn one.

    Both mobile clients re-assert their cached PTY size on every socket
    connect, so the previous auto-create turned merely attaching to a stale
    id (agent restarted, session swept, resumed id from preferences) into a
    real phantom shell that then re-entered the session list.
    """

    async def test_resize_frame_for_unknown_session_creates_nothing(
        self, client, mock_session_manager
    ):
        """The reported repro: the size re-assert every client sends on
        connect is a resize frame, and that is what used to materialize a
        session for an id nobody asked to create."""
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        await client._handle_session_input(
            "unknown-1", FRAME_RESIZE + struct.pack(">HH", 50, 132), ws
        )

        mock_session_manager.create_session.assert_not_awaited()

    async def test_unknown_session_answers_with_stopped_state_frame(
        self, client, mock_session_manager
    ):
        """The client is left waiting on a state frame otherwise: exactly one
        ``stopped`` frame, and none of the handshake the auto-create used to
        send (no replay, no ring, no ``running``)."""
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        await client._handle_session_input("unknown-1", FRAME_INPUT + b"hi", ws)

        decoded = [decode(call[0][0]) for call in ws.send.call_args_list]
        sid_frames = [d for d in decoded if d[0] == CH_SESSION_IO]
        assert len(sid_frames) == 1
        channel, sid, payload = sid_frames[0]
        assert channel == CH_SESSION_IO
        assert sid == "unknown-1"
        assert payload[:1] == FRAME_STATE
        assert json.loads(payload[1:]) == {"status": "stopped"}
        assert all(
            d[2][:1] not in (FRAME_OUTPUT, FRAME_REPLAY, FRAME_REPLAY_END)
            for d in sid_frames
        )

    async def test_repeat_frames_for_same_unknown_id_notify_once(
        self, client, mock_session_manager
    ):
        """A client that keeps pushing at a dead id must not have every frame
        echoed back down the shared tunnel."""
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        for _ in range(5):
            await client._handle_session_input(
                "unknown-1", FRAME_INPUT + b"hi", ws
            )

        assert ws.send.await_count == 1
        assert client._unknown_session_input_total == 5

    async def test_notified_id_set_stays_capped(
        self, client, mock_session_manager
    ):
        """The set is keyed on ids that come off the wire, so it must not be
        a memory sink a peer can grow without bound."""
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        for i in range(UNKNOWN_SESSION_NOTIFY_CAP + 5):
            await client._handle_session_input(
                f"ghost-{i}", FRAME_INPUT + b"hi", ws
            )

        assert len(client._unknown_session_notified) <= UNKNOWN_SESSION_NOTIFY_CAP

    async def test_empty_payload_for_unknown_session_is_dropped_first(
        self, client, mock_session_manager
    ):
        """The empty-payload guard runs before the session lookup: a zero-length
        envelope carries no request at all, so it must not even be counted or
        answered."""
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        await client._handle_session_input("unknown-1", b"", ws)

        mock_session_manager.get_session.assert_not_awaited()
        mock_session_manager.create_session.assert_not_awaited()
        ws.send.assert_not_awaited()
        assert client._unknown_session_input_total == 0

    async def test_notify_failure_does_not_propagate(
        self, client, mock_session_manager
    ):
        """A half-closed socket must not take down the reader loop that is
        still dispatching frames for live sessions."""
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()
        ws.send = AsyncMock(side_effect=ConnectionError("WS closed"))

        await client._handle_session_input("unknown-1", FRAME_INPUT + b"hi", ws)

        ws.send.assert_awaited_once()


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """Poll ``predicate`` until truthy; fail the test on timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class TestSessionInputQueues:
    """The tunnel reader must never block on PTY write delays: CH_SESSION_IO
    frames go through a per-session queue + pump, preserving per-session
    ordering while other sessions and control traffic keep flowing."""

    def _make_session(self, mock_session_manager):
        mock_session = AsyncMock()
        mock_session.session_id = "s1"
        mock_session.state = SessionState.RUNNING
        mock_session.pid = 100
        mock_session.get_ring = MagicMock(return_value=b"")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        return mock_session

    async def test_frames_dispatch_in_order_per_session(
        self, client, mock_session_manager
    ):
        mock_session = self._make_session(mock_session_manager)
        ws = AsyncMock()

        client._enqueue_session_input("s1", FRAME_SUBMIT + b"echo hi", ws)
        client._enqueue_session_input("s1", FRAME_INPUT + b"\r", ws)

        await _wait_until(lambda: mock_session.write.await_count >= 1)
        ordered = [
            name
            for name, _args, _kwargs in mock_session.mock_calls
            if name in ("write", "write_submit")
        ]
        assert ordered == ["write_submit", "write"], (
            "raw input must not overtake a queued submit"
        )
        mock_session.write_submit.assert_awaited_once_with(b"echo hi")
        mock_session.write.assert_awaited_once_with(b"\r")

        pump = client._input_pumps["s1"]
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    async def test_slow_submit_does_not_block_other_sessions(
        self, client, mock_session_manager
    ):
        release = asyncio.Event()
        slow_session = AsyncMock()
        slow_session.session_id = "slow"
        slow_session.state = SessionState.RUNNING
        slow_session.pid = 100
        slow_session.get_ring = MagicMock(return_value=b"")

        async def _blocked_submit(_data):
            await release.wait()

        slow_session.write_submit = AsyncMock(side_effect=_blocked_submit)

        fast_session = AsyncMock()
        fast_session.session_id = "fast"
        fast_session.state = SessionState.RUNNING
        fast_session.pid = 101
        fast_session.get_ring = MagicMock(return_value=b"")

        sessions = {"slow": slow_session, "fast": fast_session}
        mock_session_manager.get_session = AsyncMock(
            side_effect=lambda sid: sessions.get(sid)
        )

        ws = AsyncMock()
        client._enqueue_session_input("slow", FRAME_SUBMIT + b"sleepy", ws)
        await _wait_until(lambda: slow_session.write_submit.await_count == 1)

        # While the slow session's pump is parked inside write_submit, a
        # frame for another session must still be dispatched.
        client._enqueue_session_input("fast", FRAME_INPUT + b"ls", ws)
        await _wait_until(lambda: fast_session.write.await_count == 1)
        assert not release.is_set()

        release.set()
        for pump in list(client._input_pumps.values()):
            pump.cancel()
        await asyncio.gather(
            *client._input_pumps.values(), return_exceptions=True
        )

    async def test_full_queue_drops_frame(self, client, mock_session_manager):
        self._make_session(mock_session_manager)
        ws = AsyncMock()

        # Pre-seed a full queue with a parked pump so put_nowait overflows.
        client._input_queues["s1"] = asyncio.Queue(maxsize=1)
        parked = asyncio.create_task(asyncio.sleep(999))
        client._input_pumps["s1"] = parked
        client._input_queues["s1"].put_nowait(FRAME_INPUT + b"a")

        client._enqueue_session_input("s1", FRAME_INPUT + b"b", ws)

        assert client._input_queues["s1"].qsize() == 1, (
            "overflow frame must be dropped, not block the tunnel reader"
        )
        parked.cancel()
        await asyncio.gather(parked, return_exceptions=True)

    async def test_pump_survives_handler_exception(
        self, client, mock_session_manager
    ):
        mock_session = self._make_session(mock_session_manager)
        mock_session.write = AsyncMock(
            side_effect=[RuntimeError("pty died"), None]
        )
        ws = AsyncMock()

        client._enqueue_session_input("s1", FRAME_INPUT + b"boom", ws)
        client._enqueue_session_input("s1", FRAME_INPUT + b"ok", ws)

        await _wait_until(lambda: mock_session.write.await_count == 2)
        pump = client._input_pumps["s1"]
        assert not pump.done(), (
            "a failing frame must not kill the session's input pump"
        )
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    async def test_cancelled_pump_self_cleans_registries(
        self, client, mock_session_manager
    ):
        self._make_session(mock_session_manager)
        ws = AsyncMock()

        client._enqueue_session_input("s1", FRAME_INPUT + b"hi", ws)
        pump = client._input_pumps["s1"]
        # Yield to the event loop so the pump task starts and enters its
        # ``try/while`` body before we cancel it — a task cancelled before
        # it has ever been scheduled does not execute its coroutine at all
        # (including ``finally``), so the self-clean assertion would be
        # vacuously wrong without this yield.
        await asyncio.sleep(0)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

        assert "s1" not in client._input_pumps
        assert "s1" not in client._input_queues

    async def test_cancelled_pump_does_not_evict_successor_registration(
        self, client, mock_session_manager
    ):
        """Sweep race: a late frame can recreate the pump between the
        removed-session sweep's cancel+pop and the old pump's unwind; the
        old pump's finally must not deregister the successor."""
        self._make_session(mock_session_manager)
        ws = AsyncMock()

        client._enqueue_session_input("s1", FRAME_INPUT + b"hi", ws)
        old_pump = client._input_pumps["s1"]
        await asyncio.sleep(0)  # let the pump start so its finally will run

        # Mirror the _watch_session_changes sweep: cancel + eager pop...
        old_pump.cancel()
        client._input_pumps.pop("s1", None)
        client._input_queues.pop("s1", None)
        # ...then a late frame recreates queue+pump before the unwind runs
        # (enqueue is synchronous, so this happens before any loop turn).
        client._enqueue_session_input("s1", FRAME_INPUT + b"late", ws)
        successor = client._input_pumps["s1"]
        successor_queue = client._input_queues["s1"]

        await asyncio.gather(old_pump, return_exceptions=True)

        assert client._input_pumps.get("s1") is successor, (
            "old pump's unwind must not evict the successor pump"
        )
        assert client._input_queues.get("s1") is successor_queue, (
            "old pump's unwind must not evict the successor queue"
        )
        successor.cancel()
        await asyncio.gather(successor, return_exceptions=True)

    async def test_handle_binary_returns_while_submit_still_pending(
        self, client, mock_session_manager
    ):
        release = asyncio.Event()
        mock_session = self._make_session(mock_session_manager)

        async def _blocked_submit(_data):
            await release.wait()

        mock_session.write_submit = AsyncMock(side_effect=_blocked_submit)
        ws = AsyncMock()

        envelope = encode(CH_SESSION_IO, "s1", FRAME_SUBMIT + b"echo hi")
        # An inline await of the parked submit would hang here and trip
        # the timeout: the tunnel reader must hand off to the pump.
        await asyncio.wait_for(client._handle_binary(envelope, ws), timeout=1.0)

        await _wait_until(lambda: mock_session.write_submit.await_count == 1)
        assert not release.is_set()

        release.set()
        pump = client._input_pumps["s1"]
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)


class TestWatchSessionChangesInputPumps:
    async def test_removed_session_sweep_cancels_pump_and_clears_registries(
        self, client, mock_event_bus
    ):
        ws = AsyncMock()
        # Parked stand-in tasks: their finally blocks never run, so the
        # registry pops asserted below can only come from the sweep itself.
        gone_pump = asyncio.create_task(asyncio.sleep(999))
        kept_pump = asyncio.create_task(asyncio.sleep(999))
        client._input_pumps.update({"gone": gone_pump, "kept": kept_pump})
        client._input_queues.update(
            {"gone": asyncio.Queue(), "kept": asyncio.Queue()}
        )

        watcher = asyncio.create_task(client._watch_session_changes(ws))
        events = mock_event_bus.subscribe.return_value
        events.put_nowait(
            AgentEvent(
                type="sessions.changed",
                payload={"sessions": [{"session_id": "kept"}]},
            )
        )

        await _wait_until(lambda: "gone" not in client._input_pumps)
        assert "gone" not in client._input_queues
        await asyncio.gather(gone_pump, return_exceptions=True)
        assert gone_pump.cancelled()

        assert client._input_pumps.get("kept") is kept_pump
        assert "kept" in client._input_queues
        assert not kept_pump.cancelled()

        for task in (watcher, kept_pump):
            task.cancel()
        await asyncio.gather(watcher, kept_pump, return_exceptions=True)


def _attention_session_wire(**overrides) -> dict:
    session = {
        "session_id": "s1",
        "state": "running",
        "created_at": "2026-07-09T10:00:00Z",
        "pid": 123,
        "command": "claude",
        "cwd": "/tmp/proj",
        "title": "Fix the bug",
        "project_name": "proj",
        "kind": "claude",
        "kind_label": None,
        "attention": "awaiting_permission",
        "exit_reason": None,
    }
    session.update(overrides)
    return session


class TestForwardEvents:
    async def _run_forward(self, client, mock_event_bus, event: AgentEvent):
        ws = AsyncMock()
        fwd = asyncio.create_task(client._forward_events(ws))
        mock_event_bus.subscribe.return_value.put_nowait(event)
        await _wait_until(lambda: ws.send.await_count >= 1)
        # Give the loop one extra tick so a (wrong) second send would land.
        await asyncio.sleep(0)
        fwd.cancel()
        await asyncio.gather(fwd, return_exceptions=True)
        return ws

    async def test_attention_sends_events_frame_and_control_user_event(
        self, client, mock_event_bus
    ):
        session = _attention_session_wire()
        event = AgentEvent(
            type="session.attention", payload={"session": session}
        )
        ws = AsyncMock()
        fwd = asyncio.create_task(client._forward_events(ws))
        mock_event_bus.subscribe.return_value.put_nowait(event)
        await _wait_until(lambda: ws.send.await_count >= 2)
        fwd.cancel()
        await asyncio.gather(fwd, return_exceptions=True)

        ch1, sid1, pl1 = decode(ws.send.call_args_list[0][0][0])
        assert ch1 == CH_EVENTS
        assert sid1 == ""
        events_msg = json.loads(pl1)
        assert events_msg["type"] == "session.attention"
        assert events_msg["payload"]["session"] == session

        ch2, sid2, pl2 = decode(ws.send.call_args_list[1][0][0])
        assert ch2 == CH_CONTROL
        assert sid2 == ""
        ctrl = json.loads(pl2)
        assert ctrl == {
            "type": "user_event",
            "kind": "session.attention",
            "payload": {
                "session_id": "s1",
                "reason": "awaiting_permission",
                "title": "Fix the bug",
                "project_name": "proj",
                "kind": "claude",
            },
        }

    async def test_non_attention_event_sends_only_events_frame(
        self, client, mock_event_bus
    ):
        event = AgentEvent(
            type="session.updated",
            payload={"session": _attention_session_wire()},
        )
        ws = await self._run_forward(client, mock_event_bus, event)

        assert ws.send.await_count == 1
        ch, _, _ = decode(ws.send.call_args_list[0][0][0])
        assert ch == CH_EVENTS


class TestAttentionUserEvent:
    def test_extracts_wire_fields_and_drops_null_optionals(self):
        session = _attention_session_wire(
            attention="ended", exit_reason="exit code 0", title=None
        )
        ctrl = _attention_user_event({"session": session})

        assert ctrl == {
            "type": "user_event",
            "kind": "session.attention",
            "payload": {
                "session_id": "s1",
                "reason": "ended",
                "project_name": "proj",
                "exit_reason": "exit code 0",
                "kind": "claude",
            },
        }

    def test_carries_a_custom_kind_and_its_label(self):
        # A push for a session with neither a title nor a project is named
        # after its kind ("My Linter session"), so the label must travel too.
        session = _attention_session_wire(
            attention="turn_failed",
            title=None,
            project_name=None,
            kind="custom_0123456789ab",
            kind_label="My Linter",
        )
        ctrl = _attention_user_event({"session": session})

        assert ctrl == {
            "type": "user_event",
            "kind": "session.attention",
            "payload": {
                "session_id": "s1",
                "reason": "turn_failed",
                "kind": "custom_0123456789ab",
                "kind_label": "My Linter",
            },
        }

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {"session": None},
            {"session": {"session_id": "", "attention": "turn_finished"}},
            {"session": {"session_id": "s1", "attention": None}},
            {"session": {"session_id": 7, "attention": "turn_finished"}},
        ],
    )
    def test_malformed_payload_returns_none(self, payload):
        assert _attention_user_event(payload) is None


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

        pump_task = asyncio.create_task(_forever())
        client._input_pumps["sess-1"] = pump_task
        client._input_queues["sess-1"] = asyncio.Queue()

        all_tasks = [
            client._heartbeat_task,
            client._event_fwd_task,
            client._sesslist_fwd_task,
            client._session_watch_task,
            bridge_task,
            pump_task,
        ]

        client._cancel_all_subtasks()

        assert len(client._bridges) == 0
        assert len(client._input_pumps) == 0
        assert len(client._input_queues) == 0

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

    async def test_malformed_json_text_is_ignored(self, client):
        ws = AsyncMock()
        await client._handle_text("not valid json", ws)
        ws.send.assert_not_called()


# -- start / stop lifecycle -----------------------------------------------------


class TestStartStop:
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
                "model": "opus",
                "effort": "xhigh",
                "fast_mode": True,
                "ultracode": False,
                "append_system_prompt": "be terse",
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
        assert opts.model == "opus"
        assert opts.effort == "xhigh"
        assert opts.fast_mode is True
        assert opts.ultracode is False
        assert opts.append_system_prompt == "be terse"

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
                "model": "sonnet",
                "effort": "low",
                "fast_mode": False,
                "ultracode": True,
                "append_system_prompt": "relay note",
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
        assert opts.model == "sonnet"
        assert opts.effort == "low"
        assert opts.fast_mode is False
        assert opts.ultracode is True
        assert opts.append_system_prompt == "relay note"

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


class TestHandleCreateSessionAdHocPathValidation:
    """When a control-channel ``create_session`` frame arrives with
    ``project_name=None`` and a user-supplied ``cwd``, the relay runs
    the same pre-validation as the FastAPI router. Invalid paths
    short-circuit with ``session_create_failed`` instead of reaching
    ``SessionManager.create_session`` (which would otherwise surface a
    ``FileNotFoundError`` mapped through ``str(exc)`` and leak doubled
    backslashes onto the wire).
    """

    async def test_nonexistent_cwd_returns_session_create_failed(
        self, client, mock_session_manager, tmp_path,
    ):
        missing = tmp_path / "no-such"
        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "adhoc-missing",
            "cwd": str(missing),
        }
        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert "does not exist" in resp["error"]
        assert str(missing) in resp["error"]
        mock_session_manager.create_session.assert_not_awaited()

    async def test_validation_skipped_when_project_name_set(
        self, client, mock_session_manager,
    ):
        """``project_name`` set ⇒ server derives cwd from the project;
        the ad-hoc validator must not fire. A relative ``cwd`` here must
        NOT short-circuit — it is simply ignored, and the request flows
        through to ``create_session`` normally (which the mock accepts)."""
        mock_session = MagicMock()
        mock_session.session_id = "ok-sess"
        mock_session.pid = 1234
        mock_session_manager.create_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "ok-sess",
            "project_name": "proj-x",
            "cwd": "relative/should/be/ignored",
        }
        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        # Validation was skipped — create_session was called.
        mock_session_manager.create_session.assert_awaited_once()
        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"

    async def test_valid_adhoc_cwd_creates_session(
        self, client, mock_session_manager, tmp_path,
    ):
        """Sanity: a real absolute directory passes validation and
        reaches ``create_session`` — Claude mode (with ``claude_options``)
        in an ad-hoc directory is the happy path."""
        mock_session = MagicMock()
        mock_session.session_id = "adhoc-ok"
        mock_session.pid = 4242
        mock_session_manager.create_session = AsyncMock(return_value=mock_session)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "adhoc-ok",
            "cwd": str(tmp_path),
            "claude_options": {},
        }
        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        mock_session_manager.create_session.assert_awaited_once()
        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"
        assert resp["session_id"] == "adhoc-ok"


class TestDispatchProxyAdHocPathValidation:
    """Same validation applies on the relay's proxy path
    (``POST /api/sessions``). Mirrors the router's behavior: invalid
    custom paths surface as ``{"status": 400, "body": {"detail": ...}}``
    before ``SessionManager.create_session`` is reached.
    """

    async def test_relative_cwd_returns_400(
        self, client, mock_session_manager,
    ):
        mock_session_manager.create_session = AsyncMock()
        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/sessions", {"cwd": "relative/path"}, None, ws,
        )
        assert result["status"] == 400
        assert "absolute" in result["body"]["detail"]
        assert "relative/path" in result["body"]["detail"]
        mock_session_manager.create_session.assert_not_awaited()

    async def test_validation_skipped_when_project_name_set(
        self, client, mock_session_manager,
    ):
        """A bad ``cwd`` accompanied by a ``project_name`` must NOT
        trigger the 400 — the server derives cwd from the project. Pins
        the same invariant tested on the router side."""
        from mobaide.core.models import SessionState
        from datetime import datetime, timezone

        fake_info = MagicMock()
        fake_info.session_id = "sid"
        fake_info.state = SessionState.RUNNING
        fake_info.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fake_info.pid = 7
        fake_info.command = ["/bin/bash"]
        fake_info.cwd = "/some/project/path"

        fake_session = MagicMock()
        fake_session.to_info.return_value = fake_info
        mock_session_manager.create_session = AsyncMock(return_value=fake_session)

        ws = AsyncMock()
        body = {
            "project_name": "proj",
            "cwd": "should/be/ignored/because/relative",
        }
        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy(
                "POST", "/api/sessions", body, None, ws,
            )

        assert result["status"] == 201
        mock_session_manager.create_session.assert_awaited_once()

    async def test_valid_adhoc_cwd_returns_201(
        self, client, mock_session_manager, tmp_path,
    ):
        """Happy path: ``project_name=None`` + valid absolute cwd +
        ``claude_options`` → 201 via the ad-hoc Claude launch path."""
        from mobaide.core.models import SessionState
        from datetime import datetime, timezone

        fake_info = MagicMock()
        fake_info.session_id = "adhoc-sid"
        fake_info.state = SessionState.RUNNING
        fake_info.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fake_info.pid = 9
        fake_info.command = ["claude"]
        fake_info.cwd = str(tmp_path)

        fake_session = MagicMock()
        fake_session.to_info.return_value = fake_info
        mock_session_manager.create_session = AsyncMock(return_value=fake_session)

        ws = AsyncMock()
        body = {"cwd": str(tmp_path), "claude_options": {}}
        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy(
                "POST", "/api/sessions", body, None, ws,
            )

        assert result["status"] == 201
        assert result["body"]["session_id"] == "adhoc-sid"
        mock_session_manager.create_session.assert_awaited_once()


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


class TestProxyWorktreeBases:
    """``GET /api/projects/{name}/git/worktree-bases`` through the relay:
    same body as the FastAPI router, with the store's last-used base."""

    async def test_get_worktree_bases(self, client, mock_app_state, tmp_path):
        from mobaide.agent.git.models import WorktreeBaseRef, WorktreeBasesResponse

        (tmp_path / ".git").mkdir()
        project = MagicMock()
        project.name = "proj"
        project.path = str(tmp_path)
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.projects_store.get_last_worktree_base = MagicMock(
            return_value="origin/dev",
        )
        mock_app_state.worktree_manager = MagicMock()
        resp = WorktreeBasesResponse(
            default="origin/main",
            last_used="origin/dev",
            refs=[WorktreeBaseRef(ref="origin/main", kind="remote")],
        )
        with patch(
            "mobaide.relay.client.list_worktree_bases",
            new=AsyncMock(return_value=resp),
        ) as mock_list:
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/git/worktree-bases", None, None, AsyncMock(),
            )

        assert result == {"status": 200, "body": resp.model_dump()}
        assert mock_list.await_args.kwargs["last_used"] == "origin/dev"


class TestProxyGitBranches:
    """Relay parity for the project-git routes added next to
    ``TestProxyGitItems``: the ``?remote`` flag reaches the service parsed,
    ``POST /branches`` and ``POST /checkout`` answer the FastAPI router's
    bodies byte-for-byte — the busy 409 nested under ``detail`` and the coded
    refusals in the session-git ``{detail: {code, message}}`` shape."""

    def _wire_project(self, mock_app_state, tmp_path, name="proj"):
        (tmp_path / ".git").mkdir(exist_ok=True)
        project = MagicMock()
        project.name = name
        project.path = str(tmp_path)
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=project)
        mock_app_state.worktree_manager = MagicMock()
        mock_app_state.session_manager = MagicMock()
        mock_app_state.event_bus = MagicMock()

    @pytest.mark.parametrize(
        ("query", "expected"),
        [(None, False), ({"remote": "true"}, True), ({"remote": "0"}, False)],
    )
    async def test_get_items_forwards_the_parsed_remote_flag(
        self, client, mock_app_state, tmp_path, query, expected,
    ):
        self._wire_project(mock_app_state, tmp_path)
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.list_git_items",
            new=AsyncMock(return_value=[]),
        ) as mock_list:
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/git/items", None, query, ws,
            )

        assert result["status"] == 200
        assert result["body"] == {"items": []}
        assert mock_list.await_args is not None
        assert mock_list.await_args.kwargs["remote"] is expected

    async def test_get_items_rejects_a_non_boolean_remote_flag_with_422(
        self, client, mock_app_state, tmp_path,
    ):
        self._wire_project(mock_app_state, tmp_path)
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.list_git_items",
            new=AsyncMock(return_value=[]),
        ) as mock_list:
            result = await client._dispatch_proxy(
                "GET", "/api/projects/proj/git/items", None, {"remote": "maybe"}, ws,
            )

        assert result["status"] == 422
        assert isinstance(result["body"]["detail"], list)
        mock_list.assert_not_awaited()

    async def test_post_branches_happy_path(self, client, mock_app_state, tmp_path):
        from mobaide.agent.git.models import GitBranchCreateRequest, GitBranchResponse

        self._wire_project(mock_app_state, tmp_path)
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.create_branch",
            new=AsyncMock(
                return_value=GitBranchResponse(branch="x", checked_out=True, created=True),
            ),
        ) as mock_create:
            result = await client._dispatch_proxy(
                "POST",
                "/api/projects/proj/git/branches",
                {"name": "x", "start_point": "origin/main", "checkout": True},
                None,
                ws,
            )

        assert result["status"] == 200
        assert result["body"] == {"branch": "x", "checked_out": True, "created": True}
        mock_create.assert_awaited_once()
        await_args = mock_create.await_args
        assert await_args is not None
        assert await_args.args == (
            GitBranchCreateRequest(name="x", start_point="origin/main", checkout=True),
        )
        assert await_args.kwargs["project_name"] == "proj"
        assert await_args.kwargs["project_root"] == str(tmp_path)
        assert await_args.kwargs["actor"] is None

    async def test_post_branches_malformed_returns_422(self, client, mock_app_state, tmp_path):
        self._wire_project(mock_app_state, tmp_path)
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.create_branch", new=AsyncMock(),
        ) as mock_create:
            result = await client._dispatch_proxy(
                "POST", "/api/projects/proj/git/branches", {"checkout": True}, None, ws,
            )

        assert result["status"] == 422
        assert isinstance(result["body"]["detail"], list)
        mock_create.assert_not_awaited()

    async def test_post_checkout_happy_path(self, client, mock_app_state, tmp_path):
        from mobaide.agent.git.models import GitBranchResponse, GitCheckoutRequest

        self._wire_project(mock_app_state, tmp_path)
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.checkout_ref",
            new=AsyncMock(
                return_value=GitBranchResponse(
                    branch="feature/x", checked_out=True, created=True,
                ),
            ),
        ) as mock_checkout:
            result = await client._dispatch_proxy(
                "POST",
                "/api/projects/proj/git/checkout",
                {"ref": "origin/feature/x"},
                None,
                ws,
            )

        assert result["status"] == 200
        assert result["body"] == {"branch": "feature/x", "checked_out": True, "created": True}
        await_args = mock_checkout.await_args
        assert await_args is not None
        assert await_args.args == (GitCheckoutRequest(ref="origin/feature/x"),)
        assert await_args.kwargs["project_name"] == "proj"

    async def test_post_checkout_busy_returns_409_with_nested_detail(
        self, client, mock_app_state, tmp_path,
    ):
        from mobaide.agent.git.models import BusyHolder
        from mobaide.agent.git.service import GitItemBusyError

        self._wire_project(mock_app_state, tmp_path)
        busy_by = [BusyHolder(kind="session", session_id="s1", cwd=str(tmp_path))]
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.checkout_ref",
            new=AsyncMock(side_effect=GitItemBusyError(busy_by=busy_by)),
        ):
            result = await client._dispatch_proxy(
                "POST", "/api/projects/proj/git/checkout", {"ref": "x"}, None, ws,
            )

        assert result["status"] == 409
        assert result["body"]["detail"]["busy_by"][0]["session_id"] == "s1"
        assert "code" not in result["body"]["detail"]
        assert "busy_by" not in result["body"]

    @pytest.mark.parametrize(
        ("exc", "status", "detail"),
        [
            (
                GitRefusedError("dirty", "error: Your local changes"),
                409,
                {"code": "dirty", "message": "error: Your local changes"},
            ),
            (GitNotFoundError("ref_not_found"), 404, {"code": "ref_not_found"}),
            (GitBadRequestError("bad_ref"), 400, {"code": "bad_ref"}),
        ],
    )
    async def test_post_checkout_coded_refusals_match_the_router_shape(
        self, client, mock_app_state, tmp_path, exc, status, detail,
    ):
        self._wire_project(mock_app_state, tmp_path)
        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.checkout_ref", new=AsyncMock(side_effect=exc),
        ):
            result = await client._dispatch_proxy(
                "POST", "/api/projects/proj/git/checkout", {"ref": "x"}, None, ws,
            )

        assert result["status"] == status
        assert result["body"] == {"detail": detail}

    async def test_unknown_project_and_non_git_project_apply_to_the_new_routes(
        self, client, mock_app_state, tmp_path,
    ):
        mock_app_state.projects_store = MagicMock()
        mock_app_state.projects_store.get = MagicMock(return_value=None)
        mock_app_state.worktree_manager = MagicMock()
        ws = AsyncMock()

        missing = await client._dispatch_proxy(
            "POST", "/api/projects/missing/git/checkout", {"ref": "x"}, None, ws,
        )

        assert missing["status"] == 404
        assert "not found" in missing["body"]["detail"]

        project = MagicMock()
        project.name = "plain"
        project.path = str(tmp_path / "plain")
        (tmp_path / "plain").mkdir()
        mock_app_state.projects_store.get = MagicMock(return_value=project)

        plain = await client._dispatch_proxy(
            "POST", "/api/projects/plain/git/branches", {"name": "x"}, None, ws,
        )

        assert plain["status"] == 400
        assert "not a git repository" in plain["body"]["detail"]


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
        # Canonical 12-key shape, in any order — locks down session_to_wire_dict.
        expected_keys = {
            "session_id", "state", "created_at", "pid",
            "command", "cwd", "title", "project_name", "kind",
            "kind_label", "attention", "exit_reason", "ui", "activity",
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

        # Canonical 12-key shape — the consolidation contract.
        assert set(body.keys()) == {
            "session_id", "state", "created_at", "pid",
            "command", "cwd", "title", "project_name", "kind",
            "kind_label", "attention", "exit_reason", "ui", "activity",
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


class TestConnectLoopStabilityBackoff:
    """Stability-gated backoff with decorrelated jitter in ``_connect_loop``.

    The backoff state must reset to the base delay only after a connection
    survives ``reconnect_stability_threshold`` seconds — regardless of how
    it ended — and every non-protocol-incompatible sleep is drawn from
    ``uniform(base, delay * 3)`` capped at ``reconnect_max_delay``.
    Fixture delays: base=0.01, max=0.05, stability threshold default 60.0.
    """

    @staticmethod
    def _wire(client, monkeypatch, *, monotonic_values, stop_after, outcome):
        """Patch _run_connection/time/random/sleep; return (sleeps, uniform)."""
        async def run_connection():
            if outcome == "raise":
                raise ConnectionError("boom")

        monkeypatch.setattr(client, "_run_connection", run_connection)
        monkeypatch.setattr(
            "mobaide.relay.client.time",
            MagicMock(monotonic=MagicMock(side_effect=monotonic_values)),
        )
        uniform = MagicMock(side_effect=lambda a, b: b)  # upper bound
        monkeypatch.setattr(
            "mobaide.relay.client.random", MagicMock(uniform=uniform),
        )
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)
            if len(sleeps) >= stop_after:
                client._stopping = True

        monkeypatch.setattr("mobaide.relay.client.asyncio.sleep", fake_sleep)
        return sleeps, uniform

    async def test_stable_connection_resets_backoff(self, client, monkeypatch):
        # Two flaps escalate the delay, then a 100s-uptime connection drops:
        # the third jitter draw must start from base again (upper bound
        # base*3, not prev*3) — this is the de-escalation the old
        # reset-on-clean-return logic never performed for exceptions.
        sleeps, uniform = self._wire(
            client, monkeypatch,
            monotonic_values=[0, 1, 10, 11, 100, 200],
            stop_after=3,
            outcome="raise",
        )

        await client._connect_loop()

        base = client._config.reconnect_base_delay
        assert [c.args for c in uniform.call_args_list] == [
            pytest.approx((base, base * 3)),
            pytest.approx((base, base * 3 * 3)),
            pytest.approx((base, base * 3)),  # reset: stable uptime ≥ 60s
        ]
        assert sleeps == pytest.approx([0.03, 0.05, 0.03])

    async def test_clean_instant_return_does_not_reset(self, client, monkeypatch):
        # The old loop reset the delay on ANY clean return — recreating
        # thundering herds when the server accepts and instantly closes.
        # A clean return below the stability threshold must keep escalating.
        sleeps, _ = self._wire(
            client, monkeypatch,
            monotonic_values=[0, 1, 10, 11],
            stop_after=2,
            outcome="return",
        )

        await client._connect_loop()

        assert sleeps == pytest.approx([0.03, 0.05])
        assert sleeps[1] != client._config.reconnect_base_delay

    async def test_jitter_bounds_with_real_random(self, client, monkeypatch):
        # With the real ``random`` module every sleep must stay within
        # [base, max]: uniform(base, ·) bounds it below, the min() caps it.
        import itertools

        async def run_connection():
            raise ConnectionError("boom")

        monkeypatch.setattr(client, "_run_connection", run_connection)
        monkeypatch.setattr(
            "mobaide.relay.client.time",
            MagicMock(monotonic=MagicMock(side_effect=itertools.count(0, 1))),
        )
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)
            if len(sleeps) >= 6:
                client._stopping = True

        monkeypatch.setattr("mobaide.relay.client.asyncio.sleep", fake_sleep)

        await client._connect_loop()

        base = client._config.reconnect_base_delay
        max_delay = client._config.reconnect_max_delay
        assert len(sleeps) == 6
        assert all(base <= s <= max_delay for s in sleeps)


class TestHandleCreateSessionCodex:
    """The control-channel ``create_session`` path (used when a central relay
    drives the agent directly, not via the HTTP proxy) must support the second
    managed CLI: parse ``codex_options``, thread the ``cli`` discriminator into
    a CODEX ``SessionConfig``, and map a missing Codex binary to its own
    ``codex_cli_missing`` marker — the Claude-only coverage in
    ``TestHandleControl`` / ``TestHandleCreateSessionPathErrors`` never exercises
    the Codex branch.
    """

    async def test_create_session_forwards_codex_options_and_kind(
        self, client, mock_session_manager,
    ):
        """An explicit ``cli="codex"`` plus a ``codex_options`` block must reach
        ``create_session`` as a CODEX ``SessionConfig`` carrying the parsed
        :class:`CodexOptions` (and no ``claude_options``). Otherwise the sandbox
        / model selection is silently dropped on the relay control path."""
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionConfig

        async def _capture(config: SessionConfig):
            _capture.config = config  # type: ignore[attr-defined]
            s = MagicMock()
            s.session_id = "codex-sess"
            s.pid = 7
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "codex-sess",
            "cli": "codex",
            "codex_options": {
                "model": "gpt-5.4-codex",
                "sandbox": "read-only",
            },
        }
        payload = json.dumps(ctrl).encode()

        with patch.object(client, "_bridge_session"):
            await client._handle_control(payload, ws)

        cfg: SessionConfig = _capture.config  # type: ignore[attr-defined]
        assert cfg.kind is SessionKind.CODEX
        assert cfg.claude_options is None
        assert isinstance(cfg.codex_options, CodexOptions)
        assert cfg.codex_options.model == "gpt-5.4-codex"
        assert cfg.codex_options.sandbox == "read-only"

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"
        assert resp["session_id"] == "codex-sess"
        assert resp["pid"] == 7

    async def test_codex_options_without_cli_still_resolves_codex_kind(
        self, client, mock_session_manager,
    ):
        """Back-compat inference: a payload that carries ``codex_options`` but
        omits the ``cli`` discriminator must still resolve to a CODEX session
        (mirrors how a ``claude_options``-only payload resolves to Claude)."""
        from mobaide.core.models import SessionConfig

        async def _capture(config: SessionConfig):
            _capture.config = config  # type: ignore[attr-defined]
            s = MagicMock()
            s.session_id = "infer-sess"
            s.pid = 8
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "infer-sess",
            "codex_options": {"sandbox": "workspace-write"},
        }

        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        cfg: SessionConfig = _capture.config  # type: ignore[attr-defined]
        assert cfg.kind is SessionKind.CODEX
        assert cfg.codex_options is not None
        assert cfg.codex_options.sandbox == "workspace-write"

    async def test_invalid_codex_options_returns_create_failed(
        self, client, mock_session_manager,
    ):
        """An out-of-range enum value in ``codex_options`` is rejected at the
        ``CodexOptionsModel`` boundary, producing ``session_create_failed`` with
        an ``Invalid codex_options:`` error — and ``create_session`` is never
        reached."""
        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "bad-codex",
            "cli": "codex",
            "codex_options": {"sandbox": "nope"},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "bad-codex"
        assert resp["error"].startswith("Invalid codex_options:")
        mock_session_manager.create_session.assert_not_awaited()

    async def test_codex_cli_missing_emits_codex_cli_missing_discriminator(
        self, client, mock_session_manager,
    ):
        """``CodexBinaryNotFoundError`` from the spawn path must map to
        ``session_create_failed`` with ``codex_cli_missing: true`` (NOT
        ``claude_cli_missing``) and the Codex-specific install hint, so the
        mobile client renders an "install Codex CLI" affordance. The marker key
        is derived from ``exc.cli_name`` — this proves the Codex subclass routes
        through the shared ``except CliBinaryNotFoundError`` branch correctly."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=CodexBinaryNotFoundError(
                searched_paths=["PATH:codex", "/home/test/.npm-global/bin/codex"]
            ),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "no-codex-sess",
            "cli": "codex",
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "no-codex-sess"
        assert resp["codex_cli_missing"] is True
        assert "claude_cli_missing" not in resp
        assert resp["error"].startswith("Codex CLI not found:")
        assert "npm install -g @openai/codex" in resp["error"]
        assert "MOBAIDE_CODEX_BIN" in resp["error"]
        assert resp["searched_paths"] == [
            "PATH:codex",
            "/home/test/.npm-global/bin/codex",
        ]


class TestHandleCreateSessionChat:
    """The control-channel ``create_session`` checks ``ui`` and
    ``chat_resume_id`` like the proxied ``POST /api/sessions`` does, before
    anything is spawned: ``chat_resume_id`` names a stored session that the
    agent opens with ``session/load``."""

    @pytest.mark.parametrize(
        ("fields", "error"),
        [
            ({"cli": "claude", "chat_resume_id": "abc"}, "chat_resume_id needs ui=chat"),
            ({"cli": "codex", "ui": "chat"}, "The chat view is not available for codex sessions"),
            ({"cli": "claude", "ui": "chat", "chat_resume_id": "../../etc/passwd"}, "Invalid chat_resume_id"),
            ({"cli": "claude", "ui": "chat", "chat_resume_id": "a" * 201}, "Invalid chat_resume_id"),
        ],
        ids=["resume-needs-chat", "no-chat-agent", "path", "too-long"],
    )
    async def test_invalid_chat_fields_are_refused(
        self, client, mock_session_manager, fields, error,
    ):
        mock_session_manager.chat_agents = {"claude": object()}
        mock_session_manager.create_session = AsyncMock()
        ws = AsyncMock()
        ctrl = {"type": "create_session", "session_id": "chat-sess", **fields}

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp == {"type": "session_create_failed", "session_id": "chat-sess", "error": error}
        mock_session_manager.create_session.assert_not_called()

    async def test_chat_fields_reach_the_session_config(self, client, mock_session_manager):
        captured = []

        async def _capture(config):
            captured.append(config)
            s = MagicMock()
            s.session_id = "chat-sess"
            s.pid = 7
            return s

        mock_session_manager.chat_agents = {"claude": object()}
        mock_session_manager.create_session = AsyncMock(side_effect=_capture)
        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "chat-sess",
            "cli": "claude",
            "ui": "chat",
            "chat_resume_id": "0b4e7c2a-11f0:main.v2",
        }

        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        assert [(c.ui, c.chat_resume_id) for c in captured] == [("chat", "0b4e7c2a-11f0:main.v2")]
        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"


class TestHandleProxyRequestCodex:
    """``POST /api/sessions`` proxied through the central relay
    (``proxy_request`` control frame) must thread Codex the same way the direct
    HTTP router does: parse ``codex_options`` into a CODEX session and map a
    missing Codex binary to status 503 with a ``codex_cli_missing`` body marker.
    The pre-existing proxy coverage in ``TestHandleProxyRequestPathErrors`` is
    Claude-only.
    """

    async def test_proxy_post_sessions_threads_codex_options(
        self, client, mock_session_manager,
    ):
        """A proxied ``POST /api/sessions`` body with ``cli="codex"`` builds a
        CODEX ``SessionConfig`` from ``codex_options`` and returns a 201
        ``proxy_response`` whose body reports ``kind == "codex"``."""
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionConfig

        captured: dict[str, SessionConfig] = {}

        async def _capture(config: SessionConfig):
            captured["config"] = config
            s = MagicMock()
            s.session_id = "proxy-codex"
            s.pid = 11
            info = MagicMock()
            info.session_id = "proxy-codex"
            info.state = SessionState.RUNNING
            info.created_at = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
            info.pid = 11
            info.command = ["codex"]
            info.cwd = None
            info.title = None
            info.project_name = None
            info.kind = SessionKind.CODEX
            info.kind_label = None
            info.attention = None
            info.exit_reason = None
            info.ui = "terminal"
            info.activity = None
            s.to_info = MagicMock(return_value=info)
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-codex",
            "method": "POST",
            "path": "/api/sessions",
            "body": {
                "cli": "codex",
                "codex_options": {"model": "gpt-5.4-codex", "sandbox": "read-only"},
            },
        }

        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        cfg = captured["config"]
        assert cfg.kind is SessionKind.CODEX
        assert cfg.claude_options is None
        assert isinstance(cfg.codex_options, CodexOptions)
        assert cfg.codex_options.model == "gpt-5.4-codex"
        assert cfg.codex_options.sandbox == "read-only"

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-codex"
        assert resp["status"] == 201
        assert resp["body"]["kind"] == "codex"

    async def test_proxy_codex_cli_missing_returns_503_with_discriminator(
        self, client, mock_session_manager,
    ):
        """A missing Codex binary on the proxy path must map to status=503
        (not 400 / not 500) with ``codex_cli_missing: true`` in the body and the
        Codex install hint — the marker key derives from ``exc.cli_name`` so the
        Codex subclass and Claude share one 503 handler."""
        mock_session_manager.create_session = AsyncMock(
            side_effect=CodexBinaryNotFoundError(
                searched_paths=["PATH:codex", "/usr/local/bin/codex"]
            ),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-codex-missing",
            "method": "POST",
            "path": "/api/sessions",
            "body": {"cli": "codex", "codex_options": {}},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-codex-missing"
        assert resp["status"] == 503
        assert resp["body"]["codex_cli_missing"] is True
        assert "claude_cli_missing" not in resp["body"]
        assert resp["body"]["detail"].startswith("Codex CLI not found:")
        assert "npm install -g @openai/codex" in resp["body"]["detail"]
        assert resp["body"]["searched_paths"] == [
            "PATH:codex",
            "/usr/local/bin/codex",
        ]


class TestProxyVersionCapabilities:
    """The relay's ``GET /api/version`` branch must report per-CLI availability
    in ``capabilities.cli`` using the *same* ``build_cli_capabilities`` builder
    the direct FastAPI handler uses — so a CLI grayed out in DIRECT mode is
    grayed out identically in RELAY mode. This is the ``TestProxyGitItems``
    "missing relay route" bug class applied to the additive ``capabilities``
    field: the two handlers are hand-maintained and must not drift.
    """

    def _launcher(
        self,
        resolves: bool,
        cached_models: tuple[str, ...] | None = None,
        cached_model_details: tuple[dict, ...] | None = None,
    ):
        launcher = MagicMock(name="launcher")
        # Explicit ``cached_models`` / ``cached_model_details`` (default None):
        # a bare MagicMock would auto-create truthy attributes and make
        # ``build_cli_capabilities`` emit spurious ``models`` /
        # ``model_details`` keys.
        launcher.cached_models = cached_models
        launcher.cached_model_details = cached_model_details
        if resolves:
            launcher.resolve_binary.return_value = "/bin/cli"
        else:
            launcher.resolve_binary.side_effect = ClaudeBinaryNotFoundError(
                searched_paths=["PATH:cli"],
            )
        return launcher

    async def test_version_marks_models_pending_while_warmup_outlasts_the_wait(
        self, client, mock_app_state, monkeypatch,
    ):
        """Relay mirror of the direct handler's ``models_pending`` marker: a
        warm task still running after the bounded wait is reported as pending
        on every enumeration-capable entry. Awaiter stubbed for speed."""
        from mobaide.relay import client as client_module

        async def _no_wait(*_a, **_kw):
            return None

        monkeypatch.setattr(client_module, "await_model_cache_warmup", _no_wait)
        launcher = self._launcher(resolves=True)
        launcher.list_models = AsyncMock()
        mock_app_state.cli_launchers = {"codex": launcher}
        mock_app_state.cli_models_warmup = asyncio.create_task(asyncio.sleep(60))
        ws = AsyncMock()

        try:
            result = await client._dispatch_proxy(
                "GET", "/api/version", None, None, ws,
            )
        finally:
            mock_app_state.cli_models_warmup.cancel()

        assert result["status"] == 200
        assert result["body"]["capabilities"]["cli"] == {
            "codex": {"available": True, "models_pending": True},
        }

    async def test_version_waits_for_a_pending_model_warmup(
        self, client, mock_app_state,
    ):
        """Relay mirror of the direct handler's mid-warmup wait: the warm task
        releases only after the proxy dispatch is already in flight, so a
        branch that did not await it would answer with the cold cache."""
        launcher = self._launcher(resolves=True)
        mock_app_state.cli_launchers = {"codex": launcher}
        release = asyncio.Event()

        async def _warm() -> None:
            await release.wait()
            launcher.cached_models = ("gpt-5.6-sol",)

        mock_app_state.cli_models_warmup = asyncio.create_task(_warm())
        ws = AsyncMock()

        request = asyncio.create_task(
            client._dispatch_proxy("GET", "/api/version", None, None, ws),
        )
        await asyncio.sleep(0.05)
        release.set()

        result = await request
        assert result["status"] == 200
        assert result["body"]["capabilities"]["cli"]["codex"]["models"] == [
            "gpt-5.6-sol",
        ]

    async def test_capabilities_equal_shared_builder_locks_direct_relay_parity(
        self, client, mock_app_state,
    ):
        """The relay body's ``capabilities.cli`` must be byte-for-byte what
        ``build_cli_capabilities`` produces from the same registry — the single
        assertion that pins DIRECT/RELAY parity. If the relay branch ever
        hand-rolls the shape instead of calling the shared builder, this fails.
        """
        from mobaide.core.cli_launcher import build_cli_capabilities
        from mobaide.voice.models import VOICE_CAPABILITIES

        mock_app_state.cli_launchers = {
            "claude": self._launcher(resolves=True),
            "codex": self._launcher(resolves=False),
            "antigravity": self._launcher(resolves=True),
        }
        ws = AsyncMock()

        result = await client._dispatch_proxy(
            "GET", "/api/version", None, None, ws,
        )

        expected = build_cli_capabilities(mock_app_state.cli_launchers)
        assert result["body"]["capabilities"] == {
            "cli": expected,
            "git": {
                "changes": True,
                "stage": True,
                "discard": True,
                "commit": True,
                "fetch": True,
                "pull": True,
                "push": True,
                "branches": True,
                "rebase": True,
                "resolve": True,
                "delete_remote": True,
                "conflicts": True,
                "worktree_base": True,
            },
            "voice": VOICE_CAPABILITIES,
            "fs": {"dirs": True, "windows_paths": sys.platform == "win32"},
            "chat": {},
        }

    async def test_version_body_keeps_core_fields(self, client, mock_app_state):
        """The added ``capabilities`` work must not drop the existing version
        fields the client relies on (name / server_version / protocol set)."""
        from mobaide import __version__
        from mobaide.relay.envelope import (
            MIN_CLIENT_PROTOCOL_VERSION,
            SUPPORTED_PROTOCOL_VERSIONS,
        )

        mock_app_state.cli_launchers = {"claude": self._launcher(resolves=True)}
        ws = AsyncMock()

        result = await client._dispatch_proxy(
            "GET", "/api/version", None, None, ws,
        )

        body = result["body"]
        assert body["name"] == "mobaide_agent"
        assert body["server_version"] == __version__
        assert body["protocol_versions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
        assert body["min_client_version"] == MIN_CLIENT_PROTOCOL_VERSION


class TestHandleCreateSessionAntigravity:
    """The control-channel ``create_session`` path (used when a central relay
    drives the agent directly, not via the HTTP proxy) must support the third
    managed CLI: parse ``antigravity_options``, thread the ``cli`` discriminator
    into an ANTIGRAVITY ``SessionConfig``, and map a missing ``agy`` binary to
    its own ``antigravity_cli_missing`` marker — the Claude/Codex-only coverage
    elsewhere never exercises the Antigravity branch. No auth case: ``agy`` has
    no pre-spawn auth probe, so ``antigravity_auth_required`` can never fire —
    an unauthenticated CLI renders its login prompt inside the PTY instead.
    """

    async def test_create_session_forwards_antigravity_options_and_kind(
        self, client, mock_session_manager,
    ):
        """An explicit ``cli="antigravity"`` plus an ``antigravity_options``
        block must reach ``create_session`` as an ANTIGRAVITY ``SessionConfig``
        carrying the parsed :class:`AntigravityOptions` (and no
        ``claude_options``). Otherwise the model / sandbox selection is silently
        dropped on the relay control path."""
        from mobaide.core.antigravity_launcher import AntigravityOptions
        from mobaide.core.models import SessionConfig

        async def _capture(config: SessionConfig):
            _capture.config = config  # type: ignore[attr-defined]
            s = MagicMock()
            s.session_id = "agy-sess"
            s.pid = 7
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "agy-sess",
            "cli": "antigravity",
            "antigravity_options": {
                "model": "Gemini 3.1 Pro (Low)",
                "sandbox": True,
            },
        }
        payload = json.dumps(ctrl).encode()

        with patch.object(client, "_bridge_session"):
            await client._handle_control(payload, ws)

        cfg: SessionConfig = _capture.config  # type: ignore[attr-defined]
        assert cfg.kind is SessionKind.ANTIGRAVITY
        assert cfg.claude_options is None
        assert cfg.codex_options is None
        assert isinstance(cfg.antigravity_options, AntigravityOptions)
        assert cfg.antigravity_options.model == "Gemini 3.1 Pro (Low)"
        assert cfg.antigravity_options.sandbox is True

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"
        assert resp["session_id"] == "agy-sess"
        assert resp["pid"] == 7


class TestHandleProxyRequestAntigravity:
    """``POST /api/sessions`` proxied through the central relay
    (``proxy_request`` control frame) must thread Antigravity the same way the
    direct HTTP router does: parse ``antigravity_options`` into an ANTIGRAVITY
    session and map a missing ``agy`` binary to status 503 with an
    ``antigravity_cli_missing`` body marker. No 409 auth case: ``agy`` has no
    pre-spawn auth probe, so the ``<cli>_auth_required`` path can never fire
    for Antigravity.
    """

    async def test_proxy_post_sessions_threads_antigravity_options(
        self, client, mock_session_manager,
    ):
        """A proxied ``POST /api/sessions`` body with ``cli="antigravity"``
        builds an ANTIGRAVITY ``SessionConfig`` from ``antigravity_options`` and
        returns a 201 ``proxy_response`` whose body reports
        ``kind == "antigravity"``."""
        from mobaide.core.antigravity_launcher import AntigravityOptions
        from mobaide.core.models import SessionConfig

        captured: dict[str, SessionConfig] = {}

        async def _capture(config: SessionConfig):
            captured["config"] = config
            s = MagicMock()
            s.session_id = "proxy-agy"
            s.pid = 11
            info = MagicMock()
            info.session_id = "proxy-agy"
            info.state = SessionState.RUNNING
            info.created_at = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
            info.pid = 11
            info.command = ["agy"]
            info.cwd = None
            info.title = None
            info.project_name = None
            info.kind = SessionKind.ANTIGRAVITY
            info.kind_label = None
            info.attention = None
            info.exit_reason = None
            info.ui = "terminal"
            info.activity = None
            s.to_info = MagicMock(return_value=info)
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-agy",
            "method": "POST",
            "path": "/api/sessions",
            "body": {
                "cli": "antigravity",
                "antigravity_options": {
                    "model": "Gemini 3.1 Pro (Low)",
                    "sandbox": True,
                },
            },
        }

        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        cfg = captured["config"]
        assert cfg.kind is SessionKind.ANTIGRAVITY
        assert cfg.claude_options is None
        assert isinstance(cfg.antigravity_options, AntigravityOptions)
        assert cfg.antigravity_options.model == "Gemini 3.1 Pro (Low)"
        assert cfg.antigravity_options.sandbox is True

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-agy"
        assert resp["status"] == 201
        assert resp["body"]["kind"] == "antigravity"


class TestHandleCreateSessionOpenCode:
    """The control-channel ``create_session`` path must support OpenCode:
    parse ``opencode_options``, thread the ``cli`` discriminator into an
    OPENCODE ``SessionConfig``, refuse a malformed block, and map a missing
    ``opencode`` binary to its own ``opencode_cli_missing`` marker. No auth
    case: OpenCode starts without an account.
    """

    async def test_create_session_forwards_opencode_options_and_kind(
        self, client, mock_session_manager,
    ):
        """An explicit ``cli="opencode"`` plus an ``opencode_options`` block
        must reach ``create_session`` as an OPENCODE ``SessionConfig`` carrying
        the parsed :class:`OpenCodeOptions`. Otherwise the auto-approve choice
        is silently dropped on the relay control path."""
        from mobaide.core.models import SessionConfig
        from mobaide.core.opencode_launcher import OpenCodeOptions

        async def _capture(config: SessionConfig):
            _capture.config = config  # type: ignore[attr-defined]
            s = MagicMock()
            s.session_id = "oc-sess"
            s.pid = 7
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "oc-sess",
            "cli": "opencode",
            "opencode_options": {"auto_approve": True, "worktree": True},
        }
        payload = json.dumps(ctrl).encode()

        with patch.object(client, "_bridge_session"):
            await client._handle_control(payload, ws)

        cfg: SessionConfig = _capture.config  # type: ignore[attr-defined]
        assert cfg.kind is SessionKind.OPENCODE
        assert cfg.claude_options is None
        assert cfg.codex_options is None
        assert cfg.antigravity_options is None
        assert cfg.opencode_options == OpenCodeOptions(auto_approve=True, worktree=True)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"
        assert resp["session_id"] == "oc-sess"
        assert resp["pid"] == 7

    @pytest.mark.parametrize(
        "block",
        [{"auto_approve": "sometimes"}, {"initial_prompt": "x" * 32_001}, ["--auto"]],
        ids=["wrong-type", "prompt-over-the-cap", "not-an-object"],
    )
    async def test_invalid_opencode_options_fail_the_create(
        self, client, mock_session_manager, block,
    ):
        """A malformed block answers ``session_create_failed`` and never
        spawns: a session started with the defaults instead would ask for
        permissions the user chose to skip, or run a truncated prompt."""
        mock_session_manager.create_session = AsyncMock()
        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "bad-opencode",
            "cli": "opencode",
            "opencode_options": block,
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "bad-opencode"
        assert resp["error"].startswith("Invalid opencode_options:")
        mock_session_manager.create_session.assert_not_awaited()

    async def test_opencode_cli_missing_emits_opencode_cli_missing_discriminator(
        self, client, mock_session_manager,
    ):
        """``OpenCodeBinaryNotFoundError`` from the spawn path must map to
        ``session_create_failed`` with ``opencode_cli_missing: true`` (not
        another CLI's marker) and the OpenCode install hint. Most PCs have no
        ``opencode``, so this is the failure a user meets first."""
        from mobaide.core.opencode_launcher import OpenCodeBinaryNotFoundError

        mock_session_manager.create_session = AsyncMock(
            side_effect=OpenCodeBinaryNotFoundError(searched_paths=["PATH:opencode"]),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "no-opencode-sess",
            "cli": "opencode",
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "no-opencode-sess"
        assert resp["opencode_cli_missing"] is True
        assert "claude_cli_missing" not in resp
        assert "https://opencode.ai/docs/" in resp["error"]


class TestHandleProxyRequestOpenCode:
    """``POST /api/sessions`` proxied through the central relay
    (``proxy_request`` control frame) must thread OpenCode the same way the
    direct HTTP router does, and map a missing ``opencode`` binary to status
    503 with an ``opencode_cli_missing`` body marker.
    """

    async def test_proxy_post_sessions_threads_opencode_options(
        self, client, mock_session_manager,
    ):
        from mobaide.core.models import SessionConfig, SessionInfo
        from mobaide.core.opencode_launcher import OpenCodeOptions

        captured: dict[str, SessionConfig] = {}

        async def _capture(config: SessionConfig):
            captured["config"] = config
            s = MagicMock()
            s.session_id = "proxy-oc"
            s.pid = 11
            s.to_info = MagicMock(
                return_value=SessionInfo(
                    session_id="proxy-oc",
                    state=SessionState.RUNNING,
                    created_at=datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc),
                    pid=11,
                    command=["opencode"],
                    cwd=None,
                    kind=config.kind,
                ),
            )
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        body = {
            "cli": "opencode",
            "opencode_options": {"auto_approve": True, "base_branch": "origin/main"},
        }

        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy("POST", "/api/sessions", body, None, ws)

        cfg = captured["config"]
        assert cfg.kind is SessionKind.OPENCODE
        assert cfg.claude_options is None
        assert cfg.opencode_options == OpenCodeOptions(
            auto_approve=True, base_branch="origin/main",
        )
        assert result["status"] == 201
        assert result["body"]["kind"] == "opencode"

    async def test_proxy_opencode_cli_missing_returns_503_with_discriminator(
        self, client, mock_session_manager,
    ):
        from mobaide.core.opencode_launcher import OpenCodeBinaryNotFoundError

        mock_session_manager.create_session = AsyncMock(
            side_effect=OpenCodeBinaryNotFoundError(searched_paths=["PATH:opencode"]),
        )
        ws = AsyncMock()
        ctrl = {
            "type": "proxy_request",
            "request_id": "req-opencode-missing",
            "method": "POST",
            "path": "/api/sessions",
            "body": {"cli": "opencode", "opencode_options": {}},
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "proxy_response"
        assert resp["request_id"] == "req-opencode-missing"
        assert resp["status"] == 503
        assert resp["body"]["opencode_cli_missing"] is True
        assert "claude_cli_missing" not in resp["body"]
        assert resp["body"]["searched_paths"] == ["PATH:opencode"]


CUSTOM_KIND = "custom_0123456789ab"


class TestProxyCreateSessionCustomKind:
    """``POST /api/sessions`` over proxy_request with a ``custom_<12hex>``
    ``cli``: spawn argv comes exclusively from the relay-injected
    ``resolved_command``; a missing/malformed resolution is a clean 400 —
    never the catch-all 500, never a silent bare-shell downgrade."""

    def _created_info(self):
        from mobaide.core.models import SessionInfo

        return SessionInfo(
            session_id="sid-custom",
            state=SessionState.RUNNING,
            created_at=datetime(2026, 7, 27, 13, 0, 0, tzinfo=timezone.utc),
            pid=4242,
            command=["/bin/sh", "-l", "-c", "aider"],
            cwd="/work",
            kind=CUSTOM_KIND,
            kind_label="My Agent",
        )

    async def test_custom_create_builds_config_from_resolved_command(
        self, client, mock_session_manager,
    ):
        from mobaide.core.models import SessionConfig
        from mobaide.core.shell import build_custom_argv

        fake_session = MagicMock()
        fake_session.to_info.return_value = self._created_info()
        mock_session_manager.create_session = AsyncMock(
            return_value=fake_session
        )

        ws = AsyncMock()
        body = {
            "cli": CUSTOM_KIND,
            "resolved_command": "aider --model x",
            "resolved_name": "My Agent",
            # Client hints must stay discarded on the custom path.
            "command": ["rm", "-rf", "/"],
            "env": {"EVIL": "1"},
        }

        with patch.object(client, "_bridge_session"):
            result = await client._dispatch_proxy(
                "POST", "/api/sessions", body, None, ws,
            )

        assert result["status"] == 201
        assert result["body"]["kind"] == CUSTOM_KIND
        assert result["body"]["kind_label"] == "My Agent"

        call = mock_session_manager.create_session.await_args
        config: SessionConfig = call.args[0]
        assert config.kind == CUSTOM_KIND
        assert isinstance(config.kind, str)
        assert config.kind_label == "My Agent"
        assert config.command == build_custom_argv("aider --model x")
        assert config.command[-1] == "aider --model x"
        assert config.env is None
        assert config.claude_options is None
        assert config.codex_options is None
        assert config.antigravity_options is None

    async def test_custom_without_resolved_command_returns_400_not_500(
        self, client, mock_session_manager,
    ):
        mock_session_manager.create_session = AsyncMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST", "/api/sessions", {"cli": CUSTOM_KIND}, None, ws,
        )

        assert result["status"] == 400
        assert "relay" in result["body"]["detail"].lower()
        mock_session_manager.create_session.assert_not_awaited()

    async def test_malformed_custom_cli_returns_400(
        self, client, mock_session_manager,
    ):
        mock_session_manager.create_session = AsyncMock()

        ws = AsyncMock()
        result = await client._dispatch_proxy(
            "POST",
            "/api/sessions",
            {"cli": "custom_XYZ", "resolved_command": "echo hi"},
            None,
            ws,
        )

        assert result["status"] == 400
        mock_session_manager.create_session.assert_not_awaited()


class TestHandleCreateSessionCustomKind:
    """The dormant CH_CONTROL ``create_session`` path honours the same
    ``resolved_command``/``resolved_name`` contract (relay-only reachable),
    answering ``session_create_failed`` on an unresolved custom kind."""

    async def test_custom_create_forwards_resolved_fields_to_config(
        self, client, mock_session_manager,
    ):
        from mobaide.core.models import SessionConfig
        from mobaide.core.shell import build_custom_argv

        async def _capture(config: SessionConfig):
            _capture.config = config  # type: ignore[attr-defined]
            s = MagicMock()
            s.session_id = "sid-c"
            s.pid = 7
            return s

        mock_session_manager.create_session = AsyncMock(side_effect=_capture)

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "sid-c",
            "cli": CUSTOM_KIND,
            "resolved_command": "goose run",
            "resolved_name": "Goose",
            "command": ["rm", "-rf", "/"],
            "env": {"EVIL": "1"},
        }

        with patch.object(client, "_bridge_session"):
            await client._handle_control(json.dumps(ctrl).encode(), ws)

        cfg: SessionConfig = _capture.config  # type: ignore[attr-defined]
        assert cfg.kind == CUSTOM_KIND
        assert cfg.kind_label == "Goose"
        assert cfg.command == build_custom_argv("goose run")
        assert cfg.env is None

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_created"
        assert resp["session_id"] == "sid-c"

    async def test_custom_without_resolved_command_fails_cleanly(
        self, client, mock_session_manager,
    ):
        mock_session_manager.create_session = AsyncMock()

        ws = AsyncMock()
        ctrl = {
            "type": "create_session",
            "session_id": "sid-fail",
            "cli": CUSTOM_KIND,
        }

        await client._handle_control(json.dumps(ctrl).encode(), ws)

        resp = json.loads(decode(ws.send.call_args[0][0])[2])
        assert resp["type"] == "session_create_failed"
        assert resp["session_id"] == "sid-fail"
        assert "relay" in resp["error"].lower()
        mock_session_manager.create_session.assert_not_awaited()


class TestUnknownSessionNeverSpawnsShell:
    async def test_unknown_session_does_not_reach_the_shell_resolver(
        self, client, mock_session_manager,
    ):
        """Session input is not a create request. Spawning a login shell for
        an id that arrived over the wire is what let a stale attach resurrect
        a session and write it back into the list, keeping the client's
        last-used id "valid" forever."""
        mock_session_manager.get_session = AsyncMock(return_value=None)

        ws = AsyncMock()
        with patch(
            "mobaide.relay.client.resolve_default_shell",
            return_value=["/bin/zsh", "-l"],
        ) as resolver:
            await client._handle_session_input(
                "unknown-1", FRAME_INPUT + b"hi", ws
            )

        resolver.assert_not_called()
        mock_session_manager.create_session.assert_not_awaited()

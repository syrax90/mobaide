"""Tests for RelayClient internals — unit-level, no real WebSocket server.

Covers: _state_frame helper, _authenticate, _handle_control dispatch,
_handle_session_input routing, _cancel_all_subtasks, exponential backoff
in _connect_loop, and _send_heartbeat.
"""
from __future__ import annotations

import asyncio
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_mobile.relay.client import (
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
from claude_mobile.relay.config import RelayConfig
from claude_mobile.relay.envelope import (
    CH_CONTROL,
    CH_SESSION_IO,
    decode,
    encode,
)
from claude_mobile.core.models import SessionState


@pytest.fixture
def relay_config():
    return RelayConfig(
        server_url="wss://relay.test.io",
        agent_id="agent-42",
        agent_token="tok-secret",
        heartbeat_interval=15,
        reconnect_base_delay=0.01,
        reconnect_max_delay=0.05,
    )


@pytest.fixture
def mock_session_manager():
    mgr = AsyncMock()
    mgr.list_sessions.return_value = []
    mgr.get_session.return_value = None
    return mgr


@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    q = asyncio.Queue()
    bus.subscribe.return_value = q
    bus.unsubscribe = MagicMock()
    return bus


@pytest.fixture
def mock_broadcaster():
    bc = MagicMock()
    q = asyncio.Queue()
    bc.subscribe.return_value = q
    bc.unsubscribe = MagicMock()
    return bc


@pytest.fixture
def mock_app_state():
    state = MagicMock()
    state.fanouts = {}
    return state


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

        with patch("claude_mobile.relay.client.asyncio.sleep", new_callable=AsyncMock):
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
        client._handshake_sent.add("sess-1")

        all_tasks = [
            client._heartbeat_task,
            client._event_fwd_task,
            client._sesslist_fwd_task,
            client._session_watch_task,
            bridge_task,
        ]

        client._cancel_all_subtasks()

        # Verify bridges and handshake state were cleared
        assert len(client._bridges) == 0
        assert len(client._handshake_sent) == 0

        # Await tasks so they actually reach cancelled state
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

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
from claude_mobile.relay.envelope import (
    CH_CONTROL,
    CH_SESSION_IO,
    decode,
    encode,
)
from claude_mobile.core.models import SessionState


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
        from claude_mobile.core.claude_launcher import ClaudeOptions

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
        from claude_mobile.linear.scanner import AutoScanAlreadyRunning

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
        from claude_mobile.linear.scanner import AutoScanCapacityReached

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
        from claude_mobile.relay.client import RelayClient

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
        from claude_mobile.core.claude_launcher import ClaudeOptions
        from claude_mobile.core.models import SessionConfig, SessionState
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
        from claude_mobile.core.models import SessionState
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

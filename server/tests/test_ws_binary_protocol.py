"""End-to-end tests for the binary WebSocket frame protocol (CLA-13).

These exercise the new wire format: 1-byte type prefix + raw payload, with
a per-session 256 KB ring buffer that is replayed on reconnect.
"""
from __future__ import annotations

import json
import struct
import threading
import time

import pytest
from starlette.testclient import TestClient

from tests.conftest import TEST_USERNAME


def _recv_bytes_with_timeout(ws, timeout: float) -> bytes | None:
    """Receive a binary frame from the TestClient WS or None on timeout.

    The starlette TestClient WS uses a blocking sync receive with no
    timeout parameter, so we run it in a worker thread and join with a
    deadline.
    """
    result: list[bytes] = []

    def _worker() -> None:
        try:
            result.append(ws.receive_bytes())
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if not result:
        return None
    return result[0]


@pytest.fixture
def test_client(app):
    return TestClient(app)


@pytest.fixture
def auth_token(app) -> str:
    return app.state.jwt_service.create_access_token(TEST_USERNAME)


def _create_session(test_client: TestClient, auth_token: str) -> str:
    resp = test_client.post(
        "/api/sessions",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={},
    )
    assert resp.status_code == 201
    return resp.json()["session_id"]


def _drain_until_replay(ws) -> None:
    """Read frames until the replay-marker frame ('b') is consumed."""
    for _ in range(50):
        frame = ws.receive_bytes()
        if frame[:1] == b"b":
            return
    pytest.fail("Replay marker frame never arrived")


class TestFrameTypes:
    def test_state_frame_is_first(self, test_client, auth_token):
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            frame = ws.receive_bytes()
            assert frame[:1] == b"s"
            payload = json.loads(frame[1:].decode("utf-8"))
            assert payload["status"] in ("starting", "running")

    def test_unknown_frame_type_is_ignored(self, test_client, auth_token):
        """A bogus type byte must not crash the connection."""
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            ws.receive_bytes()  # state
            ws.receive_bytes()  # replay marker
            ws.send_bytes(b"\xff garbage")
            # Connection should still be open — send a real resize after.
            ws.send_bytes(b"r" + struct.pack(">HH", 30, 100))
            time.sleep(0.2)


class TestRingBufferReplay:
    @pytest.mark.timeout(20)
    def test_ring_replays_after_reconnect(self, test_client, auth_token):
        """Recent raw bytes are replayed in a single 'o' frame on reconnect.

        We directly seed the session's ring buffer so the test exercises the
        WS replay path without depending on shell echo behavior, which is
        unreliable across platforms (cmd.exe under ConPTY in particular
        produces variable output timing inside TestClient).
        """
        sid = _create_session(test_client, auth_token)
        marker = b"RINGREPLAYMARKER42\x1b[31mRED\x1b[0m"

        # Reach into the live session and seed its ring buffer.
        manager = test_client.app.state.session_manager
        session = manager._sessions[sid]
        session._append_ring(marker)

        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            state = ws.receive_bytes()
            assert state[:1] == b"s"
            replay_marker = ws.receive_bytes()
            assert replay_marker == b"b"
            replay_output = ws.receive_bytes()
            assert replay_output[:1] == b"o"
            # Byte-equal: the ANSI escape survives intact (no UTF-8
            # decode/encode round-trip, no base64).
            assert marker in replay_output[1:]

    def test_empty_ring_still_emits_output_frame(self, test_client, auth_token):
        """Even with an empty ring buffer the handshake yields three frames."""
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            assert ws.receive_bytes()[:1] == b"s"
            assert ws.receive_bytes() == b"b"
            third = ws.receive_bytes()
            assert third[:1] == b"o"


class TestResizeFrame:
    def test_resize_frame_dispatches(self, test_client, auth_token):
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            ws.receive_bytes()  # state
            ws.receive_bytes()  # replay marker
            ws.send_bytes(b"r" + struct.pack(">HH", 50, 132))
            # No assertion on PTY internals — just verify the connection is
            # still healthy after dispatch.
            time.sleep(0.2)
            ws.send_bytes(b"i" + b"\r")
            time.sleep(0.1)


class TestInputFrame:
    def test_input_frame_writes_raw_bytes_to_pty(self, test_client, auth_token):
        """An 'i' frame's raw bytes (including CR / Ctrl-C) reach the PTY
        backend exactly as-sent — no LF substitution, no UTF-8 round-trip."""
        sid = _create_session(test_client, auth_token)

        # Patch the live session's backend.write to capture bytes.
        manager = test_client.app.state.session_manager
        session = manager._sessions[sid]
        captured: list[bytes] = []
        original_write = session._backend.write

        def _capture(data):
            captured.append(data)
            # Don't actually write — avoids confusing the shell during the
            # rest of the test run.

        session._backend.write = _capture  # type: ignore[method-assign]

        try:
            with test_client.websocket_connect(
                f"/ws/session/{sid}?token={auth_token}",
            ) as ws:
                _drain_until_replay(ws)
                ws.receive_bytes()  # the always-emitted output frame
                payload = b"\x03echo hi\r"  # Ctrl-C then a CR-terminated line
                ws.send_bytes(b"i" + payload)
                # Give the server reader a moment to dispatch.
                time.sleep(0.5)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert captured, "input frame did not reach backend.write"
        # The first capture must be byte-equal to the payload — no LF
        # substitution, no UTF-8 mangling.
        assert captured[0] == payload


class TestExecuteFrame:
    def test_execute_frame_clears_then_writes_in_two_phases(self, test_client, auth_token):
        """An 'x' frame must write clearing bytes first, pause, then write
        the payload — two separate PTY writes so the shell / TUI processes
        the clear before the command arrives."""
        sid = _create_session(test_client, auth_token)

        manager = test_client.app.state.session_manager
        session = manager._sessions[sid]
        captured: list[bytes] = []
        original_write = session._backend.write

        def _capture(data):
            captured.append(data)

        session._backend.write = _capture  # type: ignore[method-assign]

        try:
            with test_client.websocket_connect(
                f"/ws/session/{sid}?token={auth_token}",
            ) as ws:
                _drain_until_replay(ws)
                ws.receive_bytes()  # the always-emitted output frame
                payload = b"/stats\r"
                ws.send_bytes(b"x" + payload)
                time.sleep(1.0)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert len(captured) == 2, (
            f"expected 2 writes (clear + command), got {len(captured)}"
        )
        # First write: 300 Backspace (DEL) chars to erase stale input
        assert captured[0] == b"\x7f" * 300
        # Second write: the actual command (after the pause)
        assert captured[1] == payload

    def test_execute_frame_empty_payload_does_not_write(self, test_client, auth_token):
        """An 'x' frame with no payload must NOT write anything to the PTY.

        Without this guard the server would send bare Ctrl+E Ctrl+U (0x05 0x15),
        which would needlessly clear the user's current input line.
        """
        sid = _create_session(test_client, auth_token)

        manager = test_client.app.state.session_manager
        session = manager._sessions[sid]
        captured: list[bytes] = []
        original_write = session._backend.write

        def _capture(data):
            captured.append(data)

        session._backend.write = _capture  # type: ignore[method-assign]

        try:
            with test_client.websocket_connect(
                f"/ws/session/{sid}?token={auth_token}",
            ) as ws:
                _drain_until_replay(ws)
                ws.receive_bytes()  # the always-emitted output frame
                # Send an 'x' frame with zero-length payload (type byte only)
                ws.send_bytes(b"x")
                time.sleep(0.5)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert captured == [], (
            "empty execute frame must not trigger any write, "
            f"but got {len(captured)} write(s)"
        )

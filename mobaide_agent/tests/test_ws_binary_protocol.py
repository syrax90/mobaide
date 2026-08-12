"""End-to-end tests for the binary WebSocket frame protocol.

These exercise the wire format — 1-byte type prefix + raw payload — and
the protocol-v3 reconnect handshake (``state → 'b' → 'o'(chunk)…'o'(chunk)
→ 'e'``) backed by a per-session ring buffer (default 4 MB, configurable
via ``SessionConfig.ring_buffer_bytes``).
"""
from __future__ import annotations

import json
import struct
import threading
import time

import pytest
from starlette.testclient import TestClient

from tests.conftest import TEST_USERNAME, drain_text_preamble


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
    """Read frames until the replay-marker frame ('b') is consumed.

    Skips the leading ``server_info`` text frame before reading binaries.
    """
    drain_text_preamble(ws)
    for _ in range(50):
        frame = ws.receive_bytes()
        if frame[:1] == b"b":
            return
    pytest.fail("Replay marker frame never arrived")


def _drain_through_replay_end(ws) -> None:
    """Drain the whole protocol-v3 reconnect handshake.

    Consumes state → replay-start → 1..N output chunks → replay-end. Returns
    once the ``'e'`` terminator has been read; the next ``receive_bytes`` is
    therefore the first live frame (or nothing, depending on the test).
    """
    _drain_until_replay(ws)
    for _ in range(200):
        frame = ws.receive_bytes()
        if frame[:1] == b"e":
            return
        if frame[:1] != b"o":
            pytest.fail(
                f"Unexpected frame inside replay: {frame[:1]!r}",
            )
    pytest.fail("Replay end marker never arrived")


class TestFrameTypes:
    def test_state_frame_is_first(self, test_client, auth_token):
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            drain_text_preamble(ws)
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
            _drain_through_replay_end(ws)
            ws.send_bytes(b"\xff garbage")
            # Connection should still be open — send a real resize after.
            ws.send_bytes(b"r" + struct.pack(">HH", 30, 100))
            time.sleep(0.2)


class TestRingBufferReplay:
    @pytest.mark.timeout(20)
    def test_ring_replays_after_reconnect(self, test_client, auth_token):
        """Recent raw bytes are replayed across one or more 'o' chunks on
        reconnect, framed by 'b' (start) and 'e' (end) markers.

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
            drain_text_preamble(ws)
            state = ws.receive_bytes()
            assert state[:1] == b"s"
            winsize_frame = ws.receive_bytes()
            assert winsize_frame[:1] == b"w"
            replay_marker = ws.receive_bytes()
            assert replay_marker == b"b"
            # Concatenate all 'o' chunks until 'e' terminates the replay.
            chunks = bytearray()
            for _ in range(50):
                frame = ws.receive_bytes()
                if frame[:1] == b"e":
                    break
                assert frame[:1] == b"o"
                chunks += frame[1:]
            else:
                pytest.fail("Replay never terminated with 'e'")
            # Byte-equal: the ANSI escape survives intact (no UTF-8
            # decode/encode round-trip, no base64).
            assert marker in chunks

    def test_empty_ring_still_emits_output_frame(self, test_client, auth_token):
        """Even with an empty ring buffer the handshake yields the full v8
        sequence: state ('s') → winsize ('w') → replay-start ('b') → empty
        output ('o') → replay-end ('e'). The empty 'o' preserves the
        protocol-v2 invariant that at least one output frame appears inside
        the replay span."""
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            drain_text_preamble(ws)
            assert ws.receive_bytes()[:1] == b"s"
            assert ws.receive_bytes()[:1] == b"w"
            assert ws.receive_bytes() == b"b"
            third = ws.receive_bytes()
            assert third[:1] == b"o"
            assert third[1:] == b""
            assert ws.receive_bytes() == b"e"

    @pytest.mark.timeout(20)
    def test_ring_larger_than_one_chunk_emits_multiple_output_frames(
        self, test_client, auth_token,
    ):
        """Multi-chunk replay path: a ring strictly larger than
        ``REPLAY_CHUNK_BYTES`` (64 KiB) must produce **two or more**
        ``FRAME_OUTPUT`` frames between the ``FRAME_REPLAY`` start marker
        and the ``FRAME_REPLAY_END`` terminator, and the concatenation of
        their payloads must be byte-equal to the original ring.

        Existing tests (``test_ring_replays_after_reconnect``,
        ``test_attach_sends_full_handshake_in_order``) only cover the
        single-frame case; this guards the chunked emission loop that the
        protocol-v3 bump introduced.
        """
        from mobaide.core.session import REPLAY_CHUNK_BYTES

        sid = _create_session(test_client, auth_token)
        # 64 KiB + 1 — the boundary case called out in the plan. Each byte
        # is unique modulo 256 so a head-trim or chunk reorder would be
        # detected by the byte-equal assertion rather than a length-only
        # comparison.
        ring = bytes((i & 0xFF) for i in range(REPLAY_CHUNK_BYTES + 1))

        manager = test_client.app.state.session_manager
        session = manager._sessions[sid]
        session._append_ring(ring)

        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            # session_io.py sends a single ``server_info`` text frame before
            # any binary handshake frame (the version/capabilities envelope
            # the mobile uses to render the "agent outdated" banner). Drain
            # it via the low-level ``receive()`` so this test runs cleanly
            # regardless of whether the existing ``receive_bytes()``-first
            # tests in the same class are kept or updated.
            first = ws.receive()
            if first.get("text") is not None:
                state = ws.receive_bytes()
            else:
                state = first["bytes"]
            assert state[:1] == b"s"
            assert ws.receive_bytes()[:1] == b"w"
            assert ws.receive_bytes() == b"b"

            output_chunks: list[bytes] = []
            for _ in range(64):
                frame = ws.receive_bytes()
                if frame == b"e":
                    break
                assert frame[:1] == b"o", (
                    f"unexpected frame type inside replay: {frame[:1]!r}"
                )
                output_chunks.append(frame[1:])
            else:
                pytest.fail("Replay never terminated with 'e'")

            assert len(output_chunks) >= 2, (
                "ring of size REPLAY_CHUNK_BYTES + 1 must split into at "
                f"least 2 output frames; got {len(output_chunks)}"
            )
            # Every chunk except possibly the last must equal the server's
            # configured chunk size; the trailing chunk holds the remainder.
            for chunk in output_chunks[:-1]:
                assert len(chunk) == REPLAY_CHUNK_BYTES
            assert len(output_chunks[-1]) <= REPLAY_CHUNK_BYTES
            # Byte-equal concatenation: head not trimmed, tail not duplicated.
            assert b"".join(output_chunks) == ring


class TestResizeFrame:
    def test_resize_frame_dispatches(self, test_client, auth_token):
        sid = _create_session(test_client, auth_token)
        with test_client.websocket_connect(
            f"/ws/session/{sid}?token={auth_token}",
        ) as ws:
            _drain_through_replay_end(ws)
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
                _drain_through_replay_end(ws)
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
    def test_execute_frame_sends_ctrl_c_then_payload_then_cr(
        self, test_client, auth_token
    ):
        """An 'x' frame must write Ctrl+C, pause, write the payload, pause,
        then write CR — three separate PTY writes so the TUI cancels stale
        input, accepts the replacement text, and parses the CR as a discrete
        Enter rather than pasted content."""
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
                _drain_through_replay_end(ws)
                payload = b"/stats"
                ws.send_bytes(b"x" + payload)
                time.sleep(1.0)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert len(captured) == 3, (
            f"expected 3 writes (Ctrl+C + command + CR), got {len(captured)}"
        )
        # First write: Ctrl+C to cancel stale input
        assert captured[0] == b"\x03"
        # Second write: the actual command (after the pause)
        assert captured[1] == payload
        # Third write: the CR that submits it, as its own delayed write
        assert captured[2] == b"\r"

    def test_execute_frame_empty_payload_does_not_write(self, test_client, auth_token):
        """An 'x' frame with no payload must NOT write anything to the PTY.

        Without this guard the server would send a bare Ctrl+C, which would
        needlessly cancel the user's current input.
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
                _drain_through_replay_end(ws)
                # Send an 'x' frame with zero-length payload (type byte only)
                ws.send_bytes(b"x")
                time.sleep(0.5)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert captured == [], (
            "empty execute frame must not trigger any write, "
            f"but got {len(captured)} write(s)"
        )


class TestSubmitFrame:
    def test_submit_frame_writes_payload_then_cr(self, test_client, auth_token):
        """An 'm' frame must write the payload, pause, then write CR as a
        separate PTY write. Ink TUIs (Claude CLI) treat a CR embedded in the
        same stdin chunk as pasted content — only a CR arriving as its own
        later read registers as the Enter key."""
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
                _drain_through_replay_end(ws)
                payload = b"\x01\x0bhello world"
                ws.send_bytes(b"m" + payload)
                time.sleep(1.0)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert len(captured) == 2, (
            f"expected 2 writes (payload + CR), got {len(captured)}"
        )
        # First write: the text exactly as sent (clear prefix included)
        assert captured[0] == payload
        # Second write: the CR that submits it, as its own delayed write
        assert captured[1] == b"\r"

    def test_submit_frame_empty_payload_does_not_write(self, test_client, auth_token):
        """An 'm' frame with no payload must NOT write anything to the PTY —
        a bare Enter could submit whatever stale text sits on the composer."""
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
                _drain_through_replay_end(ws)
                ws.send_bytes(b"m")
                time.sleep(0.5)
        finally:
            session._backend.write = original_write  # type: ignore[method-assign]

        assert captured == [], (
            "empty submit frame must not trigger any write, "
            f"but got {len(captured)} write(s)"
        )

"""Tests for the ``/ws/autoscan`` WebSocket handler.

Covers the full streaming protocol:
  * auth/configuration gating (4001 / 4002)
  * snapshot + caught_up envelopes
  * optional initial config frame (subscribe / resume_from_seq, happy + malformed)
  * live event pump + ``log`` event filtering via subscribe_set
  * subscribe updates mid-stream
  * cleanup (queues unsubscribed on disconnect)
  * empty-instances edge case (``_reader``-only branch)

The REST portion of the router is already fully covered by
``test_autoscan_router.py`` and must not be duplicated here.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from starlette.testclient import TestClient

from claude_mobile.linear.models import ScanEvent
from tests.conftest import TEST_USERNAME


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class FakeAutoScanInstance:
    """Faithful fake of ``AutoScanInstance`` — only the surface the handler uses."""

    project_name: str
    _recent_events: list[ScanEvent] = field(default_factory=list)
    _event_subscribers: set[asyncio.Queue] = field(default_factory=set)
    _dto: dict[str, Any] = field(default_factory=dict)
    # Captured from the server's event loop on first ``subscribe_events`` call
    # so tests can safely enqueue events from the main thread via
    # ``loop.call_soon_threadsafe``.
    _loop: asyncio.AbstractEventLoop | None = None

    @property
    def recent_events(self) -> list[ScanEvent]:
        return list(self._recent_events)

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._event_subscribers.add(q)
        # Called from within the ASGI/server event loop — capture it for
        # thread-safe event injection from the test thread.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Should never happen; subscribe_events is only called from async code.
            self._loop = None
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        self._event_subscribers.discard(q)

    def to_dto(self) -> dict[str, Any]:
        return dict(self._dto)

    # ---- test helpers -----------------------------------------------------

    def push_event(self, evt: ScanEvent) -> None:
        """Push an event into every live subscriber queue, from the test thread.

        Schedules ``q.put_nowait(evt)`` on the owning event loop so the async
        pump task can consume it.
        """
        loop = self._loop
        assert loop is not None, "subscribe_events must be called first"
        queues = list(self._event_subscribers)
        for q in queues:
            loop.call_soon_threadsafe(q.put_nowait, evt)


class FakeAutoScanSessionManager:
    """Faithful fake of ``AutoScanSessionManager`` — only what the ws handler uses."""

    def __init__(self, instances: list[FakeAutoScanInstance]) -> None:
        self._instances: dict[str, FakeAutoScanInstance] = {
            i.project_name: i for i in instances
        }
        self._list_response_data: dict[str, Any] = {
            "scans": [i.to_dto() for i in instances],
            "capacity": len(instances),
            "max_scans": 5,
            "available_projects": list(self._instances.keys()),
            "auto_scan_prompt": None,
        }

    def all_instances(self) -> list[FakeAutoScanInstance]:
        return list(self._instances.values())

    def get(self, name: str) -> FakeAutoScanInstance | None:
        return self._instances.get(name)

    def list_response(self) -> dict[str, Any]:
        return dict(self._list_response_data)


def _make_event(seq: int, type_: str = "phase", extra: dict | None = None) -> ScanEvent:
    payload: dict[str, Any] = {"note": f"evt-{seq}"}
    if extra:
        payload.update(extra)
    return ScanEvent(
        seq=seq,
        ts=f"2026-04-11T00:00:{seq:02d}+00:00",
        type=type_,
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# Fixtures — reuse the shared ``app`` / ``access_token`` from conftest.
# --------------------------------------------------------------------------- #


@pytest.fixture
def test_client(app):
    return TestClient(app)


@pytest.fixture
def auth_token(app) -> str:
    return app.state.jwt_service.create_access_token(TEST_USERNAME)


def _install_manager(
    app, instances: list[FakeAutoScanInstance]
) -> FakeAutoScanSessionManager:
    mgr = FakeAutoScanSessionManager(instances)
    # ``app.state`` is dynamic — no cast needed for Pyright.
    app.state.autoscan_manager = mgr
    return mgr


# --------------------------------------------------------------------------- #
# Auth / configuration gating
# --------------------------------------------------------------------------- #


class TestAuthAndConfiguration:
    def test_missing_token_rejected(self, test_client):
        """No ``?token=`` query param → ws_authenticate closes 4001 and handler returns."""
        with pytest.raises(Exception):
            with test_client.websocket_connect("/ws/autoscan") as ws:
                ws.receive_json()

    def test_invalid_token_rejected(self, test_client):
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                "/ws/autoscan?token=not.a.real.jwt",
            ) as ws:
                ws.receive_json()

    def test_refresh_token_rejected(self, test_client, app):
        """Only access tokens are valid for the ws channel."""
        refresh = app.state.jwt_service.create_refresh_token(TEST_USERNAME)
        with pytest.raises(Exception):
            with test_client.websocket_connect(
                f"/ws/autoscan?token={refresh}",
            ) as ws:
                ws.receive_json()

    def test_not_configured_closes_4002(self, test_client, auth_token, app):
        """Default app fixture has ``autoscan_manager = None`` — handler must
        send close 4002 "Auto scan not configured" right after auth.
        """
        assert app.state.autoscan_manager is None
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as excinfo:
            with test_client.websocket_connect(
                f"/ws/autoscan?token={auth_token}",
            ) as ws:
                # The 200ms initial-frame wait elapses, then the handler
                # sends the close. ``receive_json`` raises.
                ws.receive_json()
        assert excinfo.value.code == 4002


# --------------------------------------------------------------------------- #
# Snapshot protocol
# --------------------------------------------------------------------------- #


class TestSnapshot:
    def test_snapshot_with_two_instances(self, test_client, auth_token, app):
        inst_a = FakeAutoScanInstance(
            project_name="projectA",
            _recent_events=[_make_event(1, "phase"), _make_event(2, "issue")],
            _dto={"project_name": "projectA", "status": "running"},
        )
        inst_b = FakeAutoScanInstance(
            project_name="projectB",
            _recent_events=[_make_event(10, "worktree"), _make_event(11, "session")],
            _dto={"project_name": "projectB", "status": "running"},
        )
        _install_manager(app, [inst_a, inst_b])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            snap = ws.receive_json()
            caught_up = ws.receive_json()

        assert snap["type"] == "snapshot"
        assert "data" in snap
        scans = snap["data"]["scans"]
        assert isinstance(scans, list) and len(scans) == 2
        project_names = {s["project_name"] for s in scans}
        assert project_names == {"projectA", "projectB"}

        events = snap["data"]["events"]
        assert set(events.keys()) == {"projectA", "projectB"}
        assert [e["seq"] for e in events["projectA"]] == [1, 2]
        assert [e["type"] for e in events["projectA"]] == ["phase", "issue"]
        assert [e["seq"] for e in events["projectB"]] == [10, 11]
        # Each serialized event has the full wire shape.
        for e in events["projectA"] + events["projectB"]:
            assert set(e.keys()) == {"version", "seq", "ts", "type", "payload"}

        assert caught_up == {"type": "caught_up"}

    def test_snapshot_empty_instances(self, test_client, auth_token, app):
        _install_manager(app, [])
        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            snap = ws.receive_json()
            caught_up = ws.receive_json()

        assert snap["type"] == "snapshot"
        assert snap["data"]["scans"] == []
        assert snap["data"]["events"] == {}
        assert caught_up == {"type": "caught_up"}


# --------------------------------------------------------------------------- #
# Initial config frame handling
# --------------------------------------------------------------------------- #


class TestInitialFrame:
    def test_no_initial_frame_still_works(self, test_client, auth_token, app):
        """Client sends nothing → server 200ms timeout elapses → snapshot still flows."""
        _install_manager(
            app,
            [FakeAutoScanInstance("p", _recent_events=[_make_event(1)])],
        )
        t0 = time.monotonic()
        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            snap = ws.receive_json()
            caught_up = ws.receive_json()
        elapsed = time.monotonic() - t0

        assert snap["type"] == "snapshot"
        assert caught_up == {"type": "caught_up"}
        # We should have waited at least some meaningful fraction of the
        # 200ms initial-frame timeout (allow headroom for CI jitter).
        assert elapsed >= 0.15

    def test_invalid_json_initial_frame_silently_ignored(
        self, test_client, auth_token, app
    ):
        _install_manager(
            app,
            [FakeAutoScanInstance("p", _recent_events=[_make_event(1)])],
        )
        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.send_text("{not valid json")
            snap = ws.receive_json()
            caught_up = ws.receive_json()

        assert snap["type"] == "snapshot"
        assert caught_up == {"type": "caught_up"}

    def test_malformed_subscribe_and_resume_types_silently_ignored(
        self, test_client, auth_token, app
    ):
        """``subscribe`` is a str (not a list) and ``resume_from_seq`` is a str
        (not a dict) — isinstance guards should drop them; snapshot still fires.
        """
        _install_manager(
            app,
            [
                FakeAutoScanInstance(
                    "p",
                    _recent_events=[_make_event(1), _make_event(2)],
                ),
            ],
        )
        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "subscribe": "not-a-list",
                        "resume_from_seq": "not-a-dict",
                    }
                )
            )
            snap = ws.receive_json()
            caught_up = ws.receive_json()

        assert snap["type"] == "snapshot"
        # Both events should be present — no filtering applied.
        assert [e["seq"] for e in snap["data"]["events"]["p"]] == [1, 2]
        assert caught_up == {"type": "caught_up"}

    def test_resume_from_seq_in_range_filters_ring(
        self, test_client, auth_token, app
    ):
        """``first_seq (1) <= resume_seq (2) + 1`` → filter to ``seq > 2``."""
        _install_manager(
            app,
            [
                FakeAutoScanInstance(
                    "projectA",
                    _recent_events=[_make_event(s) for s in (1, 2, 3, 4, 5)],
                ),
            ],
        )
        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.send_text(json.dumps({"resume_from_seq": {"projectA": 2}}))
            snap = ws.receive_json()
            caught_up = ws.receive_json()

        assert snap["type"] == "snapshot"
        seqs = [e["seq"] for e in snap["data"]["events"]["projectA"]]
        assert seqs == [3, 4, 5]
        assert caught_up == {"type": "caught_up"}

    def test_resume_from_seq_gap_returns_full_ring(
        self, test_client, auth_token, app
    ):
        """``first_seq (10) > resume_seq (5) + 1`` → filter skipped, full ring sent."""
        _install_manager(
            app,
            [
                FakeAutoScanInstance(
                    "projectA",
                    _recent_events=[_make_event(s) for s in (10, 11, 12)],
                ),
            ],
        )
        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.send_text(json.dumps({"resume_from_seq": {"projectA": 5}}))
            snap = ws.receive_json()
            caught_up = ws.receive_json()

        assert snap["type"] == "snapshot"
        seqs = [e["seq"] for e in snap["data"]["events"]["projectA"]]
        assert seqs == [10, 11, 12]
        assert caught_up == {"type": "caught_up"}


# --------------------------------------------------------------------------- #
# Live event pump
# --------------------------------------------------------------------------- #


class TestLiveEvents:
    def test_log_event_filtered_for_unsubscribed_client(
        self, test_client, auth_token, app
    ):
        """Client does NOT subscribe → a ``log`` event is dropped. A following
        ``phase`` event is delivered, proving the pump is alive and the filter
        is selective to ``type == 'log'``.
        """
        inst = FakeAutoScanInstance("projectA")
        _install_manager(app, [inst])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.receive_json()  # snapshot
            ws.receive_json()  # caught_up

            # No subscribe frame from the client → log events filtered out.
            inst.push_event(_make_event(100, type_="log"))
            inst.push_event(_make_event(101, type_="phase"))

            frame = ws.receive_json()
            assert frame["type"] == "event"
            assert frame["project_name"] == "projectA"
            # The log event (seq=100) was skipped; we get the phase (seq=101).
            assert frame["event"]["seq"] == 101
            assert frame["event"]["type"] == "phase"

    def test_log_event_delivered_for_subscribed_client(
        self, test_client, auth_token, app
    ):
        """After an initial ``{"subscribe": ["projectA"]}`` frame, log events flow."""
        inst = FakeAutoScanInstance("projectA")
        _install_manager(app, [inst])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.send_text(json.dumps({"subscribe": ["projectA"]}))
            ws.receive_json()  # snapshot
            ws.receive_json()  # caught_up

            inst.push_event(_make_event(7, type_="log"))
            frame = ws.receive_json()

            assert frame["type"] == "event"
            assert frame["project_name"] == "projectA"
            assert frame["event"]["type"] == "log"
            assert frame["event"]["seq"] == 7

    def test_non_log_event_always_delivered(self, test_client, auth_token, app):
        inst = FakeAutoScanInstance("projectA")
        _install_manager(app, [inst])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.receive_json()  # snapshot
            ws.receive_json()  # caught_up

            # issue, worktree, session all pass the filter (only 'log' is gated).
            inst.push_event(_make_event(1, type_="issue"))
            inst.push_event(_make_event(2, type_="worktree"))
            inst.push_event(_make_event(3, type_="session"))

            got_types = []
            for _ in range(3):
                frame = ws.receive_json()
                assert frame["type"] == "event"
                assert frame["project_name"] == "projectA"
                got_types.append(frame["event"]["type"])

        assert got_types == ["issue", "worktree", "session"]

    def test_multi_project_event_routing(self, test_client, auth_token, app):
        """Events flow to the correct ``project_name`` label on each frame."""
        inst_a = FakeAutoScanInstance("A")
        inst_b = FakeAutoScanInstance("B")
        _install_manager(app, [inst_a, inst_b])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.receive_json()  # snapshot
            ws.receive_json()  # caught_up

            inst_a.push_event(_make_event(1, type_="phase"))
            frame1 = ws.receive_json()
            assert frame1["project_name"] == "A"
            assert frame1["event"]["seq"] == 1

            inst_b.push_event(_make_event(2, type_="phase"))
            frame2 = ws.receive_json()
            assert frame2["project_name"] == "B"
            assert frame2["event"]["seq"] == 2


# --------------------------------------------------------------------------- #
# Subscribe mid-stream
# --------------------------------------------------------------------------- #


class TestSubscribeMidStream:
    def test_subscribe_frame_after_snapshot_enables_log_delivery(
        self, test_client, auth_token, app
    ):
        """Initial frame not sent → server's 200ms timeout elapses → snapshot flows.
        Then the reader task takes over; a late ``{"subscribe": [...]}`` frame
        updates ``subscribe_set`` so subsequent log events are delivered.
        """
        inst = FakeAutoScanInstance("projectA")
        _install_manager(app, [inst])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.receive_json()  # snapshot
            ws.receive_json()  # caught_up

            # Late subscribe — reader task must process this text frame and
            # mutate ``subscribe_set``.
            ws.send_text(json.dumps({"subscribe": ["projectA"]}))
            # Small real-time delay so the reader coroutine has a chance to
            # run before we push the gated event.
            time.sleep(0.1)

            inst.push_event(_make_event(42, type_="log"))
            frame = ws.receive_json()

            assert frame["type"] == "event"
            assert frame["project_name"] == "projectA"
            assert frame["event"]["type"] == "log"
            assert frame["event"]["seq"] == 42


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #


class TestCleanup:
    def test_queues_unsubscribed_on_disconnect(
        self, test_client, auth_token, app
    ):
        inst = FakeAutoScanInstance("projectA")
        _install_manager(app, [inst])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            ws.receive_json()  # snapshot
            ws.receive_json()  # caught_up
            # Inside the context: exactly one subscriber queue is registered.
            assert len(inst._event_subscribers) == 1

        # On block exit the client disconnects; the handler's ``finally``
        # must call ``unsubscribe_events`` for every queue it created.
        # Give the background ASGI thread a brief moment to run ``finally``.
        deadline = time.monotonic() + 1.0
        while inst._event_subscribers and time.monotonic() < deadline:
            time.sleep(0.02)
        assert inst._event_subscribers == set()


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_no_instances_reader_only_branch(self, test_client, auth_token, app):
        """``all_instances() == []`` → handler enters ``if not queues: await _reader()``.

        The socket stays open, the client gets snapshot + caught_up, then
        disconnects cleanly — no crash, no unretrieved task exceptions.
        """
        _install_manager(app, [])

        with test_client.websocket_connect(
            f"/ws/autoscan?token={auth_token}",
        ) as ws:
            snap = ws.receive_json()
            caught_up = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert snap["data"]["scans"] == []
            assert snap["data"]["events"] == {}
            assert caught_up == {"type": "caught_up"}
            # Send a no-op text frame to exercise the reader loop branch.
            ws.send_text(json.dumps({"subscribe": []}))
            # Tiny delay so the reader processes it before we disconnect.
            time.sleep(0.05)
        # Block exit = clean disconnect. No assertion needed beyond not-crashing.


# Pyright unused-symbol anchors (helpers used only above via string references).
_ = threading

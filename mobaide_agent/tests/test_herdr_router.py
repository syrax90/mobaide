"""``/api/herdr`` through the FastAPI app and the relay ``dispatch_herdr``
dispatcher (the same answers on both transports), ``capabilities.herdr`` on
``/api/version``, the ``herdr.changed`` connect snapshots, the
``herdr.attention`` user event, the ``pane`` block of the session state frame
and the viewer count the relay keeps for a pane session.

The herdr socket has no authentication, so these routes are the whole access
control: every one needs a user, and no path reaches a herdr method outside
the route table. The server is the scripted ``tui:demo`` of
``mobaide.herdr.fake``; no test reaches a real ``herdr`` process.
"""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.agent.events.bus import AgentEvent
from mobaide.herdr import service as service_module
from mobaide.herdr.dispatch import dispatch_herdr
from mobaide.herdr.fake import SERVER_ID, WATCH_GRID
from mobaide.herdr.models import HERDR_CAPABILITIES
from mobaide.herdr.pane_session import HerdrPaneSession
from mobaide.herdr.service import pane_session_id
from mobaide.relay.client import (
    FRAME_OUTPUT,
    FRAME_STATE,
    HERDR_DISPLAY_MAX_CHARS,
    RelayClient,
    _herdr_attention_user_event,
    _state_frame,
)
from mobaide.relay.envelope import CH_CONTROL, CH_EVENTS, CH_SESSION_IO, decode

from .conftest import TEST_JWT_SECRET, TEST_USERNAME
from .herdr_support import AGENT_PANE, GRID, ScriptedLink, settle

HERDR = "/api/herdr"
OPEN = f"{HERDR}/pane/open"
CONTROL = f"{HERDR}/pane/control"
TEXT = f"{HERDR}/pane/text"
TERMINALS = f"{HERDR}/terminals"
RENAME = f"{HERDR}/rename"
CLOSE = f"{HERDR}/close"
CLAUDE_PANE = {"server_id": SERVER_ID, "pane_id": "w1:p1"}
ATTENTION = {
    "server_id": "tui:default",
    "pane_id": "w1:p1",
    "reason": "awaiting_permission",
    "title": "api refactor",
    "agent": "claude",
}


def _config(tmp_path, **overrides) -> AgentConfig:
    fields = {
        "jwt_secret_key": TEST_JWT_SECRET,
        "user_store_path": str(tmp_path / "users.json"),
        "projects_store_path": str(tmp_path / "projects.json"),
        "voice_dir": str(tmp_path / "voice"),
        "herdr_fake": True,
        "max_sessions": 4,
        "log_level": "DEBUG",
    }
    fields.update(overrides)
    return AgentConfig(**fields)


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


@pytest.fixture
async def herdr_app(tmp_path):
    application = create_app(config=_config(tmp_path))
    herdr = application.state.herdr
    bus = application.state.event_bus
    first_state = bus.subscribe()
    await herdr.start()
    # The service publishes its first read a moment after it finds the server.
    # Waiting for that event keeps it out of what a test observes later.
    while (await asyncio.wait_for(first_state.get(), 5)).type != "herdr.changed":
        pass
    bus.unsubscribe(first_state)
    yield application
    await herdr.shutdown()
    await application.state.session_manager.shutdown()


@pytest.fixture
async def http(herdr_app):
    async with AsyncClient(transport=ASGITransport(app=herdr_app), base_url="http://test") as c:
        yield c


@pytest.fixture
def headers(herdr_app) -> dict[str, str]:
    token = herdr_app.state.jwt_service.create_access_token(TEST_USERNAME)
    return {"Authorization": f"Bearer {token}"}


def _relay(app_state, session_manager, relay_config, mock_event_bus, mock_broadcaster):
    return RelayClient(
        config=relay_config,
        session_manager=session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=app_state,
    )


async def _assert_parity(http, headers, herdr_app, method, url, body=None, query=None):
    resp = await http.request(method, url, headers=headers, json=body, params=query)
    dispatched = await dispatch_herdr(herdr_app.state.herdr, method, url, body, query)
    assert dispatched == {"status": resp.status_code, "body": resp.json()}
    return resp


def _labels(snapshot: dict) -> dict[str, list[str]]:
    return {
        ws["label"]: [tab["label"] for tab in ws["tabs"]]
        for ws in snapshot["servers"][0]["workspaces"]
    }


class TestRouting:
    @pytest.mark.parametrize(
        "method,url",
        [("GET", HERDR), ("GET", TEXT), ("POST", OPEN), ("POST", CONTROL),
         ("POST", f"{HERDR}/pane/agent"), ("POST", TERMINALS), ("POST", RENAME),
         ("POST", CLOSE)],
    )
    async def test_every_route_requires_auth(self, http, method, url):
        resp = await http.request(method, url, json={})

        assert resp.status_code == 401

    async def test_snapshot_on_both_transports(self, http, headers, herdr_app):
        resp = await _assert_parity(http, headers, herdr_app, "GET", HERDR)

        assert resp.status_code == 200
        (server,) = resp.json()["servers"]
        assert (server["server_id"], server["product"], server["state"]) == (
            SERVER_ID, "tui", "ready",
        )
        assert _labels(resp.json()) == {"mobaide": ["main"], "website": ["main"]}

    async def test_open_pane_answers_the_stable_session_id_on_both_transports(
        self, http, headers, herdr_app
    ):
        resp = await _assert_parity(http, headers, herdr_app, "POST", OPEN, CLAUDE_PANE)

        assert resp.json() == {"session_id": pane_session_id(SERVER_ID, "w1:p1")}
        # The pane view is reachable by id and is in no session list.
        sessions = await http.get("/api/sessions", headers=headers)
        assert sessions.json() == []

    @pytest.mark.parametrize(
        "target,code",
        [
            ({"server_id": SERVER_ID, "pane_id": "w9:p9"}, "pane_not_found"),
            ({"server_id": "tui:gone", "pane_id": "w1:p1"}, "server_not_found"),
        ],
    )
    async def test_a_missing_target_is_a_coded_404_on_both_transports(
        self, http, headers, herdr_app, target, code
    ):
        resp = await _assert_parity(http, headers, herdr_app, "POST", OPEN, target)

        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == code
        assert resp.json()["detail"]["message"]

    @pytest.mark.parametrize(
        "url,body",
        [
            (OPEN, {"server_id": SERVER_ID}),
            (OPEN, {"server_id": SERVER_ID, "pane_id": "p" * 201}),
            (CONTROL, CLAUDE_PANE),
            (CONTROL, {**CLAUDE_PANE, "control": True, "client_id": "c" * 65}),
            (TERMINALS, {"server_id": SERVER_ID, "placement": "sideways"}),
            # Herdr draws a label on the user's desktop terminal.
            (TERMINALS, {"server_id": SERVER_ID, "placement": "workspace", "label": "a\x1b[2Jb"}),
            (RENAME, {"server_id": SERVER_ID, "target": "pane", "id": "w1:p1", "label": "a\rb"}),
            (RENAME, {"server_id": SERVER_ID, "target": "pane", "id": "w1:p1", "label": "x" * 81}),
            (RENAME, {"server_id": SERVER_ID, "target": "pane", "id": "w1:p1", "label": ""}),
            (CLOSE, {"server_id": SERVER_ID, "target": "server", "id": "tui:demo"}),
        ],
        ids=["no-pane", "long-pane-id", "no-control-flag", "long-client-id",
             "bad-placement", "escape-in-new-label", "control-char-in-label",
             "long-label", "empty-label", "close-server"],
    )
    async def test_a_malformed_body_is_422_on_both_transports(
        self, http, headers, herdr_app, url, body
    ):
        before = herdr_app.state.herdr.snapshot()

        resp = await http.post(url, headers=headers, json=body)
        dispatched = await dispatch_herdr(herdr_app.state.herdr, "POST", url, body, None)

        assert resp.status_code == dispatched["status"] == 422
        assert herdr_app.state.herdr.snapshot() == before

    @pytest.mark.parametrize(
        "method,url",
        [
            ("POST", f"{HERDR}/server/stop"),
            ("POST", f"{HERDR}/server.stop"),
            ("POST", f"{HERDR}/layout/apply"),
            ("POST", f"{HERDR}/plugin/install"),
            ("DELETE", HERDR),
            ("POST", HERDR),
        ],
    )
    async def test_no_route_exists_outside_the_table(
        self, http, headers, herdr_app, method, url
    ):
        body = {"server_id": SERVER_ID, "method": "server.stop", "params": {}}

        resp = await http.request(method, url, headers=headers, json=body)
        dispatched = await dispatch_herdr(herdr_app.state.herdr, method, url, body, None)

        assert resp.status_code in (404, 405)
        assert dispatched["status"] == 404
        assert len(herdr_app.state.herdr.snapshot().servers) == 1

    async def test_pane_text_takes_its_ids_from_the_query(self, http, headers, herdr_app):
        query = {"server_id": SERVER_ID, "pane_id": "w1:p2", "lines": "1"}

        resp = await _assert_parity(http, headers, herdr_app, "GET", TEXT, query=query)

        assert resp.json() == {"text": "nothing to commit", "truncated": False}

    async def test_actions_reach_the_server_on_both_transports(
        self, http, headers, herdr_app, tmp_path
    ):
        herdr = herdr_app.state.herdr

        renamed = await http.post(
            RENAME, headers=headers,
            json={"server_id": SERVER_ID, "target": "workspace", "id": "w1", "label": "agent"},
        )
        created = await dispatch_herdr(
            herdr, "POST", TERMINALS,
            {"server_id": SERVER_ID, "placement": "tab", "workspace_id": "w1",
             "cwd": str(tmp_path), "label": "logs"},
            None,
        )
        closed = await http.post(
            CLOSE, headers=headers,
            json={"server_id": SERVER_ID, "target": "workspace", "id": "w2"},
        )

        assert renamed.json() == closed.json() == {"ok": True}
        assert created["status"] == 200 and created["body"]["pane_id"]
        snapshot = (await http.get(HERDR, headers=headers)).json()
        assert _labels(snapshot) == {"agent": ["main", "logs"]}

    async def test_a_folder_that_does_not_exist_is_refused(
        self, http, headers, herdr_app, tmp_path
    ):
        body = {"server_id": SERVER_ID, "placement": "workspace",
                "cwd": str(tmp_path / "missing")}

        resp = await _assert_parity(http, headers, herdr_app, "POST", TERMINALS, body)

        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "invalid_folder"

    async def test_one_client_controls_a_pane_on_both_transports(
        self, http, headers, herdr_app
    ):
        herdr = herdr_app.state.herdr

        def body(client_id: str, control: bool = True) -> dict:
            return {**CLAUDE_PANE, "control": control, "client_id": client_id}

        opened = await http.post(OPEN, headers=headers, json=CLAUDE_PANE)
        session = await herdr_app.state.session_manager.get_session(
            opened.json()["session_id"]
        )

        # No client shows the pane yet.
        refused = await _assert_parity(http, headers, herdr_app, "POST", CONTROL, body("phone"))
        session.viewer_attached()
        await settle()
        taken = await http.post(CONTROL, headers=headers, json=body("phone"))
        # A second client that leaves the pane view sends a release.
        released = await dispatch_herdr(herdr, "POST", CONTROL, body("tablet", False), None)

        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "pane_not_open"
        assert taken.json() == {"ok": True}
        assert released == {"status": 200, "body": {"ok": True}}
        assert session.wire_state_extras() == {
            "pane": {
                "mode": "control", "controller": "phone",
                "cols": WATCH_GRID[0], "rows": WATCH_GRID[1],
            }
        }

        await dispatch_herdr(herdr, "POST", CONTROL, body("tablet"), None)

        assert session.wire_state_extras()["pane"]["controller"] == "tablet"


class TestAuditActor:
    """A Herdr action changes a program on the desktop, so its audit record
    says who did it. A direct route knows its user; a relayed request has
    none, as for projects."""

    async def test_a_direct_action_records_its_user_and_a_relayed_one_records_none(
        self, http, headers, herdr_app, monkeypatch
    ):
        written: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            service_module, "audit_event",
            lambda event, **fields: written.append((event, fields)),
        )
        opened = await http.post(OPEN, headers=headers, json=CLAUDE_PANE)
        session = await herdr_app.state.session_manager.get_session(
            opened.json()["session_id"]
        )
        session.viewer_attached()
        await settle()
        rename = {"server_id": SERVER_ID, "target": "workspace", "id": "w1", "label": "agent"}

        answers = [
            await http.post(url, headers=headers, json=body)
            for url, body in [
                (CONTROL, {**CLAUDE_PANE, "control": True, "client_id": "phone"}),
                (f"{HERDR}/pane/agent",
                 {"server_id": SERVER_ID, "pane_id": "w1:p2", "agent": "claude"}),
                (TERMINALS, {"server_id": SERVER_ID, "placement": "workspace"}),
                (RENAME, rename),
                (CLOSE, {"server_id": SERVER_ID, "target": "workspace", "id": "w2"}),
            ]
        ]

        assert [resp.status_code for resp in answers] == [200] * 5
        assert [(event, fields["actor"]) for event, fields in written] == [
            ("audit.herdr.control", TEST_USERNAME),
            ("audit.herdr.agent_started", TEST_USERNAME),
            ("audit.herdr.created", TEST_USERNAME),
            ("audit.herdr.renamed", TEST_USERNAME),
            ("audit.herdr.closed", TEST_USERNAME),
        ]

        written.clear()
        relayed = await dispatch_herdr(herdr_app.state.herdr, "POST", RENAME, rename, None)

        assert relayed["status"] == 200
        assert [(event, fields["actor"]) for event, fields in written] == [
            ("audit.herdr.renamed", None)
        ]


class TestSwitchedOff:
    @pytest.fixture
    async def off_app(self, tmp_path):
        application = create_app(
            config=_config(tmp_path, herdr_fake=False, herdr_enabled=False)
        )
        yield application
        await application.state.session_manager.shutdown()

    async def test_routes_answer_503_and_no_capability_is_sent(
        self, off_app, relay_config, mock_session_manager, mock_event_bus,
        mock_broadcaster, mock_app_state,
    ):
        token = off_app.state.jwt_service.create_access_token(TEST_USERNAME)
        auth = {"Authorization": f"Bearer {token}"}
        mock_app_state.herdr = None
        mock_app_state.cli_launchers = {}
        mock_app_state.cli_models_warmup = None
        relay = _relay(
            mock_app_state, mock_session_manager, relay_config, mock_event_bus,
            mock_broadcaster,
        )

        async with AsyncClient(transport=ASGITransport(app=off_app), base_url="http://test") as c:
            snapshot = await c.get(HERDR, headers=auth)
            version = await c.get("/api/version", headers=auth)
        dispatched = await dispatch_herdr(None, "GET", HERDR, None, None)
        relayed = await relay._dispatch_proxy("GET", "/api/version", None, None, AsyncMock())

        assert off_app.state.herdr is None
        assert snapshot.status_code == 503
        assert snapshot.json()["detail"]["code"] == "herdr_unavailable"
        assert dispatched == {"status": 503, "body": snapshot.json()}
        assert "herdr" not in version.json()["capabilities"]
        assert "herdr" not in relayed["body"]["capabilities"]


class TestHerdrMode:
    @pytest.mark.parametrize(
        "fields,mode",
        [
            ({}, "real"),
            # A disposable test agent must not find the herdr servers of the
            # developer who runs it.
            ({"env": "test"}, "off"),
            ({"env": "test", "herdr_enabled": "1"}, "real"),
            ({"herdr_enabled": "0"}, "off"),
            ({"herdr_enabled": ""}, "real"),
            ({"env": "test", "herdr_fake": "1"}, "fake"),
            ({"herdr_enabled": "0", "herdr_fake": "1"}, "fake"),
        ],
    )
    def test_mode(self, tmp_path, monkeypatch, fields, mode):
        monkeypatch.delenv("MOBAIDE_HERDR_ENABLED", raising=False)
        monkeypatch.delenv("MOBAIDE_HERDR_FAKE", raising=False)

        config = _config(tmp_path, **{"herdr_fake": False, **fields})

        assert config.herdr_mode == mode

    @pytest.mark.parametrize("env,mode", [({}, "off"), ({"MOBAIDE_HERDR_ENABLED": "1"}, "real")])
    def test_windows_is_off_until_it_is_switched_on(self, tmp_path, monkeypatch, env, mode):
        # The named-pipe transport has not run on a Windows computer yet.
        monkeypatch.delenv("MOBAIDE_HERDR_ENABLED", raising=False)
        monkeypatch.delenv("MOBAIDE_HERDR_FAKE", raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        config = _config(tmp_path, herdr_fake=False)

        monkeypatch.setattr(sys, "platform", "win32")

        assert config.herdr_mode == mode


class TestVersionCapability:
    async def test_direct_and_relay_advertise_the_dashboard(
        self, http, headers, herdr_app, relay_config, mock_session_manager,
        mock_event_bus, mock_broadcaster, mock_app_state,
    ):
        mock_app_state.herdr = herdr_app.state.herdr
        mock_app_state.cli_launchers = {}
        mock_app_state.cli_models_warmup = None
        relay = _relay(
            mock_app_state, mock_session_manager, relay_config, mock_event_bus,
            mock_broadcaster,
        )

        direct = await http.get("/api/version", headers=headers)
        relayed = await relay._dispatch_proxy("GET", "/api/version", None, None, AsyncMock())

        assert direct.json()["capabilities"]["herdr"] == HERDR_CAPABILITIES == {"dashboard": True}
        assert relayed["body"]["capabilities"]["herdr"] == HERDR_CAPABILITIES


class TestConnectSnapshots:
    def test_events_websocket_sends_the_herdr_state_on_connect(self, herdr_app):
        token = herdr_app.state.jwt_service.create_access_token(TEST_USERNAME)

        with TestClient(herdr_app).websocket_connect(f"/ws/events?token={token}") as ws:
            frames = [ws.receive_json() for _ in range(5)]

        assert [f["type"] for f in frames] == [
            "sessions.changed", "projects.changed", "autoscan.list.changed",
            "voice.changed", "herdr.changed",
        ]
        assert frames[4]["payload"] == herdr_app.state.herdr.snapshot().model_dump(mode="json")
        assert frames[4]["payload"]["servers"][0]["server_id"] == SERVER_ID

    async def test_relay_snapshot_publishes_herdr_changed(
        self, herdr_app, relay_config, mock_session_manager, mock_broadcaster, mock_app_state,
    ):
        herdr = herdr_app.state.herdr
        bus = herdr_app.state.event_bus
        queue = bus.subscribe()
        mock_app_state.herdr = herdr
        mock_app_state.voice = None
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        relay = _relay(mock_app_state, mock_session_manager, relay_config, bus, mock_broadcaster)

        await relay._handle_control(
            json.dumps({"type": "request_events_snapshot"}).encode(), AsyncMock()
        )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        changed = [e.payload for e in events if e.type == "herdr.changed"]
        assert changed == [herdr.snapshot().model_dump(mode="json")]


def _proxy(request_id: str, method: str, path: str, body=None, query=None) -> bytes:
    return json.dumps(
        {"type": "proxy_request", "request_id": request_id, "method": method,
         "path": path, "body": body, "query": query}
    ).encode()


def _session_frames(ws, session_id: str) -> list[bytes]:
    """The session frames that went into the tunnel for one session."""
    out = []
    for call in ws.send.call_args_list:
        channel, sid, payload = decode(call[0][0])
        if channel == CH_SESSION_IO and sid == session_id:
            out.append(payload)
    return out


def _states(frames: list[bytes]) -> list[dict]:
    return [json.loads(frame[1:]) for frame in frames if frame[:1] == FRAME_STATE]


def _control_messages(ws) -> list[dict]:
    out = []
    for call in ws.send.call_args_list:
        channel, _sid, payload = decode(call[0][0])
        if channel == CH_CONTROL:
            out.append(json.loads(payload))
    return out


class TestRelayTunnel:
    @pytest.fixture
    async def tunnel(self, herdr_app, relay_config, mock_event_bus, mock_broadcaster,
                     mock_app_state):
        mock_app_state.herdr = herdr_app.state.herdr
        client = _relay(
            mock_app_state, herdr_app.state.session_manager, relay_config,
            mock_event_bus, mock_broadcaster,
        )
        ws = AsyncMock()
        client._ws = ws
        yield client, ws
        await client.stop()

    @pytest.fixture
    async def live_tunnel(self, herdr_app, relay_config):
        """A tunnel on the app's own state: the bridge and the session-removed
        hook share one set of fanouts, as in the running agent."""
        state = herdr_app.state
        client = RelayClient(
            config=relay_config,
            session_manager=state.session_manager,
            event_bus=state.event_bus,
            broadcaster=state.broadcaster,
            app_state=state,
        )
        ws = AsyncMock()
        client._ws = ws
        yield client, ws
        await client.stop()
        for fanout in list(state.fanouts.values()):
            fanout.stop()

    async def test_a_pane_opened_again_under_its_id_gets_a_working_bridge(
        self, live_tunnel, herdr_app
    ):
        # The idle sweep drops a view nobody watches, and the pane keeps its
        # session id. The bridge of the dropped view must end with it, or it
        # stands in for the new one and the second view stays empty.
        client, ws = live_tunnel
        herdr = herdr_app.state.herdr
        manager = herdr_app.state.session_manager
        session_id = await herdr.open_pane(**CLAUDE_PANE)
        attach = json.dumps({"type": "mobile_session_attach", "session_id": session_id}).encode()
        detach = json.dumps({"type": "mobile_session_detach", "session_id": session_id}).encode()

        def frames() -> list[bytes]:
            return _session_frames(ws, session_id)

        def painted() -> bool:
            return any(f[:1] == FRAME_OUTPUT and b"api refactor" in f for f in frames())

        await client._handle_control(attach, ws)
        await _wait_for(painted)
        await client._handle_control(detach, ws)

        await manager.release_session(session_id)

        await _wait_for(lambda: _states(frames())[-1]["status"] == "stopped")
        ws.send.reset_mock()

        assert await herdr.open_pane(**CLAUDE_PANE) == session_id
        await client._handle_control(attach, ws)
        await _wait_for(painted)

        # The pane session learns its grid after the handshake, so this state
        # frame can only come through the bridge.
        assert {
            "status": "running",
            "pane": {"mode": "watch", "cols": WATCH_GRID[0], "rows": WATCH_GRID[1]},
        } in _states(frames())

    async def test_viewer_counts_survive_a_tunnel_reconnect(self, live_tunnel, herdr_app):
        client, ws = live_tunnel
        session_id = await herdr_app.state.herdr.open_pane(**CLAUDE_PANE)
        session = await herdr_app.state.session_manager.get_session(session_id)
        # After a reconnect the relay sends one attach for all clients of a session.
        replay = json.dumps(
            {"type": "mobile_session_attach", "session_id": session_id, "count": 2}
        ).encode()
        detach = json.dumps({"type": "mobile_session_detach", "session_id": session_id}).encode()
        # A browser on the direct socket.
        session.viewer_attached()

        await client._handle_control(replay, ws)
        assert session.viewers == 3

        # The tunnel drops: nothing is known about its two clients, and the
        # browser still watches.
        client._reset_external_viewers()
        assert session.viewers == 1
        assert session.idle_since is None

        await client._handle_control(replay, ws)
        await client._handle_control(detach, ws)
        assert session.viewers == 2

    async def test_a_proxied_request_answers_like_the_rest_route(
        self, tunnel, http, headers
    ):
        client, ws = tunnel
        direct = await http.post(OPEN, headers=headers, json=CLAUDE_PANE)

        await asyncio.wait_for(
            client._handle_control(_proxy("r-1", "POST", OPEN, CLAUDE_PANE), ws), 1
        )
        await _wait_for(lambda: len(_control_messages(ws)) == 1)

        assert _control_messages(ws) == [
            {"type": "proxy_response", "request_id": "r-1", "status": 200,
             "body": direct.json()},
        ]

    async def test_attach_and_detach_count_the_viewers_of_a_pane_session(
        self, tunnel, herdr_app
    ):
        client, ws = tunnel
        session_id = await herdr_app.state.herdr.open_pane(**CLAUDE_PANE)
        session = await herdr_app.state.session_manager.get_session(session_id)
        attach = json.dumps({"type": "mobile_session_attach", "session_id": session_id})
        detach = json.dumps({"type": "mobile_session_detach", "session_id": session_id})

        await client._handle_control(attach.encode(), ws)
        await settle()

        # The first frame of the handshake carries the pane block.
        channel, sid, payload = decode(ws.send.call_args_list[0][0][0])
        assert (channel, sid, payload[:1]) == (CH_SESSION_IO, session_id, FRAME_STATE)
        assert json.loads(payload[1:])["pane"]["mode"] == "watch"
        assert session.viewers == 1

        await client._handle_control(detach.encode(), ws)

        assert session.viewers == 0
        assert session.idle_since is not None

    async def test_crossed_detach_and_attach_messages_lose_no_viewer(
        self, live_tunnel, herdr_app
    ):
        # Two relay clients watched a view that was dropped, and the pane is
        # open again under its id. Each client replaces its socket, and the
        # detach of the old one arrives first. The relay says how many
        # clients it has after each event; counted as steps, the first
        # detach is lost and the second takes a viewer that is there.
        client, ws = live_tunnel
        session_id = await herdr_app.state.herdr.open_pane(**CLAUDE_PANE)
        session = await herdr_app.state.session_manager.get_session(session_id)
        # A browser on the direct socket.
        session.viewer_attached()

        def message(kind: str, count: int | None = None) -> bytes:
            body = {"type": f"mobile_session_{kind}", "session_id": session_id}
            if count is not None:
                body["count"] = count
            return json.dumps(body).encode()

        for kind, count in (("detach", 1), ("attach", 2), ("detach", 1), ("attach", 2)):
            await client._handle_control(message(kind, count), ws)

        assert session.viewers == 3

        # A relay that sends no count (an older one): one step per message.
        await client._handle_control(message("detach"), ws)
        assert session.viewers == 2
        await client._handle_control(message("attach"), ws)
        assert session.viewers == 3

        # The last relay client left. The browser still watches.
        await client._handle_control(message("detach", 0), ws)
        assert session.viewers == 1
        assert session.idle_since is None


class TestAttentionUserEvent:
    def test_keeps_the_wire_fields_only(self):
        ctrl = _herdr_attention_user_event({**ATTENTION, "session_id": "s1", "extra": 1})

        assert ctrl == {"type": "user_event", "kind": "herdr.attention", "payload": ATTENTION}

    def test_agent_is_optional(self):
        payload = {k: v for k, v in ATTENTION.items() if k != "agent"}

        assert _herdr_attention_user_event({**payload, "agent": None})["payload"] == payload

    def test_a_long_title_is_cut_and_the_ids_are_not(self):
        # The relay drops an event with a longer display field, and a pane
        # title can be a terminal title of any length. A cut id would name
        # another pane.
        long_id = "w1:" + "p" * 300
        payload = {**ATTENTION, "pane_id": long_id, "title": "t" * 500, "agent": "a" * 500}

        slim = _herdr_attention_user_event(payload)["payload"]

        assert slim["title"] == "t" * HERDR_DISPLAY_MAX_CHARS
        assert slim["agent"] == "a" * HERDR_DISPLAY_MAX_CHARS
        assert slim["pane_id"] == long_id

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {**ATTENTION, "pane_id": ""},
            {**ATTENTION, "server_id": None},
            {**ATTENTION, "reason": 7},
            {k: v for k, v in ATTENTION.items() if k != "title"},
        ],
    )
    def test_malformed_payload_returns_none(self, payload):
        assert _herdr_attention_user_event(payload) is None

    async def test_bus_event_goes_out_as_events_frame_and_control_user_event(
        self, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
        mock_app_state,
    ):
        client = _relay(
            mock_app_state, mock_session_manager, relay_config, mock_event_bus,
            mock_broadcaster,
        )
        ws = AsyncMock()
        forward = asyncio.create_task(client._forward_events(ws))
        mock_event_bus.subscribe.return_value.put_nowait(
            AgentEvent(type="herdr.attention", payload=ATTENTION)
        )
        await _wait_for(lambda: ws.send.await_count >= 2)
        forward.cancel()
        await asyncio.gather(forward, return_exceptions=True)

        events_channel, _, events_payload = decode(ws.send.call_args_list[0][0][0])
        assert events_channel == CH_EVENTS
        assert json.loads(events_payload)["type"] == "herdr.attention"
        assert json.loads(events_payload)["payload"] == ATTENTION
        assert _control_messages(ws) == [
            {"type": "user_event", "kind": "herdr.attention", "payload": ATTENTION}
        ]


class TestStateFrame:
    async def test_a_pane_session_adds_its_pane_block(self):
        session = HerdrPaneSession("herdr-test", ScriptedLink(), AGENT_PANE)
        await session.start()
        session.viewer_attached()
        await settle()

        frame = _state_frame("running", session.pid, session)

        assert frame[:1] == FRAME_STATE
        assert json.loads(frame[1:]) == {
            "status": "running",
            "pane": {"mode": "watch", "cols": GRID[0], "rows": GRID[1]},
        }

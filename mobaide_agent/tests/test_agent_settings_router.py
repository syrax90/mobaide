"""``/api/agent/settings`` through the FastAPI app and the relay dispatcher,
which must answer identically, with a fake keep-awake backend."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from mobaide.agent.settings.keep_awake import KeepAwake, KeepAwakeError
from mobaide.agent.settings.models import AGENT_SETTINGS_CAPABILITIES
from mobaide.agent.settings.service import AgentSettingsService
from mobaide.relay.client import RelayClient
from mobaide.relay.envelope import CH_CONTROL, decode

from .conftest import TEST_USERNAME
from .keep_awake_support import FakeBackend

SETTINGS = "/api/agent/settings"
KEEP_AWAKE = "/api/agent/settings/keep_awake"


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "state" / "agent_settings.json"


@pytest.fixture
def settings_app(app, settings_file, backend):
    app.state.agent_settings = AgentSettingsService(
        settings_file, publish=app.state.event_bus.publish, keep_awake=KeepAwake(backend)
    )
    return app


@pytest.fixture
async def http(settings_app):
    async with AsyncClient(transport=ASGITransport(app=settings_app), base_url="http://test") as c:
        yield c


@pytest.fixture
def headers(settings_app) -> dict[str, str]:
    token = settings_app.state.jwt_service.create_access_token(TEST_USERNAME)
    return {"Authorization": f"Bearer {token}"}


def _relay(app_state, relay_config, session_manager, event_bus, broadcaster) -> RelayClient:
    return RelayClient(
        config=relay_config,
        session_manager=session_manager,
        event_bus=event_bus,
        broadcaster=broadcaster,
        app_state=app_state,
    )


@pytest.fixture
def relay(settings_app, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
          mock_app_state):
    mock_app_state.agent_settings = settings_app.state.agent_settings
    mock_app_state.voice = None
    mock_app_state.cli_launchers = {}
    mock_app_state.cli_models_warmup = None
    return _relay(mock_app_state, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster)


async def _assert_parity(http, headers, relay, method, url, body=None):
    resp = await http.request(method, url, headers=headers, json=body)
    dispatched = await relay._dispatch_agent_settings(method, url, body)
    assert dispatched == {"status": resp.status_code, "body": resp.json()}
    return resp


class TestRouting:
    @pytest.mark.parametrize(("method", "body"), [("GET", None), ("PUT", {"enabled": True})])
    async def test_settings_require_auth(self, http, backend, method, body):
        url = SETTINGS if method == "GET" else KEEP_AWAKE

        assert (await http.request(method, url, json=body)).status_code == 401
        assert backend.acquires == 0

    async def test_fresh_settings_are_off_on_both_transports(self, http, headers, relay):
        resp = await _assert_parity(http, headers, relay, "GET", SETTINGS)

        assert resp.status_code == 200
        assert resp.json() == {"keep_awake": {"enabled": False, "state": "off", "message": None}}

    async def test_keep_awake_on_then_off_on_both_transports(self, http, headers, relay, backend, settings_file):
        on = await _assert_parity(http, headers, relay, "PUT", KEEP_AWAKE, {"enabled": True})

        assert on.json()["keep_awake"] == {"enabled": True, "state": "active", "message": None}
        assert backend.held
        assert json.loads(settings_file.read_text(encoding="utf-8")) == {"keep_awake": True}
        assert (await _assert_parity(http, headers, relay, "GET", SETTINGS)).json() == on.json()

        off = await _assert_parity(http, headers, relay, "PUT", KEEP_AWAKE, {"enabled": False})

        assert off.json()["keep_awake"] == {"enabled": False, "state": "off", "message": None}
        assert not backend.held

    async def test_a_refused_lock_is_answered_200_failed_on_both_transports(self, http, headers, relay, backend):
        backend.refuse = KeepAwakeError("Failed to inhibit: Access denied")

        resp = await _assert_parity(http, headers, relay, "PUT", KEEP_AWAKE, {"enabled": True})

        assert resp.status_code == 200
        assert resp.json()["keep_awake"] == {
            "enabled": True, "state": "failed", "message": "Failed to inhibit: Access denied",
        }

    @pytest.mark.parametrize(
        "body",
        [{}, {"enabled": "true"}, {"enabled": 1}, {"enabled": None}],
        ids=["missing", "string", "number", "null"],
    )
    async def test_a_non_bool_choice_is_422_on_both_transports(
        self, http, headers, relay, backend, settings_file, body
    ):
        resp = await http.put(KEEP_AWAKE, headers=headers, json=body)
        dispatched = await relay._dispatch_agent_settings("PUT", KEEP_AWAKE, body)

        assert resp.status_code == dispatched["status"] == 422
        assert backend.acquires == 0
        assert not settings_file.exists()

    async def test_a_write_failure_is_500_on_both_transports(self, settings_app, http, headers, relay, tmp_path):
        (tmp_path / "not-a-dir").write_text("", encoding="utf-8")
        service = AgentSettingsService(
            tmp_path / "not-a-dir" / "agent_settings.json", keep_awake=KeepAwake(FakeBackend())
        )
        settings_app.state.agent_settings = service
        relay._app_state.agent_settings = service

        resp = await _assert_parity(http, headers, relay, "PUT", KEEP_AWAKE, {"enabled": True})

        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "storage_error"

    async def test_service_missing_is_503_on_both_transports(self, settings_app, http, headers, relay):
        settings_app.state.agent_settings = None
        relay._app_state.agent_settings = None

        resp = await _assert_parity(http, headers, relay, "GET", SETTINGS)

        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "settings_unavailable"

    @pytest.mark.parametrize(
        ("method", "path"),
        [("PUT", SETTINGS), ("GET", KEEP_AWAKE), ("PUT", f"{SETTINGS}/sleep_display")],
    )
    async def test_relay_answers_no_route_the_app_does_not_have(self, relay, backend, method, path):
        assert await relay._dispatch_agent_settings(method, path, {"enabled": True}) is None
        assert backend.acquires == 0

    async def test_settings_are_reachable_through_dispatch_proxy(self, relay):
        result = await relay._dispatch_proxy("GET", SETTINGS, None, None, AsyncMock())

        assert result["status"] == 200
        assert result["body"]["keep_awake"]["state"] == "off"


class TestVersionCapability:
    async def test_direct_and_relay_advertise_the_agent_settings_capability(self, http, headers, relay):
        direct = await http.get("/api/version", headers=headers)
        relayed = await relay._dispatch_proxy("GET", "/api/version", None, None, AsyncMock())

        assert direct.json()["capabilities"]["agent_settings"] == AGENT_SETTINGS_CAPABILITIES
        assert relayed["body"]["capabilities"]["agent_settings"] == AGENT_SETTINGS_CAPABILITIES


class TestConnectSnapshots:
    def test_events_websocket_sends_an_agent_settings_snapshot_on_connect(self, settings_app):
        token = settings_app.state.jwt_service.create_access_token(TEST_USERNAME)

        with TestClient(settings_app).websocket_connect(f"/ws/events?token={token}") as ws:
            frames = [ws.receive_json() for _ in range(5)]

        snapshot = frames[-1]
        assert snapshot["type"] == "agent_settings.changed"
        assert snapshot["payload"] == settings_app.state.agent_settings.status().model_dump(mode="json")

    async def test_relay_snapshot_publishes_agent_settings_changed(
        self, settings_app, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
        mock_app_state,
    ):
        service = settings_app.state.agent_settings
        await service.set_keep_awake(True)
        mock_app_state.agent_settings = service
        mock_app_state.voice = None
        mock_session_manager.list_sessions = MagicMock(return_value=[])
        client = _relay(mock_app_state, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster)

        await client._handle_control(json.dumps({"type": "request_events_snapshot"}).encode(), AsyncMock())

        calls = [c[0] for c in mock_event_bus.publish.call_args_list if c[0][0] == "agent_settings.changed"]
        assert calls == [
            ("agent_settings.changed", {"keep_awake": {"enabled": True, "state": "active", "message": None}}),
        ]


def _proxy(request_id: str, method: str, path: str, body: dict | None = None) -> bytes:
    return json.dumps(
        {"type": "proxy_request", "request_id": request_id, "method": method, "path": path, "body": body}
    ).encode()


def _responses(ws) -> list[dict]:
    out = []
    for call in ws.send.call_args_list:
        channel, _sid, payload = decode(call[0][0])
        if channel == CH_CONTROL:
            out.append(json.loads(payload))
    return out


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


class TestRelaySettingsDispatchIsOffTheReader:
    @pytest.fixture
    async def tunnel(self, settings_app, backend, relay_config, mock_session_manager, mock_event_bus,
                     mock_broadcaster, mock_app_state):
        backend.gate = asyncio.Event()
        mock_app_state.agent_settings = settings_app.state.agent_settings
        mock_app_state.voice = None
        client = _relay(mock_app_state, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster)
        ws = AsyncMock()
        client._ws = ws
        yield client, ws
        backend.gate.set()
        await client.stop()

    async def test_a_slow_keep_awake_write_does_not_hold_up_the_reader(self, tunnel, backend):
        client, ws = tunnel

        await asyncio.wait_for(
            client._handle_control(_proxy("r-put", "PUT", KEEP_AWAKE, {"enabled": True}), ws), 1
        )
        await _wait_for(lambda: backend.acquires == 1)
        await asyncio.wait_for(client._handle_control(_proxy("r-get", "GET", SETTINGS), ws), 1)
        await _wait_for(lambda: len(_responses(ws)) == 1)

        assert _responses(ws)[0]["request_id"] == "r-get"
        assert _responses(ws)[0]["status"] == 200

        backend.gate.set()
        await _wait_for(lambda: len(_responses(ws)) == 2)
        assert _responses(ws)[1] == {
            "type": "proxy_response",
            "request_id": "r-put",
            "status": 200,
            "body": {"keep_awake": {"enabled": True, "state": "active", "message": None}},
        }

    async def test_stop_cancels_an_in_flight_settings_request(self, tunnel, backend):
        client, ws = tunnel
        await client._handle_control(_proxy("r-put", "PUT", KEEP_AWAKE, {"enabled": True}), ws)
        await _wait_for(lambda: backend.acquires == 1)

        await client.stop()

        await _wait_for(lambda: backend.cancelled == 1)

    async def test_an_unknown_settings_route_is_answered_404(self, tunnel):
        client, ws = tunnel

        await client._handle_control(_proxy("r-1", "PATCH", KEEP_AWAKE, {"enabled": True}), ws)
        await _wait_for(lambda: len(_responses(ws)) == 1)

        assert _responses(ws)[0]["request_id"] == "r-1"
        assert _responses(ws)[0]["status"] == 404

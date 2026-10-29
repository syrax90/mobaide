"""End-to-end tests for device revocation reaching already-issued tokens.

JWT ``iat`` has one-second resolution, so the "fresh sign-in" tests issue
the old tokens on a clock one minute behind and move the watermark to half
a minute ago, rather than sleeping across a second boundary.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from types import SimpleNamespace

import jwt
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from central_server.auth import service as auth_service_module
from central_server.auth.dependencies import DEVICE_REVOKED_DETAIL
from central_server.relay import mobile_ws as mobile_ws_module
from central_server.relay import user_ws as user_ws_module
from central_server.relay.mobile_proxy import MobileProxy
from central_server.relay.routing import (
    WS_CLOSE_AGENT_TRANSIENT_OFFLINE,
    MobileClient,
    UserClient,
)
from tests.conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD

_DEV = "44444444-4444-4444-8444-444444444444"
_OTHER_DEV = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def proxy_app(app):
    """The conftest app plus the mobile proxy the lifespan would wire."""
    app.state.mobile_proxy = MobileProxy(app.state.routing)
    return app


@contextmanager
def _issued_a_minute_ago():
    """Mint every JWT inside the block with an ``iat`` one minute old."""
    past = time.time() - 60
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_service_module, "time", SimpleNamespace(time=lambda: past))
        yield


@contextmanager
def _rotated_a_minute_later():
    """Run every refresh inside the block on a clock one minute ahead, so its
    ``started`` second falls strictly after a watermark written just before."""
    future = time.time() + 60
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_service_module, "time", SimpleNamespace(time=lambda: future))
        yield


async def _move_revoke_to_half_a_minute_ago(db, device_id: str) -> int:
    watermark = int(time.time()) - 30
    await db.conn.execute(
        "UPDATE device_revocations SET tokens_valid_after = ? WHERE device_id = ?",
        (watermark, device_id),
    )
    await db.conn.commit()
    return watermark


async def _revoke_like_the_route(db, user_id: str) -> None:
    """The DB writes ``DELETE /api/devices/{id}`` makes for ``_DEV``."""
    await db.revoke_mobile_device(_DEV, user_id)
    await db.revoke_all_refresh_tokens_for_device(_DEV)
    await db.revoke_user_refresh_tokens_for_device(user_id, _DEV)


def _revoke_first(monkeypatch, db, method: str, user_id: str) -> None:
    """Land a device revoke right before ``db.<method>`` runs."""
    original = getattr(db, method)

    async def hooked(*args, **kwargs):
        await _revoke_like_the_route(db, user_id)
        return await original(*args, **kwargs)

    monkeypatch.setattr(db, method, hooked)


def _revoke_after_socket_auth(
    monkeypatch, module, auth_name: str, app, user_id: str
) -> list[int]:
    """Land a full device revoke — DB writes, then the route's socket sweep —
    after ``module.<auth_name>`` accepts the socket but before the handler
    registers it. Returns the sweep's closed-socket counts."""
    original = getattr(module, auth_name)
    swept: list[int] = []

    async def hooked(websocket):
        payload = await original(websocket)
        await _revoke_like_the_route(app.state.db, user_id)
        swept.append(await app.state.routing.disconnect_device(user_id, _DEV))
        return payload

    monkeypatch.setattr(module, auth_name, hooked)
    return swept


def _record_stored_tokens(monkeypatch, db, method: str) -> list[str]:
    """Collect every raw refresh token ``db.<method>`` stores."""
    original = getattr(db, method)
    stored: list[str] = []

    async def recording(*args, **kwargs):
        stored.append(kwargs["raw_token"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(db, method, recording)
    return stored


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


async def _sign_in(client, **extra) -> dict:
    resp = await client.post(
        "/api/auth/login",
        json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mint(client, headers: dict[str, str], agent_id: str, device_id: str):
    return await client.post(
        f"/api/agents/{agent_id}/token",
        headers=headers,
        json={"mobile_device_id": device_id, "mobile_device_name": "Pixel 8"},
    )


async def _proxy_status(client, agent_id: str, mobile_token: str) -> int:
    """401 = token rejected; 503 = token accepted, agent offline."""
    resp = await client.get(
        f"/proxy/{agent_id}/api/sessions", headers=_bearer(mobile_token)
    )
    return resp.status_code


def _ws_close_code(app, path: str) -> int:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with TestClient(app).websocket_connect(path) as ws:
            ws.receive_text()
    return excinfo.value.code


def _ws_frames_until_close(app, path: str) -> tuple[list[str], int, str]:
    """Every text frame the server sends, then its close code and reason."""
    frames: list[str] = []
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with TestClient(app).websocket_connect(path) as ws:
            while True:
                frames.append(ws.receive_text())
    return frames, excinfo.value.code, excinfo.value.reason


class _RecordingWs:
    """WebSocket fake that records every send/close in call order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def send_text(self, data: str) -> None:
        self.events.append(("text", data))

    async def close(self, code: int, reason: str) -> None:
        self.events.append(("close", (code, reason)))


_REVOKED_CLOSE = [
    ("text", json.dumps({"reason": "device_revoked"})),
    ("close", (4001, "device_revoked")),
]


class TestRevokeKillsMobileTokens:
    async def test_proxy_rejects_a_mobile_token_issued_before_the_revoke(
        self, client, proxy_app, api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        minted = await _mint(client, api_auth_headers, agent_id, _DEV)
        assert minted.status_code == 200
        access = minted.json()["access_token"]
        assert await _proxy_status(client, agent_id, access) == 503

        resp = await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        assert resp.status_code == 204
        assert await _proxy_status(client, agent_id, access) == 401

    async def test_another_devices_mobile_token_survives_the_revoke(
        self, client, proxy_app, api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        await _mint(client, api_auth_headers, agent_id, _DEV)
        kept = await _mint(client, api_auth_headers, agent_id, _OTHER_DEV)

        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        assert await _proxy_status(client, agent_id, kept.json()["access_token"]) == 503

    async def test_session_socket_closes_4001_for_a_mobile_token_from_before_the_revoke(
        self, client, app, api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        minted = await _mint(client, api_auth_headers, agent_id, _DEV)
        access = minted.json()["access_token"]
        path = f"/relay/ws/session/{agent_id}/s1?token={access}"
        # Before the revoke the token authenticates; only the offline agent
        # stops the socket.
        assert _ws_close_code(app, path) == WS_CLOSE_AGENT_TRANSIENT_OFFLINE
        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        code = _ws_close_code(app, path)

        assert code == 4001


class TestSignInBindsTheSession:
    @pytest.mark.parametrize("refreshable", [False, True])
    async def test_sign_in_with_a_device_id_puts_it_in_the_token(
        self, client, viewer_user, refreshable
    ):
        del viewer_user
        body = await _sign_in(client, device_id=_DEV, refreshable=refreshable)

        assert _claims(body["access_token"])["device_id"] == _DEV

    async def test_sign_in_without_a_device_id_stays_unbound(
        self, client, viewer_user
    ):
        del viewer_user
        body = await _sign_in(client)

        assert "device_id" not in _claims(body["access_token"])

    async def test_a_device_id_that_is_not_a_uuid_is_rejected(
        self, client, viewer_user
    ):
        del viewer_user
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": TEST_VIEWER_EMAIL,
                "password": TEST_VIEWER_PASSWORD,
                "device_id": "not-a-uuid",
            },
        )

        assert resp.status_code == 422

    async def test_refresh_rotation_keeps_the_device_binding(
        self, client, viewer_user
    ):
        del viewer_user
        first = await _sign_in(client, device_id=_DEV, refreshable=True)

        second = await client.post(
            "/api/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
        third = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": second.json()["refresh_token"]},
        )

        assert second.status_code == 200
        assert third.status_code == 200
        assert _claims(second.json()["access_token"])["device_id"] == _DEV
        assert _claims(third.json()["access_token"])["device_id"] == _DEV


class TestRevokeKillsBoundUserSessions:
    async def test_bound_session_is_401_after_the_revoke_while_an_unbound_one_works(
        self, client, registered_agent
    ):
        agent_id, _ = registered_agent
        bound = (await _sign_in(client, device_id=_DEV))["access_token"]
        unbound = (await _sign_in(client))["access_token"]
        assert (await _mint(client, _bearer(bound), agent_id, _DEV)).status_code == 200

        resp = await client.delete(f"/api/devices/{_DEV}", headers=_bearer(unbound))

        assert resp.status_code == 204
        assert (await client.get("/api/devices", headers=_bearer(bound))).status_code == 401
        assert (await client.get("/api/devices", headers=_bearer(unbound))).status_code == 200

    async def test_bound_user_socket_closes_4001_after_the_revoke(
        self, client, app, api_auth_headers, viewer_user
    ):
        bound = (await _sign_in(client, device_id=_DEV))["access_token"]
        await app.state.db.upsert_mobile_device(
            device_id=_DEV, user_id=viewer_user["id"], name="Pixel 8"
        )
        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        code = _ws_close_code(app, f"/ws/user-events?token={bound}")

        assert code == 4001

    async def test_revoke_ends_the_bound_refresh_session_only(
        self, client, app, api_auth_headers, viewer_user
    ):
        bound = await _sign_in(client, device_id=_DEV, refreshable=True)
        unbound = await _sign_in(client, refreshable=True)
        await app.state.db.upsert_mobile_device(
            device_id=_DEV, user_id=viewer_user["id"], name="Pixel 8"
        )

        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        bound_refresh = await client.post(
            "/api/auth/refresh", json={"refresh_token": bound["refresh_token"]}
        )
        unbound_refresh = await client.post(
            "/api/auth/refresh", json={"refresh_token": unbound["refresh_token"]}
        )
        assert bound_refresh.status_code == 401
        assert unbound_refresh.status_code == 200

    async def test_user_socket_records_the_tokens_device(
        self, client, app, viewer_user
    ):
        bound = (await _sign_in(client, device_id=_DEV))["access_token"]

        with TestClient(app).websocket_connect(f"/ws/user-events?token={bound}"):
            clients = app.state.routing.get_user_clients(viewer_user["id"])
            assert [c.token_device_id for c in clients] == [_DEV]


class TestMintingForARevokedDevice:
    async def test_a_session_from_before_the_revoke_cannot_bring_the_device_back(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        assert (await _mint(client, api_auth_headers, agent_id, _DEV)).status_code == 200
        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        resp = await _mint(client, api_auth_headers, agent_id, _DEV)

        assert resp.status_code == 403
        assert await app.state.db.list_user_devices(viewer_user["id"]) == []

    async def test_a_fresh_sign_in_reconnects_the_device_while_old_tokens_stay_dead(
        self, client, proxy_app, api_auth_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        with _issued_a_minute_ago():
            old_user = (await _sign_in(client, device_id=_DEV))["access_token"]
            old_mobile = (
                await _mint(client, _bearer(old_user), agent_id, _DEV)
            ).json()["access_token"]
        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)
        watermark = await _move_revoke_to_half_a_minute_ago(proxy_app.state.db, _DEV)

        new_user = (await _sign_in(client, device_id=_DEV))["access_token"]
        reminted = await _mint(client, _bearer(new_user), agent_id, _DEV)

        assert reminted.status_code == 200
        devices = await proxy_app.state.db.list_user_devices(viewer_user["id"])
        assert [d["device_id"] for d in devices] == [_DEV]
        # Re-registration clears ``revoked_at`` but never the watermark.
        assert await proxy_app.state.db.device_tokens_valid_after(_DEV) == watermark
        assert await _proxy_status(client, agent_id, reminted.json()["access_token"]) == 503
        assert await _proxy_status(client, agent_id, old_mobile) == 401
        old_user_resp = await client.get("/api/devices", headers=_bearer(old_user))
        assert old_user_resp.status_code == 401

    async def test_a_bound_session_cannot_mint_for_another_device(
        self, client, app, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        bound = (await _sign_in(client, device_id=_DEV))["access_token"]

        resp = await _mint(client, _bearer(bound), agent_id, _OTHER_DEV)

        assert resp.status_code == 403
        assert await app.state.db.list_user_devices(viewer_user["id"]) == []


class TestRevokeClosesLiveSockets:
    async def test_revoke_closes_the_devices_mobile_and_user_sockets(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        user_id = viewer_user["id"]
        routing = app.state.routing
        await app.state.db.upsert_mobile_device(
            device_id=_DEV, user_id=user_id, name="Pixel 8"
        )
        revoked_mobile = MobileClient(device_id=_DEV, websocket=_RecordingWs())
        kept_mobile = MobileClient(device_id=_OTHER_DEV, websocket=_RecordingWs())
        routing.register_mobile(agent_id, "session_io:s1", revoked_mobile)
        routing.register_mobile(agent_id, "session_io:s1", kept_mobile)
        revoked_user = UserClient(
            user_id=user_id,
            device_id="log-1",
            websocket=_RecordingWs(),
            token_device_id=_DEV,
        )
        kept_user = UserClient(user_id=user_id, device_id="log-2", websocket=_RecordingWs())
        routing.register_user_client(user_id, revoked_user)
        routing.register_user_client(user_id, kept_user)

        resp = await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)

        assert resp.status_code == 204
        assert revoked_mobile.websocket.events == _REVOKED_CLOSE
        assert revoked_user.websocket.events == _REVOKED_CLOSE
        assert kept_mobile.websocket.events == []
        assert kept_user.websocket.events == []
        assert routing.get_user_clients(user_id) == {kept_user}
        assert routing.get_mobile_clients(agent_id, "session_io:s1") == {kept_mobile}


class TestRevokeDuringRotation:
    async def test_user_refresh_is_refused_when_the_revoke_lands_before_the_store(
        self, client, app, viewer_user, monkeypatch
    ):
        db = app.state.db
        user_id = viewer_user["id"]
        session = await _sign_in(client, device_id=_DEV, refreshable=True)
        await db.upsert_mobile_device(device_id=_DEV, user_id=user_id, name="Pixel 8")
        stored = _record_stored_tokens(monkeypatch, db, "store_user_refresh_token")
        _revoke_first(monkeypatch, db, "store_user_refresh_token", user_id)

        resp = await client.post(
            "/api/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )

        assert resp.status_code == 401
        assert len(stored) == 1
        assert await db.validate_and_consume_user_refresh(stored[0]) is None
        assert await db.list_user_devices(user_id) == []

    async def test_mobile_refresh_is_refused_when_the_revoke_lands_before_the_store(
        self, client, app, api_auth_headers, viewer_user, registered_agent, monkeypatch
    ):
        db = app.state.db
        user_id = viewer_user["id"]
        agent_id, _ = registered_agent
        minted = await _mint(client, api_auth_headers, agent_id, _DEV)
        stored = _record_stored_tokens(monkeypatch, db, "store_refresh_token")
        _revoke_first(monkeypatch, db, "store_refresh_token", user_id)

        resp = await client.post(
            "/api/mobile/refresh",
            json={"refresh_token": minted.json()["refresh_token"]},
        )

        assert resp.status_code == 401
        assert len(stored) == 1
        assert await db.validate_and_consume_refresh(stored[0]) is None
        assert await db.list_user_devices(user_id) == []

    @pytest.mark.parametrize(
        "revoke_before", ["upsert_mobile_device", "store_refresh_token"]
    )
    async def test_mint_is_refused_when_the_revoke_lands_mid_mint(
        self,
        client,
        app,
        api_auth_headers,
        viewer_user,
        registered_agent,
        monkeypatch,
        revoke_before,
    ):
        db = app.state.db
        user_id = viewer_user["id"]
        agent_id, _ = registered_agent
        assert (await _mint(client, api_auth_headers, agent_id, _DEV)).status_code == 200
        stored = _record_stored_tokens(monkeypatch, db, "store_refresh_token")
        _revoke_first(monkeypatch, db, revoke_before, user_id)

        resp = await _mint(client, api_auth_headers, agent_id, _DEV)

        assert resp.status_code == 403
        assert resp.json()["detail"] == DEVICE_REVOKED_DETAIL
        assert len(stored) == 1
        assert await db.validate_and_consume_refresh(stored[0]) is None
        # A revoke before the upsert had its revoked_at cleared by it.
        assert await db.list_user_devices(user_id) == []

    async def test_a_revoke_from_before_a_fresh_sign_in_does_not_stop_rotation(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        db = app.state.db
        agent_id, _ = registered_agent
        await db.upsert_mobile_device(
            device_id=_DEV, user_id=viewer_user["id"], name="Pixel 8"
        )
        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)
        await _move_revoke_to_half_a_minute_ago(db, _DEV)
        session = await _sign_in(client, device_id=_DEV, refreshable=True)
        minted = await _mint(client, _bearer(session["access_token"]), agent_id, _DEV)
        assert minted.status_code == 200

        user_refresh = await client.post(
            "/api/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )
        mobile_refresh = await client.post(
            "/api/mobile/refresh",
            json={"refresh_token": minted.json()["refresh_token"]},
        )

        assert user_refresh.status_code == 200
        assert mobile_refresh.status_code == 200

    async def test_refresh_is_refused_when_the_watermark_predates_it_but_the_rows_are_live(
        self, client, app, api_auth_headers, viewer_user, registered_agent, monkeypatch
    ):
        db = app.state.db
        user_id = viewer_user["id"]
        agent_id, _ = registered_agent
        session = await _sign_in(client, device_id=_DEV, refreshable=True)
        minted = await _mint(client, api_auth_headers, agent_id, _DEV)
        stored_user = _record_stored_tokens(monkeypatch, db, "store_user_refresh_token")
        stored_mobile = _record_stored_tokens(monkeypatch, db, "store_refresh_token")
        # The revoke's watermark is written; its bulk refresh-token revokes
        # have not run yet.
        assert await db.revoke_mobile_device(_DEV, user_id) is True

        with _rotated_a_minute_later():
            user_refresh = await client.post(
                "/api/auth/refresh", json={"refresh_token": session["refresh_token"]}
            )
            mobile_refresh = await client.post(
                "/api/mobile/refresh",
                json={"refresh_token": minted.json()["refresh_token"]},
            )

        assert user_refresh.status_code == 401
        assert mobile_refresh.status_code == 401
        assert stored_user == []
        assert stored_mobile == []


class TestRevokeDuringSocketSetup:
    """A revoke whose socket sweep runs between authentication and
    registration finds nothing to close; the handler itself must catch it.
    The timeout turns a socket left open by a regression into a failure."""

    @pytest.mark.timeout(10)
    async def test_bound_user_socket_closes_4001_when_the_revoke_lands_mid_setup(
        self, client, app, viewer_user, monkeypatch
    ):
        user_id = viewer_user["id"]
        bound = (await _sign_in(client, device_id=_DEV))["access_token"]
        await app.state.db.upsert_mobile_device(
            device_id=_DEV, user_id=user_id, name="Pixel 8"
        )
        swept = _revoke_after_socket_auth(
            monkeypatch, user_ws_module, "ws_authenticate_user", app, user_id
        )

        frames, code, reason = _ws_frames_until_close(
            app, f"/ws/user-events?token={bound}"
        )

        assert swept == [0]
        assert frames == [json.dumps({"reason": "device_revoked"})]
        assert (code, reason) == (4001, "device_revoked")
        assert app.state.routing.get_user_clients(user_id) == set()

    @pytest.mark.timeout(10)
    @pytest.mark.parametrize(
        "path",
        [
            "/relay/ws/session/{agent_id}/s1",
            "/relay/ws/sessions/{agent_id}",
            "/relay/ws/events/{agent_id}",
            "/relay/ws/autoscan/{agent_id}",
        ],
    )
    async def test_mobile_socket_closes_4001_when_the_revoke_lands_mid_setup(
        self,
        client,
        app,
        api_auth_headers,
        viewer_user,
        registered_agent,
        monkeypatch,
        path,
    ):
        user_id = viewer_user["id"]
        agent_id, _ = registered_agent
        minted = await _mint(client, api_auth_headers, agent_id, _DEV)
        access = minted.json()["access_token"]
        app.state.routing.register_agent(agent_id, _RecordingWs())
        swept = _revoke_after_socket_auth(
            monkeypatch, mobile_ws_module, "ws_authenticate_mobile", app, user_id
        )

        frames, code, reason = _ws_frames_until_close(
            app, f"{path.format(agent_id=agent_id)}?token={access}"
        )

        assert swept == [0]
        assert frames == [json.dumps({"reason": "device_revoked"})]
        assert (code, reason) == (4001, "device_revoked")
        assert app.state.routing.get_all_mobile_for_agent(agent_id) == []


class TestRevokeOutlivesTheDeviceRow:
    async def test_deleting_a_later_owner_does_not_revive_the_revoked_tokens(
        self, client, proxy_app, api_auth_headers, viewer_user, registered_agent
    ):
        db = proxy_app.state.db
        agent_id, _ = registered_agent
        with _issued_a_minute_ago():
            old_user = (await _sign_in(client, device_id=_DEV))["access_token"]
            old_mobile = (
                await _mint(client, _bearer(old_user), agent_id, _DEV)
            ).json()["access_token"]
        await client.delete(f"/api/devices/{_DEV}", headers=api_auth_headers)
        watermark = await _move_revoke_to_half_a_minute_ago(db, _DEV)
        auth_service = proxy_app.state.auth_service
        await auth_service.register_user("second@test.local", "second-pw123")
        second_uid, _role, second_token, _scheduled = await auth_service.login_user(
            "second@test.local", "second-pw123"
        )
        second_agent_id, _ = await db.register_agent("second-agent", second_uid)
        handover = await _mint(client, _bearer(second_token), second_agent_id, _DEV)
        assert handover.status_code == 200
        assert [d["device_id"] for d in await db.list_user_devices(second_uid)] == [_DEV]

        await db.delete_user(second_uid)

        assert await db.device_tokens_valid_after(_DEV) == watermark
        assert await _proxy_status(client, agent_id, old_mobile) == 401
        old_user_resp = await client.get("/api/devices", headers=_bearer(old_user))
        assert old_user_resp.status_code == 401

"""End-to-end tests for the ``mobile_device_id`` flow on
``POST /api/agents/{agent_id}/token``.

The request body carries the client-generated ``mobile_device_id`` and
``mobile_device_name``; the issued JWT's ``sub`` claim is the device id,
and ``user_id`` is a separate claim.

These tests pin:

* The JWT shape: ``sub == mobile_device_id``, ``user_id == owner``,
  ``agent_id`` populated.
* Two distinct device ids for the same agent produce two distinct
  refresh-token rows (each can independently be revoked).
* First-mint upserts a row into ``mobile_devices`` with the provided
  name so the device appears on the user's "My devices" screen.
* Cross-tenant collision: user B reusing user A's device id is
  rejected with HTTP 409.
"""
from __future__ import annotations

import uuid


def _uuid() -> str:
    """Return a fresh canonical 36-char UUIDv4 string."""
    return str(uuid.uuid4())


class TestMobileTokenUsesDeviceId:
    async def test_jwt_sub_is_device_id_and_user_id_claim_is_account_id(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        dev_a = _uuid()

        resp = await client.post(
            f"/api/agents/{agent_id}/token",
            headers=api_auth_headers,
            json={
                "mobile_device_id": dev_a,
                "mobile_device_name": "Pixel 8",
            },
        )

        assert resp.status_code == 200
        access_token = resp.json()["access_token"]
        payload = app.state.auth_service.decode_mobile_token(access_token)
        # Headline contract: sub is the per-install device id, not the
        # user id. Confusing the two re-introduces the conflation bug.
        assert payload.sub == dev_a
        assert payload.user_id == viewer_user["id"]
        assert payload.agent_id == agent_id
        assert payload.token_type == "mobile_access"

    async def test_two_distinct_device_ids_yield_two_distinct_refresh_rows(
        self, client, app, api_auth_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        dev_a = _uuid()
        dev_b = _uuid()

        resp_a = await client.post(
            f"/api/agents/{agent_id}/token",
            headers=api_auth_headers,
            json={"mobile_device_id": dev_a, "mobile_device_name": "Phone A"},
        )
        resp_b = await client.post(
            f"/api/agents/{agent_id}/token",
            headers=api_auth_headers,
            json={"mobile_device_id": dev_b, "mobile_device_name": "Phone B"},
        )

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        refresh_a = resp_a.json()["refresh_token"]
        refresh_b = resp_b.json()["refresh_token"]
        assert refresh_a != refresh_b

        cursor = await app.state.db.conn.execute(
            "SELECT mobile_device_id, agent_id FROM mobile_refresh_tokens "
            "WHERE agent_id = ? AND revoked_at IS NULL "
            "ORDER BY mobile_device_id",
            (agent_id,),
        )
        rows = await cursor.fetchall()
        device_ids = sorted(r[0] for r in rows)
        # Two devices for the same agent -> two refresh-token rows scoped
        # by device id, so revoking one cannot sign the other out.
        assert device_ids == sorted([dev_a, dev_b])

    async def test_first_mint_upserts_mobile_device_with_name(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        # The viewer has no devices yet (the registered_agent fixture
        # only registers the agent, not a paired device).
        assert await app.state.db.list_user_devices(viewer_user["id"]) == []
        dev_id = _uuid()

        resp = await client.post(
            f"/api/agents/{agent_id}/token",
            headers=api_auth_headers,
            json={"mobile_device_id": dev_id, "mobile_device_name": "Pixel 9 Pro"},
        )

        assert resp.status_code == 200
        devices = await app.state.db.list_user_devices(viewer_user["id"])
        assert len(devices) == 1
        assert devices[0]["device_id"] == dev_id
        assert devices[0]["name"] == "Pixel 9 Pro"

    async def test_repeat_mint_for_same_device_is_idempotent(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        # Re-pairing the same device must not create duplicate rows in
        # ``mobile_devices`` (upsert idempotency). The endpoint can be
        # called many times per session as the mobile client re-mints
        # access tokens.
        agent_id, _ = registered_agent
        dev_id = _uuid()

        for _ in range(3):
            resp = await client.post(
                f"/api/agents/{agent_id}/token",
                headers=api_auth_headers,
                json={"mobile_device_id": dev_id, "mobile_device_name": "Idempotent"},
            )
            assert resp.status_code == 200

        devices = await app.state.db.list_user_devices(viewer_user["id"])
        device_ids = [d["device_id"] for d in devices]
        assert device_ids.count(dev_id) == 1


class TestMobileTokenCrossTenant:
    """A user submitting another user's ``device_id`` via
    ``POST /api/agents/{agent_id}/token`` must be rejected with HTTP 409
    and must NOT mutate the other user's device row or create a new
    refresh-token row that the legitimate owner could later revoke
    (which would deny-of-service the attacker — but more importantly,
    a malicious caller in the reverse direction must not pollute the
    legitimate owner's mobile_devices listing or rename their device).
    """

    async def test_other_users_device_id_returns_409(
        self, client, app, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        dev_id = _uuid()

        # First, the legitimate owner registers the device.
        owner_uid, _role, owner_token, _scheduled = await app.state.auth_service.login_user(
            "viewer@test.local", "viewerpass123"
        )
        del owner_uid, _role, _scheduled
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        first = await client.post(
            f"/api/agents/{agent_id}/token",
            headers=owner_headers,
            json={"mobile_device_id": dev_id, "mobile_device_name": "owner phone"},
        )
        assert first.status_code == 200

        # Now create a second user with their own agent.
        await app.state.auth_service.register_user(
            "attacker@test.local", "attacker-pw123"
        )
        att_uid, _role, att_token, _scheduled = await app.state.auth_service.login_user(
            "attacker@test.local", "attacker-pw123"
        )
        del _role, _scheduled
        att_headers = {"Authorization": f"Bearer {att_token}"}
        att_agent_id, _ = await app.state.db.register_agent("att-agent", att_uid)

        # Attacker tries to use the victim's device id.
        resp = await client.post(
            f"/api/agents/{att_agent_id}/token",
            headers=att_headers,
            json={
                "mobile_device_id": dev_id,
                "mobile_device_name": "hijacked",
            },
        )

        assert resp.status_code == 409
        # The victim's device row is untouched: still owned by the
        # viewer, name unchanged, not revoked.
        victim_devices = await app.state.db.list_user_devices(viewer_user["id"])
        assert [d["name"] for d in victim_devices] == ["owner phone"]
        # The attacker has no devices (the rejected upsert did not create one).
        assert await app.state.db.list_user_devices(att_uid) == []
        # And no refresh-token row was created against the attacker's
        # agent with the victim's device id.
        cursor = await app.state.db.conn.execute(
            "SELECT 1 FROM mobile_refresh_tokens "
            "WHERE agent_id = ? AND mobile_device_id = ?",
            (att_agent_id, dev_id),
        )
        assert await cursor.fetchone() is None

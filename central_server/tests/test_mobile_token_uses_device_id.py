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
* Cross-tenant handover: user B presenting user A's device id takes the
  device over (last-login-wins) and A's refresh tokens for it die.
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


class TestMobileTokenCrossTenant:
    """A user submitting another user's ``device_id`` via
    ``POST /api/agents/{agent_id}/token`` takes the device over
    (last-login-wins): the mint succeeds, the row moves to the caller
    under the caller's name, and every live refresh token the previous
    owner held for the device is revoked. Agent ownership is untouched —
    the caller can still only mint for agents they own.
    """

    async def test_other_users_device_id_hands_device_over(
        self, client, app, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        dev_id = _uuid()

        # The first account registers the device and holds a live
        # refresh token for it.
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
        prior_refresh = first.json()["refresh_token"]

        # A second account (same physical device, new sign-in) mints
        # against its own agent with the same device id.
        await app.state.auth_service.register_user(
            "second@test.local", "second-pw123"
        )
        second_uid, _role, second_token, _scheduled = await app.state.auth_service.login_user(
            "second@test.local", "second-pw123"
        )
        del _role, _scheduled
        second_headers = {"Authorization": f"Bearer {second_token}"}
        second_agent_id, _ = await app.state.db.register_agent(
            "second-agent", second_uid
        )

        resp = await client.post(
            f"/api/agents/{second_agent_id}/token",
            headers=second_headers,
            json={
                "mobile_device_id": dev_id,
                "mobile_device_name": "second phone",
            },
        )

        assert resp.status_code == 200
        # The device moved to the caller under the caller's name …
        second_devices = await app.state.db.list_user_devices(second_uid)
        assert [(d["device_id"], d["name"]) for d in second_devices] == [
            (dev_id, "second phone")
        ]
        # … and left the previous owner's list.
        assert await app.state.db.list_user_devices(viewer_user["id"]) == []
        # The previous owner's refresh token died with the handover.
        assert await app.state.db.validate_and_consume_refresh(
            prior_refresh
        ) is None
        # The caller's freshly minted refresh token works (stored after
        # the handover's revoke sweep, so it survives it).
        assert await app.state.db.validate_and_consume_refresh(
            resp.json()["refresh_token"]
        ) == (dev_id, second_agent_id)

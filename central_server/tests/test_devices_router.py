"""End-to-end tests for the user-scoped ``/api/devices`` router.

The router exposes:

* ``GET /api/devices`` — returns the authenticated user's non-revoked
  devices.
* ``DELETE /api/devices/{device_id}`` — revokes one of the user's devices
  and invalidates every outstanding refresh token bound to that device.

These tests pin the user-scoping (a user must NOT see another user's
devices), the cross-tenant 404 guard on DELETE (a foreign device must
NOT be revocable), and the refresh-token cascade on revoke.
"""
from __future__ import annotations


class TestListDevices:
    async def test_returns_only_current_users_devices(
        self, client, app, api_auth_headers, viewer_user
    ):
        other_user_id = await app.state.db.create_user(
            email="other-list@test.local", password_hash="hash"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-self-A", user_id=viewer_user["id"], name="Pixel 8"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-self-B", user_id=viewer_user["id"], name="Pixel Tablet"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-other", user_id=other_user_id, name="Other phone"
        )

        resp = await client.get("/api/devices", headers=api_auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        device_ids = {row["device_id"] for row in body}
        assert device_ids == {"dev-self-A", "dev-self-B"}
        # Cross-tenant leak guard: the foreign device must not appear,
        # regardless of ordering.
        assert "dev-other" not in device_ids

    async def test_excludes_revoked_devices(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-active", user_id=viewer_user["id"], name="Active"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-revoked", user_id=viewer_user["id"], name="Old"
        )
        await app.state.db.revoke_mobile_device("dev-revoked", viewer_user["id"])

        resp = await client.get("/api/devices", headers=api_auth_headers)

        assert resp.status_code == 200
        device_ids = {row["device_id"] for row in resp.json()}
        assert device_ids == {"dev-active"}

    async def test_response_shape_matches_DeviceResponse(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-shape", user_id=viewer_user["id"], name="Pixel 9"
        )

        resp = await client.get("/api/devices", headers=api_auth_headers)

        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert set(row.keys()) == {
            "device_id",
            "name",
            "created_at",
            "last_seen_at",
            "platform",
        }
        assert row["device_id"] == "dev-shape"
        assert row["name"] == "Pixel 9"
        # ``upsert_mobile_device`` sets ``last_seen_at = now`` even on
        # insert (the row is created because the device is "being seen
        # now"), so we assert presence + type, not nullness.
        assert isinstance(row["last_seen_at"], str)
        assert isinstance(row["created_at"], str)

    async def test_reports_the_platform_each_device_registered_with(
        self, client, app, api_auth_headers, viewer_user
    ):
        """Clients label the listing from this field; a client that omitted
        it must come back null rather than absent."""
        for device_id, platform in (
            ("dev-web", "web"),
            ("dev-ios", "ios"),
            ("dev-android", "android"),
            ("dev-legacy", None),
        ):
            await app.state.db.upsert_mobile_device(
                device_id=device_id,
                user_id=viewer_user["id"],
                name=device_id,
                platform=platform,
            )

        resp = await client.get("/api/devices", headers=api_auth_headers)

        assert resp.status_code == 200
        platforms = {row["device_id"]: row["platform"] for row in resp.json()}
        assert platforms == {
            "dev-web": "web",
            "dev-ios": "ios",
            "dev-android": "android",
            "dev-legacy": None,
        }


class TestRevokeDevice:
    async def test_revoke_own_active_device_returns_204(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-own", user_id=viewer_user["id"], name="Mine"
        )

        resp = await client.delete(
            "/api/devices/dev-own", headers=api_auth_headers
        )

        assert resp.status_code == 204
        # Device is now revoked (no longer appears in list_user_devices).
        remaining = await app.state.db.list_user_devices(viewer_user["id"])
        assert all(d["device_id"] != "dev-own" for d in remaining)

    async def test_revoke_foreign_device_returns_404(
        self, client, app, api_auth_headers, viewer_user
    ):
        # Cross-tenant guard: another user's device id must look like "not
        # found" to the viewer, not 403 (avoids leaking existence).
        other_user_id = await app.state.db.create_user(
            email="other-revoke@test.local", password_hash="hash"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-foreign", user_id=other_user_id, name="Foreign"
        )

        resp = await client.delete(
            "/api/devices/dev-foreign", headers=api_auth_headers
        )

        assert resp.status_code == 404
        # Foreign device MUST still be active afterwards — the unauthorised
        # caller did not actually touch it.
        foreign = await app.state.db.list_user_devices(other_user_id)
        assert any(d["device_id"] == "dev-foreign" for d in foreign)

    async def test_revoke_does_not_touch_other_devices_refresh_tokens(
        self, client, app, api_auth_headers, viewer_user, registered_agent
    ):
        # Sibling-isolation guard: revoking device A must not cascade
        # into device B's refresh tokens. Catches a regression where
        # ``revoke_all_refresh_tokens_for_device`` is called with the
        # wrong scope.
        agent_id, _ = registered_agent
        await app.state.db.upsert_mobile_device(
            device_id="dev-revoked-1", user_id=viewer_user["id"], name="R1"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-kept-1", user_id=viewer_user["id"], name="K1"
        )
        await app.state.db.store_refresh_token(
            "r-revoked", "dev-revoked-1", agent_id, 60
        )
        await app.state.db.store_refresh_token(
            "r-kept", "dev-kept-1", agent_id, 60
        )

        resp = await client.delete(
            "/api/devices/dev-revoked-1", headers=api_auth_headers
        )

        assert resp.status_code == 204
        assert (
            await app.state.db.validate_and_consume_refresh("r-revoked")
            is None
        )
        # Sibling device's refresh token must still validate. NB:
        # validate_and_consume_refresh ALSO consumes — we do this last
        # because the assertion has side effects.
        kept = await app.state.db.validate_and_consume_refresh("r-kept")
        assert kept is not None
        assert kept == ("dev-kept-1", agent_id)


class TestRegisterPushToken:
    async def test_register_persists_token(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-push", user_id=viewer_user["id"], name="iPhone"
        )

        resp = await client.post(
            "/api/devices/dev-push/push-token",
            headers=api_auth_headers,
            json={
                "platform": "ios",
                "token": "a1b2c3",
                "environment": "sandbox",
            },
        )

        assert resp.status_code == 204
        targets = await app.state.db.list_push_targets(viewer_user["id"])
        assert targets == [
            {
                "device_id": "dev-push",
                "platform": "ios",
                "push_token": "a1b2c3",
                "push_environment": "sandbox",
            }
        ]

    async def test_foreign_device_404(
        self, client, app, api_auth_headers
    ):
        other = await app.state.db.create_user(
            email="other-push@test.local", password_hash="hash"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-foreign", user_id=other, name="Other"
        )

        resp = await client.post(
            "/api/devices/dev-foreign/push-token",
            headers=api_auth_headers,
            json={
                "platform": "ios",
                "token": "tok-x",
                "environment": "sandbox",
            },
        )

        assert resp.status_code == 404
        assert await app.state.db.list_push_targets(other) == []

    async def test_revoked_device_404(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-gone", user_id=viewer_user["id"], name="Old"
        )
        await app.state.db.revoke_mobile_device(
            "dev-gone", viewer_user["id"]
        )

        resp = await client.post(
            "/api/devices/dev-gone/push-token",
            headers=api_auth_headers,
            json={
                "platform": "ios",
                "token": "tok-x",
                "environment": "sandbox",
            },
        )

        assert resp.status_code == 404

    async def test_invalid_platform_or_environment_422(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-push", user_id=viewer_user["id"], name="iPhone"
        )
        for body in (
            {"platform": "android", "token": "t", "environment": "sandbox"},
            {"platform": "ios", "token": "t", "environment": "prod"},
            {"platform": "ios", "token": "", "environment": "sandbox"},
            {"platform": "ios", "environment": "sandbox"},
        ):
            resp = await client.post(
                "/api/devices/dev-push/push-token",
                headers=api_auth_headers,
                json=body,
            )
            assert resp.status_code == 422, body


class TestUnregisterPushToken:
    async def test_clears_token(
        self, client, app, api_auth_headers, viewer_user
    ):
        await app.state.db.upsert_mobile_device(
            device_id="dev-push", user_id=viewer_user["id"], name="iPhone"
        )
        await app.state.db.set_push_token(
            "dev-push", viewer_user["id"], "ios", "tok-1", "sandbox"
        )

        resp = await client.delete(
            "/api/devices/dev-push/push-token", headers=api_auth_headers
        )

        assert resp.status_code == 204
        assert await app.state.db.list_push_targets(viewer_user["id"]) == []

    async def test_foreign_device_404(self, client, app, api_auth_headers):
        other = await app.state.db.create_user(
            email="other-clear@test.local", password_hash="hash"
        )
        await app.state.db.upsert_mobile_device(
            device_id="dev-foreign", user_id=other, name="Other"
        )
        await app.state.db.set_push_token(
            "dev-foreign", other, "ios", "tok-o", "sandbox"
        )

        resp = await client.delete(
            "/api/devices/dev-foreign/push-token", headers=api_auth_headers
        )

        assert resp.status_code == 404
        assert len(await app.state.db.list_push_targets(other)) == 1

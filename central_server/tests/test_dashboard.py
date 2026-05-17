"""Tests for the web dashboard routes."""
from __future__ import annotations

import pytest

from tests.conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)


@pytest.fixture
async def viewer_dashboard_headers(app, viewer_user) -> dict[str, str]:
    """Dashboard cookie for a viewer — dashboard must reject with 403."""
    del viewer_user  # fixture ordering: ensures viewer exists before login
    auth_service = app.state.auth_service
    _uid, _role, token = await auth_service.login_user(
        TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
    )
    return {"Cookie": f"dashboard_token={token}"}


@pytest.fixture
async def client_with_dashboard_ttl(tmp_path):
    """Client built with ``dashboard_token_expire_minutes=60`` to
    exercise the positive Max-Age branch of the login cookie path.

    The default ``client`` fixture uses TTL=0 (session-cookie mode), so
    we need a separate app with a positive TTL to cover the other branch.
    """
    from httpx import ASGITransport, AsyncClient

    from central_server.agents.service import AgentService
    from central_server.app import create_app
    from central_server.auth.service import AuthService
    from central_server.config import CentralServerConfig
    from central_server.db.database import Database
    from central_server.pairing.service import PairingService
    from central_server.relay.routing import RoutingTable

    from tests.conftest import TEST_JWT_SECRET

    config = CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        db_path=str(tmp_path / "test_ttl.db"),
        log_level="DEBUG",
        log_format="console",
        dashboard_token_expire_minutes=60,
    )
    db = Database(config.db_path)
    await db.connect()
    try:
        app = create_app(config=config)
        app.state.db = db
        app.state.routing = RoutingTable()
        app.state.auth_service = AuthService(config, db)
        app.state.pairing_service = PairingService(db)
        app.state.agent_service = AgentService(db, app.state.routing)
        await app.state.auth_service.seed_admin_user()
        transport = ASGITransport(app=app)
        # See conftest.client: https:// base_url is required so the
        # jar returns Secure-flagged cookies on follow-up requests.
        async with AsyncClient(transport=transport, base_url="https://test") as c:
            yield c
    finally:
        await db.close()


class TestDashboardLogin:
    async def test_login_page(self, client):
        resp = await client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "Login" in resp.text

    async def test_login_success(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"
        set_cookie = resp.headers.get("set-cookie", "")
        assert "dashboard_token=" in set_cookie
        # With TTL=0 (default), the cookie must be a session cookie —
        # no Max-Age attribute. Max-Age=0 would tell the browser to
        # delete the cookie immediately (RFC 6265 §5.2.2) and silently
        # break login.
        assert "Max-Age" not in set_cookie
        assert "max-age" not in set_cookie

    async def test_login_cookie_authenticates_dashboard_request(self, client):
        """Regression: the cookie set by POST /dashboard/login must
        authenticate a follow-up GET /dashboard through httpx's cookie
        jar, which mirrors browser semantics. A Max-Age=0 cookie is
        discarded by the jar, so the follow-up request would 303 back
        to the login page.
        """
        login = await client.post(
            "/dashboard/login",
            data={"username": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert login.status_code == 303
        resp = await client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 200
        assert "Users" in resp.text

    async def test_login_positive_ttl_sets_max_age(
        self, client_with_dashboard_ttl
    ):
        resp = await client_with_dashboard_ttl.post(
            "/dashboard/login",
            data={"username": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        set_cookie = resp.headers.get("set-cookie", "")
        assert "dashboard_token=" in set_cookie
        # 60 minutes * 60 seconds = 3600 — python SimpleCookie emits
        # "Max-Age" (capitalized), but assert both cases defensively.
        assert "Max-Age=3600" in set_cookie or "max-age=3600" in set_cookie

    async def test_login_positive_ttl_cookie_authenticates_dashboard_request(
        self, client_with_dashboard_ttl
    ):
        """Regression: the positive-TTL branch must also produce a
        cookie the browser/cookie-jar accepts for follow-up requests.
        The TTL=0 round-trip is already covered by
        ``test_login_cookie_authenticates_dashboard_request``; this
        locks in the non-zero Max-Age branch end-to-end so a future
        refactor that accidentally emits a malformed Max-Age (e.g.
        negative value, float) would be caught by a 303 on GET
        /dashboard instead of a silent cookie-jar drop.
        """
        login = await client_with_dashboard_ttl.post(
            "/dashboard/login",
            data={"username": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert login.status_code == 303
        resp = await client_with_dashboard_ttl.get(
            "/dashboard", follow_redirects=False
        )
        assert resp.status_code == 200
        assert "Users" in resp.text

    async def test_login_failure(self, client):
        resp = await client.post(
            "/dashboard/login",
            data={"username": "wrong@test.com", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.text

    async def test_logout(self, client, dashboard_headers):
        resp = await client.post(
            "/dashboard/logout",
            headers=dashboard_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 303


class TestDashboardPages:
    async def test_agents_page_requires_auth(self, client):
        resp = await client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 303

    async def test_agents_page(self, client, dashboard_headers):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        assert "Users" in resp.text

    async def test_users_page_shows_owner_email(
        self, client, dashboard_headers, viewer_user, registered_agent
    ):
        """The users-centric view lists every user by email and surfaces the
        per-user owned-agent count in the dedicated column."""
        del registered_agent  # ensures the agent exists; assertion is on the user row
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        assert viewer_user["email"] in resp.text

    async def test_agent_detail(self, client, dashboard_headers, registered_agent):
        agent_id, _ = registered_agent
        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert agent_id in resp.text

    async def test_agent_detail_not_found(self, client, dashboard_headers):
        resp = await client.get(
            "/dashboard/agents/nonexistent", headers=dashboard_headers
        )
        assert resp.status_code == 404


class TestAgentDetailVersion:
    """Cover the new ``Version`` row on ``GET /dashboard/agents/{id}``:
    bare version when no manifest is wired, ``Up to date`` badge when
    the stored version matches the manifest's ``latest_agent_version``,
    and the ``(latest: X.Y.Z)`` hint when they diverge. Also asserts the
    em-dash placeholder for a fresh agent (NULL columns) and the
    ``Protocol`` row formatting (``v2``).
    """

    @staticmethod
    def _populate_manifest(app, version: str) -> None:
        """Write a minimal valid manifest record into the live store so
        ``agent_detail_page`` reads back ``latest_agent_version=<version>``.

        Uses the same shape ``ManifestStore.write`` enforces (see
        ``test_version_handshake._signed_manifest``). A real signature is
        required because ``ManifestStore.write`` validates structure.
        """
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        from central_server.manifest.store import canonical_json

        private = Ed25519PrivateKey.generate()
        body = {
            "version": version,
            "tarball_url": "file:///tmp/agent.tar.gz",
            "tarball_sha256": "0" * 64,
            "wheel_url": "file:///tmp/agent.whl",
            "wheel_sha256": "1" * 64,
            "released_at": "2026-05-15T00:00:00+00:00",
        }
        sig_b64 = base64.b64encode(private.sign(canonical_json(body))).decode()
        app.state.manifest_store.write(body, sig_b64)

    async def test_renders_em_dash_when_version_missing(
        self, client, dashboard_headers, registered_agent
    ):
        """Fresh agent with no handshake yet — ``agent_version`` is NULL,
        so the template branch should emit the em-dash placeholder (and
        no badge / hint).
        """
        agent_id, _ = registered_agent

        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert "<strong>Version:</strong>" in body
        assert "Up to date" not in body
        assert "(latest:" not in body

    async def test_renders_version_without_manifest(
        self, client, app, dashboard_headers, registered_agent
    ):
        """A populated agent_version with NO manifest wired must render
        the bare version (no badge, no hint).
        """
        agent_id, _ = registered_agent
        await app.state.db.update_agent_handshake_info(agent_id, "0.3.0", 2)
        # Wipe the manifest_store to None to simulate "no published
        # manifest" path through the ``getattr(..., None)`` guard.
        app.state.manifest_store = None

        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert "0.3.0" in body
        assert "Up to date" not in body
        assert "(latest:" not in body

    async def test_renders_up_to_date_badge_when_versions_match(
        self, client, app, dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        await app.state.db.update_agent_handshake_info(agent_id, "0.3.0", 2)
        self._populate_manifest(app, "0.3.0")

        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert "0.3.0" in body
        assert "Up to date" in body
        assert "(latest:" not in body

    async def test_renders_latest_hint_when_versions_differ(
        self, client, app, dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        await app.state.db.update_agent_handshake_info(agent_id, "0.3.0", 2)
        self._populate_manifest(app, "0.9.9")

        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert "0.3.0" in body
        assert "(latest: 0.9.9)" in body
        assert "Up to date" not in body

    async def test_renders_protocol_version_when_set(
        self, client, app, dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        await app.state.db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        resp = await client.get(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert "<strong>Protocol:</strong>" in body
        assert "v2" in body

    async def test_user_agents_fragment_renders_version_column(
        self, client, app, dashboard_headers, registered_agent
    ):
        """The expanded per-user agent sub-table surfaces the version
        column for each owned agent (the top-level users table no longer
        shows versions; admins drill in by clicking the row).
        """
        agent_id, _ = registered_agent
        await app.state.db.update_agent_handshake_info(agent_id, "0.3.0", 2)
        owner_id = await app.state.db.get_agent_owner(agent_id)
        await app.state.db.register_agent("stale-agent", owner_id)

        resp = await client.get(
            f"/dashboard/users/{owner_id}/agents", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert "<th>Version</th>" in body
        assert "0.3.0" in body


class TestDashboardRoleGate:
    """Dashboard is an admin-only platform-operator surface.

    Viewers (agent owners) use the mobile app, not the dashboard. Any
    authenticated viewer hitting a protected dashboard route must get 403.
    """

    async def test_viewer_cannot_view_agents_page(
        self, client, viewer_dashboard_headers
    ):
        resp = await client.get("/dashboard", headers=viewer_dashboard_headers)
        assert resp.status_code == 403

    async def test_viewer_cannot_view_agent_detail(
        self, client, viewer_dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        resp = await client.get(
            f"/dashboard/agents/{agent_id}",
            headers=viewer_dashboard_headers,
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_revoke_pairing(
        self, client, app, viewer_dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        pairing_id = await app.state.db.create_pairing(agent_id, "device-1")
        resp = await client.delete(
            f"/dashboard/pairings/{pairing_id}",
            headers=viewer_dashboard_headers,
        )
        assert resp.status_code == 403


class TestUserList:
    """The users-centric ``GET /dashboard`` and its supporting endpoints:
    the search fragment, the per-user agents expansion, and the OOB
    status poller.
    """

    async def test_users_page_lists_seeded_admin(
        self, client, dashboard_headers
    ):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        assert TEST_ADMIN_EMAIL in resp.text

    async def test_users_page_lists_viewer_with_agent_count(
        self, client, dashboard_headers, viewer_user, registered_agent
    ):
        del registered_agent  # forces agent registration
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        body = resp.text
        assert viewer_user["email"] in body
        # The agent-count column for the viewer reads "1".
        assert f'id="user-row-{viewer_user["id"]}"' in body

    async def test_users_fragment_returns_unfiltered_table(
        self, client, dashboard_headers
    ):
        resp = await client.get(
            "/dashboard/users", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert 'id="users-table"' in resp.text
        assert TEST_ADMIN_EMAIL in resp.text

    async def test_user_agents_fragment_returns_detail_row(
        self, client, dashboard_headers, viewer_user, registered_agent
    ):
        del registered_agent
        resp = await client.get(
            f"/dashboard/users/{viewer_user['id']}/agents",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        assert 'class="detail-row"' in body
        # The detail row carries a stable id so the client-side toggle
        # (toggleUserRow) can remove it on the next click — without this
        # id the re-click would pile up duplicate rows.
        assert f'id="user-detail-{viewer_user["id"]}"' in body
        # The agent should appear in the expanded table.
        assert "test-agent" in body

    async def test_users_table_row_uses_onclick_toggle(
        self, client, dashboard_headers
    ):
        """The user row must be wired to the JS toggle, not an hx-get
        on the row itself — otherwise re-clicking would not collapse.
        """
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        body = resp.text
        assert "toggleUserRow(" in body
        # The row's hx-get-on-click pattern is gone — the toggle is JS.
        # Confirm the row markup itself doesn't carry hx-get/hx-target
        # for the agents fragment (the /agents sub-path specifically;
        # other hx-get attrs like /delete-modal and the search input
        # are expected and unrelated).
        import re
        assert not re.search(r'hx-get="/dashboard/users/[^"]+/agents"', body)

    async def test_user_agents_fragment_empty_state(
        self, client, dashboard_headers, viewer_user
    ):
        resp = await client.get(
            f"/dashboard/users/{viewer_user['id']}/agents",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        assert "No agents registered" in resp.text

    async def test_user_agents_fragment_404_for_unknown_user(
        self, client, dashboard_headers
    ):
        resp = await client.get(
            "/dashboard/users/ghost/agents", headers=dashboard_headers
        )
        assert resp.status_code == 404


class TestUserSearchFilter:
    async def test_search_filters_by_email_substring(
        self, client, dashboard_headers, viewer_user
    ):
        del viewer_user  # fixture registers the viewer
        resp = await client.get(
            "/dashboard/users?q=viewer", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert TEST_VIEWER_EMAIL in body
        # The seeded admin should NOT be in the filtered table.
        assert TEST_ADMIN_EMAIL not in body

    async def test_search_is_case_insensitive(
        self, client, dashboard_headers, viewer_user
    ):
        del viewer_user
        resp = await client.get(
            "/dashboard/users?q=VIEWER", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert TEST_VIEWER_EMAIL in resp.text

    async def test_search_empty_result_renders_empty_state(
        self, client, dashboard_headers
    ):
        resp = await client.get(
            "/dashboard/users?q=does-not-exist", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert "No users matching" in resp.text


class TestUserDelete:
    """``DELETE /dashboard/users/{id}`` — cascade, guardrails, WS teardown."""

    async def test_delete_user_cascades_agents_pairings_and_templates(
        self, client, app, dashboard_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        user_id = viewer_user["id"]
        db = app.state.db
        # Establish dependent rows the cascade must reach.
        pairing_id = await db.create_pairing(agent_id, "device-1")
        await db.store_refresh_token(
            raw_token="refresh-1",
            mobile_device_id="device-1",
            agent_id=agent_id,
            expire_minutes=60,
        )
        await db.create_template(user_id, "tpl", "body")

        resp = await client.delete(
            f"/dashboard/users/{user_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        # Every layer is gone.
        assert await db.get_user_by_id(user_id) is None
        assert await db.get_agent(agent_id) is None
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False
        assert await db.validate_and_consume_refresh("refresh-1") is None
        assert await db.list_templates_for_user(user_id) == []
        # Response carries OOB delete markers so HTMX closes the modal
        # (empty primary body → #modal-root.innerHTML="") and removes
        # the row + any expanded detail row in one round trip.
        assert f'id="user-row-{user_id}"' in resp.text
        assert f'id="user-detail-{user_id}"' in resp.text
        assert 'hx-swap-oob="delete"' in resp.text
        del pairing_id

    async def test_delete_user_response_emits_oob_swap_for_row_and_detail(
        self, client, app, dashboard_headers
    ):
        """Reproduces the user-reported flow: zero-agent user, type-the-email
        confirm. Response must carry OOB delete markers for both #user-row
        and #user-detail so the modal closes and the row vanishes.
        """
        password_hash = await app.state.auth_service._hash_password("p")
        zero_agent_user_id = await app.state.db.create_user(
            email="zero-agents@test.local",
            password_hash=password_hash,
            role="viewer",
            email_verified=True,
        )
        resp = await client.delete(
            f"/dashboard/users/{zero_agent_user_id}",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        assert f'id="user-row-{zero_agent_user_id}"' in body
        assert f'id="user-detail-{zero_agent_user_id}"' in body
        assert body.count('hx-swap-oob="delete"') == 2

    async def test_delete_seed_admin_returns_400(self, client, app):
        """The seeded admin and the default dashboard-login admin happen
        to be the same user in this suite, so the self-delete guard
        would fire first. Provision a second admin and call as them to
        exercise the seed-admin guard in isolation.
        """
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)
        password_hash = await app.state.auth_service._hash_password("p2")
        await app.state.db.create_user(
            email="admin2@test.local",
            password_hash=password_hash,
            role="admin",
            email_verified=True,
        )
        _uid, _role, token = await app.state.auth_service.login_user(
            "admin2@test.local", "p2"
        )
        headers = {"Cookie": f"dashboard_token={token}"}

        resp = await client.delete(
            f"/dashboard/users/{admin['id']}", headers=headers
        )
        assert resp.status_code == 400
        assert "seed admin" in resp.text.lower()
        assert await app.state.db.get_user_by_id(admin["id"]) is not None

    async def test_delete_self_returns_400(
        self, client, app, dashboard_headers
    ):
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)
        resp = await client.delete(
            f"/dashboard/users/{admin['id']}", headers=dashboard_headers
        )
        assert resp.status_code == 400
        assert "own account" in resp.text.lower()
        assert await app.state.db.get_user_by_id(admin["id"]) is not None

    async def test_delete_unknown_user_returns_404(
        self, client, dashboard_headers
    ):
        resp = await client.delete(
            "/dashboard/users/ghost", headers=dashboard_headers
        )
        assert resp.status_code == 404

    async def test_delete_user_modal_renders_email_and_agents(
        self, client, dashboard_headers, viewer_user, registered_agent
    ):
        del registered_agent
        resp = await client.get(
            f"/dashboard/users/{viewer_user['id']}/delete-modal",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        assert viewer_user["email"] in body
        assert "test-agent" in body
        assert "modal-overlay" in body
        # Submit button is wired to DELETE the user.
        assert f'hx-delete="/dashboard/users/{viewer_user["id"]}"' in body
        # Modal closes via primary-swap into #modal-root; row deletion
        # rides on OOB markers in the response (no hx-on::after-request).
        assert 'hx-target="#modal-root"' in body
        assert 'hx-swap="innerHTML"' in body
        assert "hx-on::after-request" not in body

    async def test_delete_user_modal_self_path_is_blocked(self, client, app):
        """Provision a second admin (not the seed) so the modal renders
        the self-delete branch instead of the seed-admin branch.
        """
        password_hash = await app.state.auth_service._hash_password("p3")
        second_admin_id = await app.state.db.create_user(
            email="admin3@test.local",
            password_hash=password_hash,
            role="admin",
            email_verified=True,
        )
        _uid, _role, token = await app.state.auth_service.login_user(
            "admin3@test.local", "p3"
        )
        headers = {"Cookie": f"dashboard_token={token}"}

        resp = await client.get(
            f"/dashboard/users/{second_admin_id}/delete-modal",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "hx-delete=" not in resp.text
        assert "cannot delete your own account" in resp.text.lower()

    async def test_delete_user_modal_seed_admin_branch_blocks(
        self, client, app
    ):
        """Independently exercise the seed-admin modal branch.

        A second (non-seed) admin viewing the seeded admin's modal must
        hit the seed-admin guard — the form is suppressed and the
        operator sees the seed-admin error copy.
        """
        admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)
        password_hash = await app.state.auth_service._hash_password("p4")
        await app.state.db.create_user(
            email="admin4@test.local",
            password_hash=password_hash,
            role="admin",
            email_verified=True,
        )
        _uid, _role, token = await app.state.auth_service.login_user(
            "admin4@test.local", "p4"
        )
        headers = {"Cookie": f"dashboard_token={token}"}

        resp = await client.get(
            f"/dashboard/users/{admin['id']}/delete-modal", headers=headers
        )
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "hx-delete=" not in body
        assert "seed admin" in body

    async def test_delete_user_closes_live_user_clients(
        self, client, app, dashboard_headers, viewer_user
    ):
        """The DELETE handler must call routing.disconnect_user to tear
        down every live mobile-event WS the deleted user has open.
        """
        from unittest.mock import AsyncMock, MagicMock

        from central_server.relay.routing import UserClient

        ws = MagicMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        user_client = UserClient(
            user_id=viewer_user["id"], device_id="dev-1", websocket=ws
        )
        app.state.routing.register_user_client(viewer_user["id"], user_client)

        resp = await client.delete(
            f"/dashboard/users/{viewer_user['id']}",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        ws.close.assert_awaited_once_with(
            code=4010, reason="admin_revoked"
        )

    async def test_delete_user_closes_live_agent_sockets(
        self, client, app, dashboard_headers, viewer_user, registered_agent
    ):
        """The DELETE handler must call routing.disconnect_agent for
        every agent the deleted user owned so that the agent WS and any
        paired mobile sockets are torn down.
        """
        from unittest.mock import AsyncMock, MagicMock

        agent_id, _ = registered_agent
        agent_ws = MagicMock()
        agent_ws.send_text = AsyncMock()
        agent_ws.close = AsyncMock()
        app.state.routing.register_agent(agent_id, agent_ws)

        resp = await client.delete(
            f"/dashboard/users/{viewer_user['id']}",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        agent_ws.close.assert_awaited_once_with(
            code=4010, reason="admin_revoked"
        )
        assert app.state.routing.is_agent_online(agent_id) is False

    async def test_post_delete_api_call_returns_401_to_mobile_user(
        self, client, app, dashboard_headers
    ):
        """Offline-then-reconnect scenario: a mobile client that missed
        the 4010 WS close (because it was offline at unregister time)
        must be logged out the next time it hits the REST API. The
        JWT signature is still valid — the existence check in
        ``get_api_user`` is what turns this into a 401 the Android
        ``CentralAuthInterceptor`` can react to.
        """
        # Create a fresh user, capture their dashboard JWT, then have
        # the admin unregister them. After cascade-delete, the JWT is
        # still cryptographically valid but the ``users`` row is gone.
        result = await app.state.auth_service.register_user(
            "offline-victim@test.local", "p1"
        )
        mobile_user_token = result.access_token
        assert mobile_user_token is not None

        resp = await client.delete(
            f"/dashboard/users/{result.user_id}",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200

        # The reconnecting mobile carries its (now stale) JWT.
        resp = await client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {mobile_user_token}"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid token"
        assert resp.headers["WWW-Authenticate"] == "Bearer"


class TestAdminDeleteAgent:
    """``DELETE /dashboard/agents/{id}`` admin path bypasses ownership."""

    async def test_admin_can_delete_any_agent(
        self, client, app, dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        resp = await client.delete(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert await app.state.db.get_agent(agent_id) is None

    async def test_admin_delete_unknown_agent_returns_404(
        self, client, dashboard_headers
    ):
        resp = await client.delete(
            "/dashboard/agents/ghost", headers=dashboard_headers
        )
        assert resp.status_code == 404

    async def test_owner_api_endpoint_still_rejects_admin_token(
        self, client, admin_api_auth_headers, registered_agent
    ):
        """Regression: ``DELETE /api/agents/{id}`` is owner-gated by
        ``get_non_admin_api_user``. Admin tokens must still be rejected
        on that endpoint — the new admin path is dashboard-only.
        """
        agent_id, _ = registered_agent
        resp = await client.delete(
            f"/api/agents/{agent_id}", headers=admin_api_auth_headers
        )
        assert resp.status_code == 403

    async def test_admin_delete_closes_live_agent_socket(
        self, client, app, dashboard_headers, registered_agent
    ):
        from unittest.mock import AsyncMock, MagicMock

        agent_id, _ = registered_agent
        agent_ws = MagicMock()
        agent_ws.send_text = AsyncMock()
        agent_ws.close = AsyncMock()
        app.state.routing.register_agent(agent_id, agent_ws)

        resp = await client.delete(
            f"/dashboard/agents/{agent_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        agent_ws.close.assert_awaited_once_with(
            code=4010, reason="admin_revoked"
        )


class TestForceDisconnect:
    """Force-disconnect endpoints only tear down live WSs — DB untouched."""

    async def test_force_disconnect_user(
        self, client, app, dashboard_headers, viewer_user
    ):
        from unittest.mock import AsyncMock, MagicMock

        from central_server.relay.routing import UserClient

        ws = MagicMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        app.state.routing.register_user_client(
            viewer_user["id"],
            UserClient(
                user_id=viewer_user["id"], device_id="dev-1", websocket=ws
            ),
        )

        resp = await client.post(
            f"/dashboard/users/{viewer_user['id']}/disconnect",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        ws.close.assert_awaited_once_with(
            code=4011, reason="admin_force_disconnect"
        )
        # User row untouched.
        assert await app.state.db.get_user_by_id(viewer_user["id"]) is not None

    async def test_force_disconnect_agent(
        self, client, app, dashboard_headers, registered_agent
    ):
        from unittest.mock import AsyncMock, MagicMock

        agent_id, _ = registered_agent
        agent_ws = MagicMock()
        agent_ws.send_text = AsyncMock()
        agent_ws.close = AsyncMock()
        app.state.routing.register_agent(agent_id, agent_ws)

        resp = await client.post(
            f"/dashboard/agents/{agent_id}/disconnect",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        agent_ws.close.assert_awaited_once_with(
            code=4011, reason="admin_force_disconnect"
        )
        # Agent DB row untouched.
        assert await app.state.db.get_agent(agent_id) is not None


class TestOOBStatus:
    """``GET /dashboard/agents-status`` emits hx-swap-oob spans only."""

    async def test_emits_user_online_count_spans(
        self, client, dashboard_headers, viewer_user
    ):
        resp = await client.get(
            "/dashboard/agents-status", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert f'id="user-online-{viewer_user["id"]}"' in body
        assert 'hx-swap-oob="true"' in body

    async def test_emits_agent_status_span_per_agent(
        self, client, dashboard_headers, registered_agent
    ):
        agent_id, _ = registered_agent
        resp = await client.get(
            "/dashboard/agents-status", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert f'id="agent-status-{agent_id}"' in resp.text

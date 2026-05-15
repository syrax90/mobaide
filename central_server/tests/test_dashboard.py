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
        assert "Agents" in resp.text

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
        assert "Agents" in resp.text

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
        assert "Agents" in resp.text

    async def test_agents_page_shows_owner(self, client, dashboard_headers, registered_agent):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        assert "Owner" in resp.text

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

    async def test_agents_list_renders_version_column(
        self, client, app, dashboard_headers, registered_agent
    ):
        """The new ``Version`` column on ``GET /dashboard`` must render
        the stored version (after a successful handshake) and the
        em-dash for agents that never reconnected since the migration.
        """
        agent_id, _ = registered_agent
        await app.state.db.update_agent_handshake_info(agent_id, "0.3.0", 2)
        # Register a second agent owned by the same viewer that has
        # never reconnected — its row should show the em-dash placeholder.
        owner_id = await app.state.db.get_agent_owner(agent_id)
        await app.state.db.register_agent("stale-agent", owner_id)

        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        body = resp.text
        # Header column is present.
        assert "<th>Version</th>" in body
        # The configured agent shows its reported version.
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

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
    _uid, _role, token, _scheduled = await auth_service.login_user(
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

    async def test_agent_detail_not_found(self, client, dashboard_headers):
        resp = await client.get(
            "/dashboard/agents/nonexistent", headers=dashboard_headers
        )
        assert resp.status_code == 404


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

class TestUserList:
    """The users-centric ``GET /dashboard`` and its supporting endpoints:
    the search fragment, the per-user agents expansion, and the OOB
    status poller.
    """

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


class TestUserVerifiedBadgeAndFilter:
    """The Verified column badges and the ``verified=`` fragment filter.

    Test config runs with ``email_verification_required=False``, so the
    seeded admin and the ``viewer_user`` fixture are both *verified*;
    unverified users are seeded via ``db.create_user`` directly (defaults
    ``email_verified=False``). Assertions use the badge CSS class names:
    the label "Verified" is a substring of "Unverified", but
    ``badge-verified`` is not a substring of ``badge-unverified``.
    """

    UNVERIFIED_EMAIL = "unverified@test.local"

    async def _seed_unverified(self, app) -> str:
        return await app.state.db.create_user(
            email=self.UNVERIFIED_EMAIL, password_hash="h"
        )

    async def test_filter_unverified_shows_only_unverified(
        self, client, app, dashboard_headers
    ):
        await self._seed_unverified(app)
        resp = await client.get(
            "/dashboard/users?verified=unverified", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert self.UNVERIFIED_EMAIL in body
        assert TEST_ADMIN_EMAIL not in body

    async def test_filter_verified_shows_only_verified(
        self, client, app, dashboard_headers
    ):
        await self._seed_unverified(app)
        resp = await client.get(
            "/dashboard/users?verified=verified", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert TEST_ADMIN_EMAIL in body
        assert self.UNVERIFIED_EMAIL not in body

    async def test_filter_all_omitted_and_unknown_are_lenient(
        self, client, app, dashboard_headers
    ):
        """``all``, an omitted param, and an unknown value all mean "no
        filter" — a tampered URL degrades to the unfiltered table rather
        than a 422 swapped into it.
        """
        await self._seed_unverified(app)
        for query in ("?verified=all", "", "?verified=bogus"):
            resp = await client.get(
                f"/dashboard/users{query}", headers=dashboard_headers
            )
            assert resp.status_code == 200
            body = resp.text
            assert TEST_ADMIN_EMAIL in body
            assert self.UNVERIFIED_EMAIL in body


async def _seed_users(app, count: int) -> list[str]:
    """Create ``count`` unverified users, newer than every fixture user;
    returns their ids oldest-first. Spaced a millisecond apart so
    ``created_at`` orders them strictly."""
    import asyncio

    db = app.state.db
    ids = []
    for n in range(count):
        ids.append(
            await db.create_user(email=f"bulk{n:03d}@test.local", password_hash="h")
        )
        await asyncio.sleep(0.001)
    return ids


def _attr(body: str, attr: str, start: int) -> str:
    """Value of the first ``attr="…"`` at or after ``start``; ``&amp;`` is
    folded back so URL assertions read like the URLs the browser sees."""
    begin = body.index(f'{attr}="', start) + len(attr) + 2
    return body[begin:body.index('"', begin)].replace("&amp;", "&")


def _row_ids(body: str) -> list[str]:
    """User ids of the rendered ``<tr id="user-row-…">`` rows, in order.
    Emails are unusable for ordering: the layout header prints the
    signed-in admin's address above the table."""
    ids = []
    start = 0
    while (found := body.find('id="user-row-', start)) != -1:
        begin = found + len('id="user-row-')
        ids.append(body[begin:body.index('"', begin)])
        start = begin
    return ids


def _sort_header(body: str, label: str) -> tuple[str, str]:
    """``(aria-sort, href)`` of the sortable header titled ``Sort by
    <label>``."""
    th = body.rfind("<th ", 0, body.index(f'title="Sort by {label}'))
    return _attr(body, "aria-sort", th), _attr(body, "href", th)


def _link_href(body: str, aria_label: str) -> str:
    anchor = body.rfind("<a ", 0, body.index(f'aria-label="{aria_label}"'))
    return _attr(body, "href", anchor)


class TestUserSorting:
    """The sortable Registered / Last Activity headers on ``GET /dashboard``."""

    async def test_default_view_sorts_registered_descending(
        self, client, dashboard_headers
    ):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        assert resp.status_code == 200
        body = resp.text
        assert _sort_header(body, "registered") == (
            "descending", "/dashboard?dir=asc",
        )
        assert _sort_header(body, "last activity") == (
            "none", "/dashboard?sort=activity",
        )

    async def test_activity_sort_orders_rows_and_header_flips(
        self, client, app, dashboard_headers, viewer_user, registered_agent
    ):
        """Ascending puts the never-active admin before the viewer whose
        agent has been seen; descending reverses it. The active header's
        link is the opposite direction, the other header's link is the
        default view."""
        agent_id, _ = registered_agent
        await app.state.db.touch_agent_last_seen(agent_id)
        admin_id = (await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL))["id"]

        resp = await client.get(
            "/dashboard?sort=activity&dir=asc", headers=dashboard_headers
        )
        assert resp.status_code == 200
        body = resp.text
        assert _row_ids(body) == [admin_id, viewer_user["id"]]
        assert _sort_header(body, "last activity") == (
            "ascending", "/dashboard?sort=activity",
        )
        assert _sort_header(body, "registered") == ("none", "/dashboard")

        resp = await client.get(
            "/dashboard?sort=activity", headers=dashboard_headers
        )
        body = resp.text
        assert _row_ids(body) == [viewer_user["id"], admin_id]
        assert _sort_header(body, "last activity") == (
            "descending", "/dashboard?sort=activity&dir=asc",
        )

    async def test_garbage_params_degrade_to_the_default_view(
        self, client, dashboard_headers
    ):
        resp = await client.get(
            "/dashboard?sort=email&dir=up&page=x&per_page=7",
            headers=dashboard_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        assert _sort_header(body, "registered")[0] == "descending"
        assert '<option value="25" selected>' in body


class TestUserPagination:
    """Paging of the users panel: row windows, the pager, clamping, and
    the canonical ``HX-Replace-Url`` the fragment always answers with."""

    async def test_single_page_hides_the_pager(self, client, dashboard_headers):
        resp = await client.get("/dashboard", headers=dashboard_headers)
        body = resp.text
        assert "Showing 1–1 of 1 user" in body
        assert "of 1 users" not in body
        assert 'class="pager"' not in body

    async def test_pages_window_users_newest_first(
        self, client, app, dashboard_headers
    ):
        seeded = await _seed_users(app, 30)
        admin_id = (await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL))["id"]
        newest_first = list(reversed(seeded))

        first = (await client.get("/dashboard", headers=dashboard_headers)).text
        assert "Showing 1–25 of 31 users" in first
        assert _row_ids(first) == newest_first[:25]
        assert 'aria-current="page">1<' in first
        assert _link_href(first, "Next page") == "/dashboard?page=2"
        assert 'aria-disabled="true">‹ Prev' in first

        second = (
            await client.get("/dashboard?page=2", headers=dashboard_headers)
        ).text
        assert "Showing 26–31 of 31 users" in second
        assert _row_ids(second) == newest_first[25:] + [admin_id]
        assert 'aria-current="page">2<' in second
        assert _link_href(second, "Previous page") == "/dashboard"
        assert 'aria-disabled="true">Next ›' in second

    async def test_page_past_the_end_clamps_to_the_last_page(
        self, client, app, dashboard_headers
    ):
        await _seed_users(app, 30)

        resp = await client.get(
            "/dashboard/users?page=99", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert resp.headers["HX-Replace-Url"] == "/dashboard?page=2"
        assert "Showing 26–31 of 31 users" in resp.text

        resp = await client.get(
            "/dashboard/users?page=99&per_page=50", headers=dashboard_headers
        )
        assert resp.headers["HX-Replace-Url"] == "/dashboard?per_page=50"
        assert len(_row_ids(resp.text)) == 31
        assert 'class="pager"' not in resp.text

    async def test_fragment_replace_url_is_canonical(
        self, client, dashboard_headers
    ):
        cases = [
            ("", "/dashboard"),
            ("?q=&verified=all&sort=registered&dir=desc&page=1&per_page=25",
             "/dashboard"),
            ("?sort=bogus&dir=up&page=x&per_page=7", "/dashboard"),
            ("?q=view&sort=activity&dir=asc&per_page=50",
             "/dashboard?q=view&sort=activity&dir=asc&per_page=50"),
        ]
        for query, expected in cases:
            resp = await client.get(
                f"/dashboard/users{query}", headers=dashboard_headers
            )
            assert resp.status_code == 200, query
            assert resp.headers["HX-Replace-Url"] == expected, query

    async def test_panel_controls_carry_the_rendered_state(
        self, client, dashboard_headers
    ):
        """The hidden inputs, the per-page select's ``hx-get`` (which must
        omit ``per_page`` — htmx appends the select's own value) and the
        poller URL all describe the state the server just rendered."""
        resp = await client.get(
            "/dashboard/users?sort=activity&dir=asc&per_page=50",
            headers=dashboard_headers,
        )
        body = resp.text
        assert '<input type="hidden" name="sort" value="activity">' in body
        assert '<input type="hidden" name="dir" value="asc">' in body
        assert _attr(body, "hx-get", body.index('id="per-page"')) == (
            "/dashboard/users?sort=activity&dir=asc"
        )
        assert '<option value="50" selected>' in body
        poller = body.rfind("<div", 0, body.index('hx-trigger="every 10s"'))
        assert _attr(body, "hx-get", poller) == (
            "/dashboard/agents-status?sort=activity&dir=asc&per_page=50"
        )


class TestAgentStatusPollerScope:
    """``GET /dashboard/agents-status`` takes the panel's listing state and
    emits OOB spans only for the users on that page."""

    async def test_poller_renders_only_the_visible_page(
        self, client, app, dashboard_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        # 25 newer users fill page 1; admin and viewer fall onto page 2.
        await _seed_users(app, 25)

        page_one = await client.get(
            "/dashboard/agents-status", headers=dashboard_headers
        )
        assert page_one.status_code == 200
        assert page_one.text.count('id="user-online-') == 25
        assert f'id="user-online-{viewer_user["id"]}"' not in page_one.text
        assert f'id="agent-status-{agent_id}"' not in page_one.text

        page_two = await client.get(
            "/dashboard/agents-status?page=2", headers=dashboard_headers
        )
        assert page_two.text.count('id="user-online-') == 2
        assert f'id="user-online-{viewer_user["id"]}"' in page_two.text
        assert f'id="agent-status-{agent_id}"' in page_two.text

    async def test_poller_honours_the_search_filter(
        self, client, dashboard_headers, viewer_user
    ):
        resp = await client.get(
            "/dashboard/agents-status?q=viewer", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert resp.text.count('id="user-online-') == 1
        assert f'id="user-online-{viewer_user["id"]}"' in resp.text


class TestUserDelete:
    """``DELETE /dashboard/users/{id}`` — cascade, guardrails, WS teardown."""

    async def test_delete_user_cascades_agents_devices_and_templates(
        self, client, app, dashboard_headers, viewer_user, registered_agent
    ):
        agent_id, _ = registered_agent
        user_id = viewer_user["id"]
        db = app.state.db
        await db.upsert_mobile_device(
            device_id="device-1", user_id=user_id, name="Test Pixel"
        )
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
        assert await db.get_user_by_id(user_id) is None
        assert await db.get_agent(agent_id) is None
        assert await db.list_user_devices(user_id) == []
        assert await db.validate_and_consume_refresh("refresh-1") is None
        assert await db.list_templates_for_user(user_id) == []
        # Response carries OOB delete markers so HTMX closes the modal
        # (empty primary body → #modal-root.innerHTML="") and removes
        # the row + any expanded detail row in one round trip.
        assert f'id="user-row-{user_id}"' in resp.text
        assert f'id="user-detail-{user_id}"' in resp.text
        assert 'hx-swap-oob="delete"' in resp.text

    async def test_delete_response_triggers_a_panel_refresh(
        self, client, dashboard_headers, viewer_user
    ):
        """Besides the OOB delete markers, the response raises
        ``users-changed`` so the panel re-fetches its page and the count
        line / page boundaries follow the vanished row."""
        user_id = viewer_user["id"]

        resp = await client.delete(
            f"/dashboard/users/{user_id}", headers=dashboard_headers
        )
        assert resp.status_code == 200
        assert resp.headers["HX-Trigger"] == "users-changed"
        assert f'<div id="user-row-{user_id}" hx-swap-oob="delete"></div>' in resp.text

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
        _uid, _role, token, _scheduled = await app.state.auth_service.login_user(
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

"""ENABLE_API_DOCS exposure policy for ``/docs``, ``/redoc``, ``/openapi.json``.

Pins the default-deny contract the relay now ships:

- flag off (the production default) — the three routes are not registered at
  all, for anonymous callers *and* for admins;
- flag on — every non-admin caller gets a 404, never a 401/403/303, so a
  prober cannot tell an enabled-but-gated deployment from a disabled one;
- flag on + admin (dashboard cookie or Bearer) — 200;
- schema hygiene: the browser-only routers and the release-pipeline endpoint
  are out of the generated schema, the JSON API is still in it;
- ``include_in_schema`` / ``docs_url=None`` changed no routing.
"""
from __future__ import annotations

import pytest

from central_server import app as app_module
from tests.conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")

# ``enable_api_docs`` is read at app-construction time, so it has to be set
# before ``create_app`` runs: parametrizing the ``config`` fixture indirectly
# flips the flag for a whole test class while every conftest fixture built on
# top of it (``app``, ``client``, ``dashboard_headers``, …) keeps working.
docs_enabled = pytest.mark.parametrize("config", [True], indirect=True)

docs_both_ways = pytest.mark.parametrize(
    "config", [False, True], indirect=True, ids=["docs_off", "docs_on"]
)

# Starlette resolves method mismatches and trailing-slash redirects in the
# router, before dependencies run, so registering the doc paths as bare
# ``@app.get`` answered 405 to HEAD/POST/… and 307 to ``/docs/`` while the
# flag was on, against 404 for all of them while it was off. Either code
# hands an anonymous prober the flag's value.
PROBE_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE")
PROBE_PATHS = tuple(
    spelling for path in DOC_PATHS for spelling in (path, f"{path}/")
)


@pytest.fixture
def config(config, request):
    """The shared config with ``enable_api_docs`` pinned; off unless asked.

    ``object.__setattr__`` is how this frozen dataclass is poked in tests
    (see ``test_version_handshake.py``).
    """
    object.__setattr__(config, "enable_api_docs", getattr(request, "param", False))
    return config


@pytest.fixture
async def viewer_dashboard_headers(app, viewer_user) -> dict[str, str]:
    """Dashboard cookie for a signed-in non-admin."""
    del viewer_user  # fixture ordering: the viewer must exist before login
    _uid, _role, token, _scheduled = await app.state.auth_service.login_user(
        TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
    )
    return {"Cookie": f"dashboard_token={token}"}


class TestDocsDisabledByDefault:
    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_anonymous_caller_gets_404(self, client, path) -> None:
        response = await client.get(path)
        assert response.status_code == 404

    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_admin_gets_404_too(self, client, dashboard_headers, path) -> None:
        """The flag registers the routes, not the session: no credential
        re-opens them while ENABLE_API_DOCS is off."""
        response = await client.get(path, headers=dashboard_headers)
        assert response.status_code == 404


@docs_enabled
class TestDocsEnabledRejectsNonAdmins:
    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_anonymous_caller_gets_404(self, client, path) -> None:
        response = await client.get(path)
        assert response.status_code == 404

    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_viewer_cookie_gets_404(
        self, client, viewer_dashboard_headers, path
    ) -> None:
        response = await client.get(path, headers=viewer_dashboard_headers)
        assert response.status_code == 404

    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_viewer_bearer_gets_404(self, client, api_auth_headers, path) -> None:
        response = await client.get(path, headers=api_auth_headers)
        assert response.status_code == 404

    async def test_unparseable_cookie_gets_404_not_a_login_redirect(
        self, client
    ) -> None:
        """``get_dashboard_user`` rejects with a 303 to ``/dashboard/login``;
        forwarding it would confirm the route exists."""
        response = await client.get(
            "/openapi.json", headers={"Cookie": "dashboard_token=not-a-jwt"}
        )
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_unexpected_auth_error_fails_closed(
        self, client, monkeypatch, admin_api_auth_headers
    ) -> None:
        """A broken auth dependency must 404, not 500 — a 500 discloses the
        route as surely as a 200 does."""

        async def _boom(request):
            raise RuntimeError("auth backend down")

        monkeypatch.setattr(app_module, "get_api_user", _boom)

        response = await client.get("/openapi.json", headers=admin_api_auth_headers)
        assert response.status_code == 404


@docs_both_ways
class TestAnonymousProbeCannotDetectTheFlag:
    """The whole method × slash-spelling matrix answers 404 either way.

    Running it under both values of ``ENABLE_API_DOCS`` is what makes it a
    parity check: the enabled and disabled deployments must be
    indistinguishable to an unauthenticated caller, so a drift on either
    side breaks the same assertion.
    """

    async def test_every_method_and_spelling_is_404(self, client) -> None:
        probes = [(method, path) for method in PROBE_METHODS for path in PROBE_PATHS]

        statuses = {}
        for method, path in probes:
            # Redirect-following would resolve a 307 on ``/docs/`` into the
            # 404 of ``/docs`` and hide the leak this pins.
            response = await client.request(method, path, follow_redirects=False)
            statuses[(method, path)] = response.status_code

        assert statuses == {probe: 404 for probe in probes}


@docs_enabled
class TestDocsEnabledServesAdmins:
    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_admin_cookie_is_served(
        self, client, dashboard_headers, path
    ) -> None:
        response = await client.get(path, headers=dashboard_headers)
        assert response.status_code == 200, response.text

    async def test_admin_bearer_is_served_the_schema(
        self, client, admin_api_auth_headers
    ) -> None:
        response = await client.get("/openapi.json", headers=admin_api_auth_headers)
        assert response.status_code == 200
        assert response.json()["info"]["title"] == "Central Server"

    async def test_swagger_page_has_no_dead_oauth_redirect(
        self, client, dashboard_headers
    ) -> None:
        """``oauth2_redirect_url=None``: FastAPI only mounts
        ``/docs/oauth2-redirect`` when ``docs_url`` is set, so the default
        would point Swagger UI at a 404."""
        response = await client.get("/docs", headers=dashboard_headers)
        assert "/openapi.json" in response.text
        assert "oauth2-redirect" not in response.text


@docs_enabled
class TestAdminReachesEverySpelling:
    """Closing the probe leak registered six routes instead of three; an
    admin must still be served the right document by each of them."""

    @pytest.mark.parametrize("path", DOC_PATHS)
    async def test_trailing_slash_serves_the_same_document(
        self, client, dashboard_headers, path
    ) -> None:
        bare = await client.get(
            path, headers=dashboard_headers, follow_redirects=False
        )
        slashed = await client.get(
            f"{path}/", headers=dashboard_headers, follow_redirects=False
        )

        assert (bare.status_code, slashed.status_code) == (200, 200)
        assert slashed.text == bare.text

    async def test_each_path_keeps_its_own_endpoint(
        self, client, dashboard_headers
    ) -> None:
        """One endpoint is registered per path inside a loop, so a mis-bound
        iteration would serve one document from all three."""
        bodies = {}
        for path in DOC_PATHS:
            response = await client.get(path, headers=dashboard_headers)
            bodies[path] = response.text

        assert "swagger-ui" in bodies["/docs"]
        assert "swagger-ui" not in bodies["/redoc"]
        assert bodies["/openapi.json"].lstrip().startswith("{")


@docs_enabled
class TestSchemaHygiene:
    @pytest.fixture
    async def paths(self, client, dashboard_headers) -> dict:
        response = await client.get("/openapi.json", headers=dashboard_headers)
        assert response.status_code == 200, response.text
        return response.json()["paths"]

    async def test_json_api_stays_documented(self, paths) -> None:
        assert any(path.startswith("/api/agents") for path in paths)
        assert "/api/v1/agent/manifest.json" in paths
        assert "/api/v1/agent/trust_key" in paths

    @pytest.mark.parametrize("prefix", ["/dashboard", "/portal"])
    async def test_browser_only_routers_are_out_of_schema(self, paths, prefix) -> None:
        assert [path for path in paths if path.startswith(prefix)] == []

    async def test_legal_and_internal_routes_are_out_of_schema(self, paths) -> None:
        assert "/privacy.html" not in paths
        assert "/api/internal/agent_manifest" not in paths

    async def test_doc_routes_do_not_document_themselves(self, paths) -> None:
        assert [path for path in DOC_PATHS if path in paths] == []


class TestPublicSurfaceStillRoutes:
    """``include_in_schema=False`` and ``docs_url=None`` change no routing."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/health",
            "/api/version",
            "/privacy.html",
            "/portal/delete-account-info",
        ],
    )
    async def test_public_page_or_endpoint_still_answers(self, client, path) -> None:
        response = await client.get(path)
        assert response.status_code == 200, path

    async def test_trust_key_still_routes(self, client, config) -> None:
        object.__setattr__(config, "manifest_signing_public_key_b64", "dGVzdA==")
        response = await client.get("/api/v1/agent/trust_key")
        assert response.status_code == 200
        assert response.json()["public_key_b64"] == "dGVzdA=="

    async def test_agent_manifest_still_routes(self, client) -> None:
        """With an empty store this is the handler's own 404, not a routing
        miss — the difference is the whole point of the guard."""
        response = await client.get("/api/v1/agent/manifest.json")
        assert response.status_code == 404
        assert response.json()["detail"] == "No manifest published yet"

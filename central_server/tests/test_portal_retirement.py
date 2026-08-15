"""What is left of ``/portal/*`` after the end-user portal was retired.

Sign-in, sign-up, verification, password reset and the account page moved to the
browser client. Two anonymous pages stayed because external systems name them:
the deletion email links ``/portal/cancel-deletion?token=…`` and
``/portal/delete-account-info`` is the URL registered in Google Play Console's
Data Safety form.

Three contracts are worth pinning against the deleted code coming back:

- the retired paths answer 404 on both verbs — the decision was no redirects, so
  a route silently reappearing would resurrect a second account system;
- ``portal_base.html`` emits no ``<script>`` at all, on any instance. It used to
  gate the Google and Apple SDK tags on ``google_client_id`` /
  ``apple_web_client_id``, which ``_portal_context`` no longer supplies — so the
  Play-Console-registered page pulled two third-party bundles on every hit for
  nothing;
- ``/portal/*`` therefore takes the strict same-origin document policy and the
  baseline Permissions-Policy *irrespective of sign-in config*. That coupling is
  what ``portal_csp()`` used to encode, and its removal is only real if a fully
  configured instance can no longer loosen the header.
"""
from __future__ import annotations

import re

import pytest

GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
APPLE_WEB_CLIENT_ID = "com.mobaide.app.web"

# Every route deleted from ``portal/router.py``. GET and POST are listed
# separately because several of these were GET-renders-the-form,
# POST-submits-it pairs and only one half disappearing would be invisible to a
# single-verb sweep.
RETIRED_GET_PATHS = (
    "/portal/login",
    "/portal/signup",
    "/portal/verify-email",
    "/portal/forgot-password",
    "/portal/reset-password",
    "/portal/account",
    "/portal/account/delete-modal",
    "/portal/account/cancel-deletion-modal",
)

RETIRED_POST_PATHS = (
    "/portal/login",
    "/portal/logout",
    "/portal/signup",
    "/portal/auth/google",
    "/portal/auth/apple",
    "/portal/verify-email",
    "/portal/forgot-password",
    "/portal/reset-password",
    "/portal/account/delete",
    # One path segment from the surviving POST below, and the pair the router
    # is most likely to be "tidied" back together.
    "/portal/account/cancel-deletion",
)

SURVIVING_PAGES = ("/portal/cancel-deletion", "/portal/delete-account-info")

# The three surfaces that extend ``portal_base.html``.
SHELL_PAGES = SURVIVING_PAGES + ("/privacy.html",)

# Byte-identical to ``test_security_headers.SAME_ORIGIN_CSP``: the dashboard's
# policy, which ``/portal/*`` now shares. Spelled out rather than imported from
# production so a loosening edit has to be made twice.
SAME_ORIGIN_DOCUMENT_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)

BASELINE_PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "identity-credentials-get=()"
)

_SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)

# Every subresource and hook the retired templates carried. ``htmx-config`` and
# ``hx-headers`` are here because htmx is inert without them, so a half-revert
# that restores only the bundle would leave a payload-shaped no-op behind.
RETIRED_MARKUP = (
    "accounts.google.com",
    "appleid.cdn-apple.com",
    "htmx.min.js",
    "portal.js",
    "htmx-config",
    "hx-headers",
    'id="modal-root"',
)


@pytest.fixture
def config(config, request):
    """The shared config with the sign-in fields pinned per class.

    ``object.__setattr__`` is how this frozen dataclass is poked in tests (see
    ``test_api_docs_exposure.py``); both fields are read inside ``create_app``,
    so they have to be set before the ``app`` fixture runs.
    """
    for field, value in getattr(request, "param", {}).items():
        object.__setattr__(config, field, value)
    return config


with_both_providers = pytest.mark.parametrize(
    "config",
    [
        {
            "google_client_id": GOOGLE_CLIENT_ID,
            "apple_web_client_id": APPLE_WEB_CLIENT_ID,
        }
    ],
    indirect=True,
    ids=["google+apple"],
)


class TestRetiredRoutesAreGone:
    @pytest.mark.parametrize("path", RETIRED_GET_PATHS)
    async def test_the_retired_pages_answer_404(self, client, path) -> None:
        assert (await client.get(path)).status_code == 404

    @pytest.mark.parametrize("path", RETIRED_POST_PATHS)
    async def test_the_retired_submissions_answer_404(self, client, path) -> None:
        """404, not 405: the whole route is gone, not just one verb of it. A 405
        would mean the path is still mounted and something still handles it."""
        assert (await client.post(path, data={})).status_code == 404


class TestSurvivingRoutes:
    @pytest.mark.parametrize("path", SURVIVING_PAGES)
    async def test_the_externally_linked_pages_still_render(
        self, client, path
    ) -> None:
        """One is in every deletion email, the other is registered in Play
        Console's Data Safety form. Neither URL can be changed or dropped."""
        response = await client.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    async def test_the_cancel_submission_is_still_mounted(self, client) -> None:
        """The one state-changing route left. 403 is its CSRF guard answering —
        which is proof the route exists, and the discriminator against
        ``POST /portal/account/cancel-deletion`` one segment away, which is
        gone."""
        assert (await client.post("/portal/cancel-deletion", data={})).status_code == (
            403
        )


class TestShellLoadsNoScript:
    @pytest.mark.parametrize("path", SHELL_PAGES)
    async def test_no_page_emits_a_script_tag(self, client, path) -> None:
        """The contract ``portal_base.html`` is held to now. These pages are
        static prose plus one native form POST; anything added here puts
        third-party JS back on the page Play Console links to."""
        assert _SCRIPT_TAG_RE.search((await client.get(path)).text) is None

    @pytest.mark.parametrize("path", SHELL_PAGES)
    @pytest.mark.parametrize("marker", RETIRED_MARKUP)
    async def test_no_page_carries_the_retired_markup(
        self, client, path, marker
    ) -> None:
        assert marker not in (await client.get(path)).text

    @with_both_providers
    @pytest.mark.parametrize("path", SHELL_PAGES)
    async def test_a_fully_configured_instance_still_emits_none(
        self, client, path
    ) -> None:
        """The regression this class exists for: the SDK tags were rendered
        behind ``{% if google_client_id %}`` / ``{% if apple_web_client_id %}``,
        fed from ``_portal_context``. Deleting the tags is only durable if the
        context keys cannot switch anything back on."""
        body = (await client.get(path)).text

        assert _SCRIPT_TAG_RE.search(body) is None
        assert GOOGLE_CLIENT_ID not in body
        assert APPLE_WEB_CLIENT_ID not in body


class TestPortalPolicyIsSignInConfigIndependent:
    @with_both_providers
    @pytest.mark.parametrize("path", SURVIVING_PAGES)
    async def test_the_strict_document_policy_survives_both_providers(
        self, client, path
    ) -> None:
        """``portal_csp()`` used to compose a looser policy per configured
        provider — the only ``'unsafe-inline'`` anywhere in the app, on the
        public deletion page."""
        response = await client.get(path)

        assert response.headers["Content-Security-Policy"] == (
            SAME_ORIGIN_DOCUMENT_CSP
        )
        assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"]

    @with_both_providers
    @pytest.mark.parametrize("path", SURVIVING_PAGES)
    async def test_fedcm_is_denied_outright(self, client, path) -> None:
        """``identity-credentials-get=(self "https://accounts.google.com")`` was
        the delegation the GIS button frame needed. No frame, no delegation."""
        assert (await client.get(path)).headers["Permissions-Policy"] == (
            BASELINE_PERMISSIONS_POLICY
        )

    @with_both_providers
    async def test_the_bucket_is_not_the_api_policy(self, client) -> None:
        """The failure mode of deleting the ``/portal/*`` branch rather than
        re-pointing it: ``_API_CSP`` is ``default-src 'none'`` with no
        ``style-src``, so both surviving pages would render unstyled — and the
        cancel form's POST would be blocked by ``form-action 'none'``."""
        policy = (await client.get("/portal/cancel-deletion")).headers[
            "Content-Security-Policy"
        ]

        assert "style-src 'self'" in policy
        assert "form-action 'self'" in policy

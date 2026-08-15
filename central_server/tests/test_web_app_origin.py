"""The one browser-client origin every surface this server renders links to.

``mobaide.com`` no longer signs anyone in: its two "Sign in" affordances, the
hero CTA and the cross-links on the pages that outlived the portal all point at
``app.mobaide.com``. The origin comes from ``WEB_APP_BASE_URL`` through
``_env_origin`` (degrade-don't-raise, same as ``SITE_BASE_URL``) and is hoisted
to ``app.state.web_app_origin`` so the three render paths cannot drift apart.

Only one of those paths fails loudly. ``render_landing`` builds its environment
with ``StrictUndefined``, but ``portal/router.py::_portal_context`` and
``legal/router.py`` render through ``Jinja2Templates``' permissive default
``Undefined``, where a forgotten context key renders ``href="/login"`` — a
same-origin path that does not exist on this host — with no error anywhere.
That is what the absence assertions below are for.
"""
from __future__ import annotations

import pytest

from central_server.app import create_app
from central_server.config import CentralServerConfig

# The pages rendered through a permissive-``Undefined`` context. All three
# extend ``portal_base.html``, whose default ``nav_right`` is built from
# ``app_origin``.
PERMISSIVE_CONTEXT_PAGES = (
    "/portal/delete-account-info",
    "/portal/cancel-deletion",
    "/privacy.html",
)

# What a missing ``app_origin`` degrades to on each of those pages: a relative
# path that resolves against ``mobaide.com`` and 404s.
DEGRADED_HREF = 'href="/login"'


@pytest.fixture
async def landing(client) -> str:
    response = await client.get("/")
    assert response.status_code == 200, response.text
    return response.text


class TestWebAppBaseUrlConfig:
    def test_defaults_to_the_production_browser_client(self, monkeypatch) -> None:
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.delenv("WEB_APP_BASE_URL", raising=False)

        assert CentralServerConfig.from_env().web_app_base_url == (
            "https://app.mobaide.com"
        )

    def test_an_origin_env_var_is_taken_verbatim(self, monkeypatch) -> None:
        """The knob exists so a local checkout can point the whole site at a
        Vite dev server, which is a loopback origin on a non-default port."""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("WEB_APP_BASE_URL", "http://localhost:5173")

        assert CentralServerConfig.from_env().web_app_base_url == (
            "http://localhost:5173"
        )

    def test_a_trailing_slash_is_normalised_away(self, monkeypatch) -> None:
        """``https://app.example.test/`` is the operator mistake this field is
        most exposed to: every use site concatenates ``/login`` onto it."""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("WEB_APP_BASE_URL", "https://app.example.test/")

        assert CentralServerConfig.from_env().web_app_base_url == (
            "https://app.example.test"
        )

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("app.example.test", id="no_scheme"),
            pytest.param("https://app.example.test/login", id="carries_a_path"),
            pytest.param("ftp://app.example.test", id="wrong_scheme"),
            pytest.param("https://app.example.test:99999", id="port_out_of_range"),
            pytest.param("", id="empty"),
        ],
    )
    def test_a_malformed_value_degrades_to_the_default(
        self, monkeypatch, raw
    ) -> None:
        """A typo in a marketing URL must not take the relay down — and must not
        emit ``href="app.example.test/login"`` either, which a browser reads as a
        relative path."""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        monkeypatch.setenv("WEB_APP_BASE_URL", raw)

        assert CentralServerConfig.from_env().web_app_base_url == (
            "https://app.mobaide.com"
        )


class TestLandingPageSignInTargets:
    async def test_both_sign_in_affordances_leave_this_origin(
        self, landing, app
    ) -> None:
        """The nav button and the footer link — the two entry points a visitor
        who came to sign in actually presses. Both used to lead to
        ``/portal/login``, which now 404s."""
        origin = app.state.web_app_origin
        assert landing.count(f'href="{origin}/login"') >= 2
        assert 'href="/portal/login"' not in landing

    async def test_the_hero_cta_opens_the_app_root_not_its_login_screen(
        self, landing, app
    ) -> None:
        """``/`` rather than ``/login`` on purpose: a returning signed-in user
        gets their sessions, where the SPA's ``RequireAnonymous`` guard would
        have bounced them off the login route anyway."""
        origin = app.state.web_app_origin
        assert f'href="{origin}/">Open the web app</a>' in landing

    def test_every_landing_link_follows_a_reconfigured_origin(self, config) -> None:
        """The landing page is rendered once inside ``create_app``, so this is
        the only place the override can be observed.

        ``object.__setattr__`` is how this frozen dataclass is poked in tests
        (see ``test_api_docs_exposure.py``). The absence assertion is on the
        production host inside an ``href``, not on the bare string: the features
        card and the FAQ both print ``app.mobaide.com`` as link *text*, which is
        copy rather than a target.
        """
        object.__setattr__(config, "web_app_base_url", "https://app.example.test")

        html = create_app(config=config).state.marketing_html

        assert html.count('href="https://app.example.test/login"') >= 2
        assert 'href="https://app.example.test/">Open the web app</a>' in html
        assert 'href="https://app.mobaide.com' not in html

    def test_a_trailing_slash_never_reaches_a_rendered_href(self, config) -> None:
        """``app.state.web_app_origin`` strips it, so a value that bypassed
        ``_env_origin`` (a config file, a test, a future loader) still cannot
        publish ``https://app.example.test//login``."""
        object.__setattr__(config, "web_app_base_url", "https://app.example.test/")

        app = create_app(config=config)

        assert app.state.web_app_origin == "https://app.example.test"
        assert "app.example.test//" not in app.state.marketing_html


class TestSurvivingPagesCarryTheAppOrigin:
    @pytest.mark.parametrize("path", PERMISSIVE_CONTEXT_PAGES)
    async def test_the_shell_sign_in_button_names_the_browser_client(
        self, client, app, path
    ) -> None:
        response = await client.get(path)

        assert response.status_code == 200
        assert f'href="{app.state.web_app_origin}/login"' in response.text
        assert DEGRADED_HREF not in response.text

    @pytest.mark.parametrize("path", PERMISSIVE_CONTEXT_PAGES)
    async def test_no_surviving_page_still_links_the_retired_portal_login(
        self, client, path
    ) -> None:
        """The deletion-info page named ``/portal/login`` in prose as well as in
        an ``href``, and the cancel-deletion page linked it three times. Every
        one of those is now a 404 the user is sent to at their worst moment."""
        assert "/portal/login" not in (await client.get(path)).text

    @pytest.mark.parametrize("path", PERMISSIVE_CONTEXT_PAGES)
    async def test_each_page_reads_the_origin_per_request(
        self, client, app, path
    ) -> None:
        """These three render per request, unlike the landing page, so the value
        must come off ``app.state`` at render time rather than being baked in at
        import or startup.

        The absence assertion is on the ``href`` shape alone: the privacy policy
        itself prints ``https://app.mobaide.com/login`` as literal ``<code>``
        text in its deletion instructions, which is published legal copy rather
        than a link this config steers.
        """
        app.state.web_app_origin = "https://app.example.test"

        body = (await client.get(path)).text

        assert 'href="https://app.example.test/login"' in body
        assert 'href="https://app.mobaide.com/login"' not in body

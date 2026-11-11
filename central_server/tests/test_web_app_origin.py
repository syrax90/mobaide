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


class TestWebAppBaseUrlConfig:


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


class TestSurvivingPagesCarryTheAppOrigin:
    @pytest.mark.parametrize("path", PERMISSIVE_CONTEXT_PAGES)
    async def test_the_shell_sign_in_button_names_the_browser_client(
        self, client, app, path
    ) -> None:
        response = await client.get(path)

        assert response.status_code == 200
        assert f'href="{app.state.web_app_origin}/login"' in response.text
        assert DEGRADED_HREF not in response.text



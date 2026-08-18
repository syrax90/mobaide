"""Tests for how the portal loads the shared "modern pixel" design system.

The portal shares ``static/site/site.css`` with the marketing landing page and
adds ``static/site/portal.css`` on top. Both live in the versioned 7-day cache
tier, so the ``?v=`` content hash is the only thing standing between a repeat
visitor and week-stale CSS — and both render paths that stamp it
(``portal/router.py::_portal_context`` and ``legal/router.py``) use
``Jinja2Templates``' permissive default ``Undefined``, which turns a forgotten
context key into a silent bare ``?v=`` rather than an error.

The stylesheet-invariant tests guard the three rules a contributor is most likely
to break, each of which is otherwise only a prose comment in the sheet itself.
See the "Portal styling" and "Accessibility invariants" sections of
``docs/website.md``.
"""
from __future__ import annotations

import re

import pytest

from central_server.static_files import resolve_static_dir

FONT_URL = "/static/site/fonts/DepartureMono-Regular.woff2"

# Every page that extends ``portal_base.html``. Sign-in, sign-up, verification
# and password reset moved to the browser client, so these two anonymous
# pages plus the privacy page are all that is left under this shell.
ANONYMOUS_PORTAL_PAGES = (
    "/portal/cancel-deletion",
    "/portal/delete-account-info",
    "/privacy.html",
)


@pytest.fixture
def portal_css() -> str:
    """``portal.css`` with comments stripped.

    The invariants below are about declarations, and the sheet's own header
    documents them in prose ("No ``:root`` and no ``@font-face`` here") — matching
    that prose would fail every one of these tests on a correct file.
    """
    raw = (resolve_static_dir() / "site" / "portal.css").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)


class TestPortalAssetStamping:
    @pytest.mark.parametrize("path", ANONYMOUS_PORTAL_PAGES)
    async def test_both_sheets_carry_the_shared_asset_version(
        self, client, app, path
    ) -> None:
        """The portal must stamp the same ``?v=`` the landing page does. A
        divergent query re-keys the cache entries per surface, so a
        landing -> portal navigation refetches a 7-day-cached stylesheet and
        wordmark that the browser already holds."""
        version = app.state.asset_version
        assert version
        body = (await client.get(path)).text
        assert f'href="/static/site/site.css?v={version}"' in body
        assert f'href="/static/site/portal.css?v={version}"' in body
        assert f'src="/static/site/img/wordmark.png?v={version}"' in body

    @pytest.mark.parametrize("path", ANONYMOUS_PORTAL_PAGES)
    async def test_the_portal_no_longer_loads_the_admin_sheet(
        self, client, path
    ) -> None:
        """``static/style.css`` is the light Bootstrap-flavoured admin dashboard
        sheet. Loading it alongside ``site.css`` would collide on ``.btn`` and
        ``.card`` and reintroduce ``--radius``."""
        assert "/static/style.css" not in (await client.get(path)).text

    async def test_the_font_preload_carries_no_version_query(self, client) -> None:
        """Preload matching keys on the full URL including the query string, and a
        static stylesheet cannot carry a template variable — so a ``?v=`` on the
        preload alone downloads the 22.5 KB font twice and defeats the preload.
        This href must stay byte-identical to the ``@font-face`` ``url()`` in
        ``site.css``; rename the file if it is ever replaced."""
        body = (await client.get("/portal/delete-account-info")).text
        assert f'href="{FONT_URL}"' in body
        assert f"{FONT_URL}?v=" not in body

    async def test_the_font_url_matches_the_font_face_rule(self) -> None:
        """The other half of the byte-identical contract, checked against the
        stylesheet rather than the template."""
        site_css = (
            (resolve_static_dir() / "site" / "site.css").read_text(encoding="utf-8")
        )
        assert f'url("{FONT_URL}")' in site_css

    async def test_the_privacy_page_stamps_the_asset_version(
        self, client, app
    ) -> None:
        """``privacy.html`` extends ``portal_base.html`` but renders through
        ``legal/router.py``, whose context is built separately from
        ``_portal_context``. With the permissive default ``Undefined`` a forgotten
        key degrades to a bare ``?v=`` with no error anywhere."""
        body = (await client.get("/privacy.html")).text
        assert f"?v={app.state.asset_version}" in body
        assert "?v=\"" not in body

    async def test_the_admin_dashboard_keeps_its_own_sheet(self, client) -> None:
        """The scope guard on this migration: the admin dashboard stays on the
        light sheet and must never pick up the portal's."""
        body = (await client.get("/dashboard/login")).text
        assert "/static/style.css" in body
        assert "portal.css" not in body


class TestPortalStylesheetInvariants:
    def test_declares_no_tokens_and_no_font_face(self, portal_css: str) -> None:
        """``site.css`` is the single source of truth for both. A ``:root`` block
        split across two files is exactly the drift that reusing ``site.css`` was
        meant to prevent."""
        assert ":root" not in portal_css
        assert "@font-face" not in portal_css

    def test_declares_no_non_zero_border_radius(self, portal_css: str) -> None:
        """The pixel look is square corners. ``border-radius: 0`` IS required —
        it defeats iOS Safari's default input rounding — so only the zero reset is
        permitted.

        The values are parsed rather than excluded with a negative lookahead: in
        ``border-radius:\\s*(?!0\\b)`` the ``\\s*`` backtracks to zero width and the
        lookahead then passes against the space, so it matches even a compliant
        sheet.
        """
        values = re.findall(r"border-radius:([^;}]*)", portal_css)
        assert values, "expected the input/radio zero-reset to be present"
        assert {value.strip() for value in values} == {"0"}

    def test_never_uses_a_brand_purple_as_a_text_colour(
        self, portal_css: str
    ) -> None:
        """``--brand`` (#6008AC) and ``--brand-2`` (#6650A4) are fill and border
        colours only; #6650A4 on #1E1E1E is ~2.6:1 and fails WCAG AA as text. The
        leading boundary in the pattern matters: ``-webkit-text-fill-color``,
        ``accent-color``, ``caret-color``, ``background-color`` and
        ``border-color`` all end in ``color:``."""
        assert re.search(r"(?:^|[;{\s])color:\s*var\(--brand", portal_css) is None

    def test_form_controls_do_not_ring_in_the_decorative_rule_colour(
        self, portal_css: str
    ) -> None:
        """Form-control boundaries must use ``--gray`` (#8B8B8B, 5.26:1 on
        ``--bg-2``). ``--rule`` (#625B71) measures 2.78:1, below WCAG 1.4.11's 3:1
        for a UI component boundary — it stays correct for decorative frames and
        plates, but not for a control the user has to find."""
        for selector in ('input[type="url"],\ntextarea {', 'input[type="radio"] {'):
            start = portal_css.index(selector)
            block = portal_css[start : portal_css.index("}", start)]
            assert "inset 0 0 0 2px var(--gray)" in block
            assert "var(--rule)" not in block

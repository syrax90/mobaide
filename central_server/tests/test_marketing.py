"""Tests for the public marketing site served off the apex domain.

Covers the contract documented in ``docs/website.md``:

- ``GET /`` serves the pre-rendered landing page with its cache and
  route-scoped hardening headers, now applied site-wide by
  ``SecurityHeadersMiddleware`` — see ``TestSecurityHeaderScope`` for the
  guarantee that keeps the marketing policy and the portal's separately
  scoped rather than one silently becoming the other.
- **Copy accuracy.** Several Play-listing claims are contradicted by the code
  ("end-to-end", 32 sessions as a per-type cap, unqualified worktrees). The page
  must not repeat them and must keep the qualifiers that make the true claims
  honest. These are the tests a copy edit is most likely to trip.
- **CSP self-containment.** No inline ``style=`` and no inline ``<script>``,
  because the response ships ``default-src 'self'`` without ``'unsafe-inline'``.
  The Google origins in the landing policy exist for the consent-gated gtag.js
  injection alone, so the served HTML itself must stay same-origin —
  ``TestConsentBanner`` holds that line.
- **The install one-liners and their copy buttons.** Each command is rendered
  exactly once, so the copied text cannot drift from the displayed text.
- Social metadata: OG/Twitter tags and a parseable JSON-LD block, all pointing
  at ``config.site_base_url`` rather than the relay origin.
- ``/robots.txt``, ``/sitemap.xml``, ``/favicon.ico``, and the two
  ``CachedStaticFiles`` cache tiers.
- Unit-level loader tests, including the trailing-slash normalisation of
  ``site_base_url``.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from central_server.app import create_app
from central_server.marketing.loader import (
    asset_version,
    render_landing,
    render_robots,
    render_sitemap,
)
from central_server.static_files import resolve_static_dir

# Byte-identical to ``security_headers._LANDING_CSP`` and to
# ``test_security_headers.LANDING_CSP`` on purpose — keep the three in sync by
# changing none of them. The Google origins admit only the consent-gated
# gtag.js load; ``TestConsentBanner`` pins that the served HTML never carries
# it.
EXPECTED_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://www.googletagmanager.com"
    " https://www.googleadservices.com https://www.google.com"
    " https://pagead2.googlesyndication.com"
    " https://googleads.g.doubleclick.net; "
    "img-src 'self' https://www.googletagmanager.com"
    " https://googleads.g.doubleclick.net https://www.google.com"
    " https://www.googleadservices.com"
    " https://pagead2.googlesyndication.com; "
    "connect-src 'self' https://pagead2.googlesyndication.com"
    " https://www.googleadservices.com https://googleads.g.doubleclick.net"
    " https://ad.doubleclick.net https://www.google.com; "
    "frame-src https://www.googletagmanager.com; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)

HARDENING_HEADERS = (
    ("Content-Security-Policy", EXPECTED_CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)

# Claims the code contradicts — see the accuracy table in docs/website.md.
# Matched against a lower-cased body, so these must stay lower case.
CONTRADICTED_CLAIMS = (
    # Transport is TLS per hop; the relay terminates TLS. No E2E crypto exists.
    "end-to-end",
    # `max_sessions` is one global cap over a flat dict, not a per-type limit.
    "32 sessions per",
    # The product is "Claude Code".
    "cloud code",
    # The agent binds 0.0.0.0:8000 unconditionally
    # (mobaide_agent/mobaide/agent/config.py:51). What is true is that nothing
    # needs to be reachable from the internet — not that nothing listens.
    "no inbound ports",
    "nothing on your machine listens",
    # No client can reach the agent without the relay: AuthRepository's
    # connectAgent() has no UI caller on either platform, and the agent's
    # UserStore.add_user() has no CLI verb or endpoint, so the store its
    # /api/auth/token checks is always empty.
    "direct connection",
    "over lan",
    "straight at the agent",
    # Reconnect replays from a byte ring (RING_MAX_BYTES = 4 MiB), not from the
    # 10,000-entry deque of read chunks that backs the REST /scrollback route.
    "10,000-line",
)

# Claims that are true only with their qualifier attached, plus the three
# reboot mechanisms (which differ per OS) and the custom-agent-type cap.
VERIFIED_CLAIMS = (
    "opt-in per launch",
    "git projects only",
    # The TLS guarantee must name the relay path. Unqualified it would cover
    # the clients' unreachable DIRECT plumbing, which builds a ws:// URL unless
    # the agent sits behind HTTPS, and the agent's listener is plain HTTP.
    "going through the relay",
    "systemd",
    "launchd",
    "scheduled task",
    "up to 20",
)

PRODUCT_NAMES = ("Claude Code", "Codex CLI", "Antigravity CLI")

# Every path the marketing router owns.
MARKETING_PATHS = ("/", "/robots.txt", "/sitemap.xml", "/favicon.ico")

# The only two surfaces a crawler could reach; /privacy.html stays crawlable.
UNINDEXABLE_PATHS = ("/api/", "/portal/")

# Naming these in a public file would publish the admin login's location and
# undercut ``app._require_docs_admin``, which answers 404 on every rejection so
# that a probe cannot tell docs-enabled-but-gated from docs-disabled. Nothing
# public links to them, so a Disallow line would buy no crawl budget either.
PATHS_ROBOTS_MUST_NOT_NAME = ("/dashboard/", "/docs", "/redoc", "/openapi.json")

ABSOLUTE_URL_PATTERNS = (
    pytest.param(
        re.compile(r'<link rel="canonical" href="([^"]+)"'), id="canonical"
    ),
    pytest.param(
        re.compile(r'<meta property="og:url" content="([^"]+)"'), id="og:url"
    ),
    pytest.param(
        re.compile(r'<meta property="og:image" content="([^"]+)"'), id="og:image"
    ),
    pytest.param(
        re.compile(r'<meta name="twitter:image" content="([^"]+)"'),
        id="twitter:image",
    ),
)

_SCRIPT_OPEN_TAG_RE = re.compile(r"<script\b[^>]*>")
# Leading whitespace keeps this from matching `data-style=`-style attributes.
_INLINE_STYLE_RE = re.compile(r"\sstyle\s*=")

# Tags that cause the browser to FETCH something, so each one is subject to the
# response's `default-src 'self'`. <meta> is excluded on purpose: its OG/Twitter
# `content=` URLs are scraper metadata, never fetched by the page.
_SUBRESOURCE_TAG_RE = re.compile(r"<(?:link|script|img)\b[^>]*>")
_SUBRESOURCE_URL_RE = re.compile(r'\s(?:src|href)="([^"]*)"')
_LD_JSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)

# The host install one-liners. Kept verbatim rather than imported: this is the
# public page a first-time user copies from, and a silent edit to either string
# sends people to a URL that does not serve an installer.
INSTALL_COMMANDS = (
    "curl -fsSL https://get.mobaide.com/install.sh | bash",
    "irm https://get.mobaide.com/install.ps1 | iex",
)

_COPY_BUTTON_RE = re.compile(r"<button\b[^>]*\bdata-copy\b[^>]*>")
_ARIA_LABEL_RE = re.compile(r'\saria-label="([^"]+)"')
# `on...=` in a tag. CSP blocks event-handler attributes exactly as it blocks an
# inline <script>, and the copy button is the page's first interactive control.
_INLINE_HANDLER_RE = re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE)


@pytest.fixture
async def landing(client) -> str:
    response = await client.get("/")
    assert response.status_code == 200, response.text
    return response.text


def _capture(body: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(body)
    assert match is not None, f"rendered page has no match for {pattern.pattern!r}"
    return match.group(1)


class TestLandingPageResponse:
    async def test_apex_serves_html_with_the_documented_cache_window(
        self, client
    ) -> None:
        """The apex used to answer ``{"detail":"Not Found"}`` — a 404 there is
        the regression this whole feature exists to prevent."""
        response = await client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["Cache-Control"] == "public, max-age=300"

    @pytest.mark.parametrize("header,value", HARDENING_HEADERS)
    async def test_hardening_headers_are_set_exactly(
        self, client, header, value
    ) -> None:
        response = await client.get("/")
        assert response.headers[header] == value

    async def test_marketing_routes_stay_out_of_the_openapi_schema(
        self, app
    ) -> None:
        """The router is browser-only, so an enabled admin docs page must keep
        describing the JSON surface alone."""
        paths = app.openapi()["paths"]
        assert [path for path in MARKETING_PATHS if path in paths] == []


class TestSecurityHeaderScope:
    """The site-wide ``SecurityHeadersMiddleware`` (``security_headers.py``)
    scopes a separate CSP to every surface — see ``test_security_headers.py``
    for the full per-path matrix. The one invariant that belongs here, the
    marketing regression guard, is that the rollout kept the two surfaces
    apart: neither the marketing policy pinned above nor the portal's own was
    unified into, or copied over, the other."""

    async def test_portal_delete_account_info_carries_a_csp_distinct_from_the_marketing_policy(
        self, client
    ) -> None:
        """Before ``SecurityHeadersMiddleware`` existed, ``/portal/*`` ran with
        no CSP at all: the marketing policy above was set per response by this
        router alone, never as middleware. The site-wide rollout deliberately
        reverses that — every surface now gets a policy, the portal included.
        What still belongs in this file, rather than in
        ``test_security_headers.py`` which pins the portal's exact string, is
        the scoping guarantee: the portal's CSP must be present and different
        from the marketing ``EXPECTED_CSP`` pinned above, so the two surfaces
        never silently collapse into one."""
        response = await client.get("/portal/delete-account-info")
        assert response.status_code == 200
        assert "Content-Security-Policy" in response.headers
        assert response.headers["Content-Security-Policy"] != EXPECTED_CSP


class TestLandingCopyAccuracy:
    @pytest.mark.parametrize("phrase", CONTRADICTED_CLAIMS)
    async def test_claims_the_code_contradicts_are_absent(
        self, landing, phrase
    ) -> None:
        assert phrase not in landing.lower()

    @pytest.mark.parametrize("phrase", VERIFIED_CLAIMS)
    async def test_qualifiers_and_mechanisms_are_stated(
        self, landing, phrase
    ) -> None:
        assert phrase in landing.lower()

    @pytest.mark.parametrize("name", PRODUCT_NAMES)
    async def test_product_names_are_spelled_exactly(self, landing, name) -> None:
        assert name in landing


class TestLandingCspSelfContainment:
    async def test_no_inline_style_attributes(self, landing) -> None:
        """``style-src`` falls back to ``default-src 'self'``, so an inline
        ``style=`` attribute would be dropped by the browser."""
        assert _INLINE_STYLE_RE.search(landing) is None

    async def test_every_script_is_external_or_a_data_block(
        self, landing
    ) -> None:
        """A ``ld+json`` block is data, not executable script, so CSP allows it;
        an inline JS block would be blocked outright."""
        tags = _SCRIPT_OPEN_TAG_RE.findall(landing)
        assert tags, "landing page has no <script> tags at all"
        inline = [
            tag
            for tag in tags
            if "src=" not in tag and 'type="application/ld+json"' not in tag
        ]
        assert inline == []

    async def test_no_subresource_is_loaded_cross_origin(self, landing) -> None:
        """A remote stylesheet, script, or image is silently blocked by the
        policy unless its origin is one of the Google entries — and those exist
        solely for the consent-gated runtime injection, never for markup.
        Without this guard a copy edit that adds a CDN script or a remote badge
        image would ship broken (or, on a Google origin, consent-bypassing)
        with every other test still green.

        ``<link rel="canonical">`` is the one legitimate absolute URL here: it is
        metadata for crawlers, never a fetched subresource. OG/Twitter image URLs
        live in ``content=`` on ``<meta>`` tags, which this guard does not match.
        """
        offenders = []
        for tag in _SUBRESOURCE_TAG_RE.findall(landing):
            if 'rel="canonical"' in tag:
                continue
            url = _SUBRESOURCE_URL_RE.search(tag)
            if url is not None and url.group(1).startswith(("http://", "https://")):
                offenders.append(tag)
        assert offenders == []


_CONSENT_BANNER_RE = re.compile(
    r"<aside\b[^>]*\bdata-consent-banner\b[^>]*>(.*?)</aside>", re.DOTALL
)
_CONSENT_BUTTON_RE = re.compile(
    r"<button\b[^>]*\bdata-consent-(accept|decline)\b[^>]*>"
)


class TestConsentBanner:
    """The Google Ads tag is consent-gated: the served HTML carries only a
    hidden banner, and ``site.js`` injects ``gtag.js`` after Accept. These pin
    the server-rendered half of that contract."""

    async def test_google_tag_is_never_server_rendered(self, landing) -> None:
        """The CSP admits ``googletagmanager.com`` for the *post-consent* load
        only. A server-rendered reference would run for every visitor and
        bypass the consent gate — exactly the "paste the snippet into <head>"
        edit Google's setup instructions invite."""
        assert "googletagmanager" not in landing

    async def test_banner_ships_hidden(self, landing) -> None:
        """``site.js`` owns the reveal: with no stored choice it clears
        ``hidden``, and with scripting off the tag can never load, so the
        banner must never render visible on its own."""
        match = _CONSENT_BANNER_RE.search(landing)
        assert match is not None, "landing page has no consent banner"
        opening_tag = match.group(0)[: match.group(0).index(">") + 1]
        assert " hidden" in opening_tag

    async def test_banner_carries_both_hooks_site_js_binds(
        self, landing
    ) -> None:
        """``site.js`` refuses to reveal a banner missing either button, so a
        renamed ``data-`` hook would fail silently as a banner that never
        appears — and a consent flow nobody can complete."""
        buttons = _CONSENT_BUTTON_RE.findall(landing)
        assert sorted(buttons) == ["accept", "decline"]
        assert all(
            'type="button"' in tag
            for tag in (m.group(0) for m in _CONSENT_BUTTON_RE.finditer(landing))
        )

    async def test_banner_links_the_privacy_policy(self, landing) -> None:
        """Consent must be informed: the policy's Section 3.5 is where the
        Google Ads cookies are actually described."""
        match = _CONSENT_BANNER_RE.search(landing)
        assert match is not None
        assert 'href="/privacy.html"' in match.group(1)

    async def test_site_js_fires_the_page_view_conversion(self) -> None:
        """The tag id alone measures nothing. A Google Ads conversion action
        carries its own label, and only a ``conversion`` event naming it in
        ``send_to`` counts against it — without this the action reports "never
        received a tag ping" however healthy the base tag is. Queued after
        ``config``, which gtag needs in order to resolve ``send_to`` at all."""
        source = (resolve_static_dir() / "site" / "site.js").read_text()
        assert 'ADS_PAGE_VIEW_LABEL = "-6EGCOnoq-ccEMeVosdE"' in source
        assert 'send_to: ADS_TAG_ID + "/" + ADS_PAGE_VIEW_LABEL' in source
        assert source.index('gtagPush("config", ADS_TAG_ID)') < source.index(
            "send_to: ADS_TAG_ID"
        )

    async def test_footer_carries_the_withdrawal_control(self, landing) -> None:
        """GDPR Art. 7(3): withdrawing must be as easy as the one-click
        Accept, so the footer ships the "Cookie settings" reopener the policy's
        Section 3.5 points visitors at. ``site.js`` reveals it only after
        wiring it, so the markup's job is to exist with the hook."""
        buttons = re.findall(r"<button\b[^>]*\bdata-consent-settings\b[^>]*>", landing)
        assert len(buttons) == 1
        assert 'type="button"' in buttons[0]


class TestLandingSocialMetadata:
    async def test_twitter_card_is_a_large_image_summary(self, landing) -> None:
        assert 'name="twitter:card" content="summary_large_image"' in landing

    @pytest.mark.parametrize("pattern", ABSOLUTE_URL_PATTERNS)
    async def test_scraped_urls_are_absolute_on_the_site_origin(
        self, landing, config, pattern
    ) -> None:
        """Scrapers reject relative OG images, and ``public_base_url`` (the relay
        origin, ``api.mobaide.com`` in production) must never leak in here."""
        url = _capture(landing, pattern)
        assert url.startswith(f"{config.site_base_url}/"), url

    async def test_json_ld_block_parses_as_a_software_application(
        self, landing, config
    ) -> None:
        block = _capture(landing, _LD_JSON_RE)
        data = json.loads(block)
        assert data["@type"] == "SoftwareApplication"
        assert data["url"] == f"{config.site_base_url}/"


class TestCrawlerFiles:
    async def test_robots_txt_points_at_the_sitemap(self, client, config) -> None:
        response = await client.get("/robots.txt")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert f"Sitemap: {config.site_base_url}/sitemap.xml" in response.text

    @pytest.mark.parametrize("path", UNINDEXABLE_PATHS)
    async def test_robots_txt_disallows_non_public_surfaces(
        self, client, path
    ) -> None:
        response = await client.get("/robots.txt")
        assert f"Disallow: {path}" in response.text

    @pytest.mark.parametrize("path", PATHS_ROBOTS_MUST_NOT_NAME)
    async def test_robots_txt_does_not_disclose_gated_surfaces(
        self, client, path
    ) -> None:
        """robots.txt is public, so listing an auth-gated path only discloses it."""
        response = await client.get("/robots.txt")
        assert path not in response.text

    async def test_robots_txt_keeps_the_deletion_page_crawlable(
        self, client
    ) -> None:
        """``/portal/delete-account-info`` is anonymous, is the URL submitted to
        Play Console's Data Safety form, and is linked from this site's own
        footer — the blanket ``Disallow: /portal/`` must not swallow it. The
        ``Allow`` has to precede the disallows for longest-match to apply."""
        body = (await client.get("/robots.txt")).text
        assert "Allow: /portal/delete-account-info" in body
        assert body.index("Allow: /portal/delete-account-info") < body.index(
            "Disallow: /portal/"
        )

    async def test_sitemap_is_valid_xml_listing_the_apex(
        self, client, config
    ) -> None:
        response = await client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        locs = [
            element.text
            for element in ET.fromstring(response.text).findall(".//{*}loc")
        ]
        assert f"{config.site_base_url}/" in locs

    async def test_favicon_is_served_at_the_root_path(self, client) -> None:
        """Browsers and scrapers probe ``/favicon.ico`` regardless of the
        ``<link rel="icon">`` declaration."""
        response = await client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")


class TestAssetVersion:
    """The ``?v=`` query is what makes the 7-day site-asset TTL safe."""

    @staticmethod
    def _tree(root: Path) -> Path:
        site = root / "site"
        (site / "img").mkdir(parents=True)
        (site / "site.css").write_text("a{}")
        (site / "site.js").write_text("//")
        (site / "img" / "wordmark.png").write_bytes(b"\x89PNG-one")
        return site

    def test_version_tracks_asset_bytes_not_the_package_version(
        self, tmp_path
    ) -> None:
        """`deploy_central.py` ships the working tree while ``__version__`` only
        moves on a release, so two deploys can carry different assets under one
        version — and a repeat visitor would then pair fresh HTML with week-stale
        CSS. The query has to change when the bytes do."""
        site = self._tree(tmp_path)
        before = asset_version(tmp_path, "fallback")

        (site / "site.css").write_text("b{}")
        after = asset_version(tmp_path, "fallback")

        assert before != after
        assert "fallback" not in (before, after)

    def test_version_covers_images_not_just_css_and_js(self, tmp_path) -> None:
        """The template stamps ``?v=`` on the images too, so an image-only deploy
        must bust them — hashing an enumerated list of files would silently miss
        whatever the list drifted away from."""
        site = self._tree(tmp_path)
        before = asset_version(tmp_path, "fallback")

        (site / "img" / "wordmark.png").write_bytes(b"\x89PNG-two")

        assert asset_version(tmp_path, "fallback") != before

    def test_version_changes_when_an_asset_is_renamed(self, tmp_path) -> None:
        site = self._tree(tmp_path)
        before = asset_version(tmp_path, "fallback")

        (site / "img" / "wordmark.png").rename(site / "img" / "wordmark-v2.png")

        assert asset_version(tmp_path, "fallback") != before

    def test_missing_tree_degrades_to_the_fallback(self, tmp_path) -> None:
        """A partial checkout must not fail startup."""
        assert asset_version(tmp_path, "fallback") == "fallback"


class TestPublicRouteMethods:
    @pytest.mark.parametrize("path", MARKETING_PATHS)
    async def test_public_routes_answer_head(self, client, path) -> None:
        """Directory validators, link checkers and uptime monitors lead with
        HEAD, and FastAPI's ``APIRoute`` does not imply it — a bare
        ``@router.get`` answers 405 to every one of them."""
        response = await client.request("HEAD", path)
        assert response.status_code == 200


class TestStaticAssetCaching:
    async def test_site_assets_get_the_long_tier(self, client) -> None:
        response = await client.get("/static/site/site.css")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "public, max-age=604800"

    async def test_the_portal_sheet_gets_the_long_tier(self, client) -> None:
        """``portal.css`` lives under ``static/site/`` precisely so the ``?v=``
        content hash — which covers the whole tree — busts it on every edit. That
        is what earns it the 7-day TTL rather than the 1-hour default."""
        response = await client.get("/static/site/portal.css")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "public, max-age=604800"

    async def test_other_static_assets_get_the_short_tier(self, client) -> None:
        """The ADMIN DASHBOARD sheet carries no ``?v=`` query, so its TTL has to
        be short enough for a deploy to propagate on its own. The end-user portal
        is deliberately not on this tier — it loads the two versioned sheets
        under ``static/site/``."""
        response = await client.get("/static/style.css")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "public, max-age=3600"

    async def test_the_sri_pinned_bundle_gets_the_revalidate_tier(self, client) -> None:
        """``portal_base.html`` pins an SRI ``integrity`` hash on this bundle and
        the filename carries no version. If a client could reuse a cached copy
        without revalidating, the next htmx upgrade would pair new HTML (new
        hash) with stale JS, the browser would block htmx, and every portal form
        would silently stop working until the entry expired.

        ``dashboard.js`` shares this ``_MUST_REVALIDATE_ASSETS`` tier for an
        unrelated reason — a ``data-`` attribute contract with per-request
        markup, not SRI — covered instead by
        ``test_security_headers.TestExternalisedBundlesAreServed``."""
        response = await client.get("/static/htmx.min.js")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-cache"

    async def test_revalidation_keeps_the_cache_header(self, client) -> None:
        """Wrapping ``get_response`` rather than ``file_response`` is what makes
        the 304 path carry ``Cache-Control`` too — without it a revalidating
        client would forget the TTL and re-ask on every view."""
        first = await client.get("/static/site/site.css")
        second = await client.get(
            "/static/site/site.css",
            headers={"If-None-Match": first.headers["etag"]},
        )
        assert second.status_code == 304
        assert second.headers["Cache-Control"] == "public, max-age=604800"

    async def test_missing_asset_is_not_cached(self, client) -> None:
        """A negative-cache window would make a newly deployed asset
        unreachable for an hour."""
        response = await client.get("/static/site/not-a-real-asset.css")
        assert response.status_code == 404
        assert "Cache-Control" not in response.headers


class TestMarketingLoader:
    def test_render_landing_raises_when_the_template_is_missing(
        self, tmp_path: Path
    ) -> None:
        """Fail loud during ``create_app`` rather than boot and serve an empty
        home page — same contract as ``render_privacy``."""
        with pytest.raises(FileNotFoundError):
            render_landing(
                "https://example.test",
                "https://app.example.test",
                "0.0.0",
                templates_dir=tmp_path,
            )

    def test_render_robots_builds_the_sitemap_url_from_the_origin(self) -> None:
        robots = render_robots("https://example.test")
        assert "Sitemap: https://example.test/sitemap.xml" in robots
        assert "https://example.test//" not in robots

    def test_render_sitemap_lists_the_origin_root(self) -> None:
        locs = [
            element.text
            for element in ET.fromstring(
                render_sitemap("https://example.test")
            ).findall(".//{*}loc")
            if element.text is not None
        ]
        assert "https://example.test/" in locs
        assert [loc for loc in locs if "example.test//" in loc] == []


class TestSiteOriginNormalisation:
    def test_trailing_slash_in_site_base_url_is_stripped(self, config) -> None:
        """``SITE_BASE_URL=https://mobaide.com/`` is an easy operator mistake and
        would otherwise publish ``https://mobaide.com//`` canonical and OG URLs.

        ``object.__setattr__`` is how this frozen dataclass is poked in tests
        (see ``test_api_docs_exposure.py``).
        """
        object.__setattr__(config, "site_base_url", "https://example.test/")
        app = create_app(config=config)
        assert "<loc>https://example.test/</loc>" in app.state.marketing_sitemap
        assert "https://example.test//" not in app.state.marketing_html
        assert "https://example.test//" not in app.state.marketing_robots


class TestLandingInstallCommands:
    """The two install one-liners and the copy button beside each.

    Ported from the clients' install sheets — Android's ``InstallCommandCard``
    is the closest mirror, an icon-only button whose ``contentDescription``
    names the OS.
    """

    @pytest.mark.parametrize("command", INSTALL_COMMANDS)
    async def test_both_one_liners_are_rendered_verbatim(
        self, landing, command
    ) -> None:
        assert command in landing

    @pytest.mark.parametrize("command", INSTALL_COMMANDS)
    async def test_each_command_appears_exactly_once(self, landing, command) -> None:
        """``site.js`` reads the text out of the ``<code>`` element, so the
        button carries a bare ``data-copy`` hook and no copy of the string.

        A second occurrence means someone reintroduced ``data-copy="..."`` (or
        an equivalent duplicate), and the copied text can then silently drift
        from the text on screen — the failure mode a visitor cannot see.
        """
        assert landing.count(command) == 1

    async def test_every_command_has_its_own_copy_button(self, landing) -> None:
        buttons = _COPY_BUTTON_RE.findall(landing)
        assert len(buttons) == len(INSTALL_COMMANDS)
        assert all('type="button"' in button for button in buttons)

    async def test_copy_buttons_are_labelled_and_distinguishable(
        self, landing
    ) -> None:
        """The icon carries no text, so ``aria-label`` is the entire accessible
        name — and two buttons both reading "Copy" would leave a screen-reader
        user unable to tell which OS they are copying."""
        labels = [
            _ARIA_LABEL_RE.search(button) for button in _COPY_BUTTON_RE.findall(landing)
        ]
        assert all(label is not None for label in labels)
        names = [label.group(1) for label in labels if label is not None]
        assert len(set(names)) == len(INSTALL_COMMANDS)

    async def test_no_inline_event_handler_attributes(self, landing) -> None:
        """``onclick=`` is blocked by ``default-src 'self'`` just as an inline
        ``<script>`` is, and it fails silently in the browser — the behaviour
        belongs in ``static/site/site.js``."""
        assert _INLINE_HANDLER_RE.search(landing) is None



def _function_body(source: str, name: str) -> str:
    """The text of one top-level ``site.js`` function (up to the next one)."""
    start = source.index(f"function {name}(")
    end = source.find("\n  function ", start + 1)
    return source[start : end if end != -1 else len(source)]


class TestAttributionCookie:
    """Accept also records the landing click id in a first-party cookie the
    web app forwards at sign-up (privacy policy §3.5); Decline removes it."""

    def _source(self) -> str:
        return (resolve_static_dir() / "site" / "site.js").read_text()

    async def test_site_js_writes_the_attribution_cookie_inside_the_gated_flow(
        self,
    ) -> None:
        source = self._source()
        assert 'ADS_COOKIE_NAME = "mobaide_ads"' in source
        assert "ADS_COOKIE_MAX_AGE = 7776000" in source
        for param in ("gclid", "wbraid", "gbraid"):
            assert f'"{param}"' in source
        writer = _function_body(source, "writeAdsCookie")
        assert "SameSite=Lax" in writer
        assert 'domain=." + window.location.hostname' in writer
        assert '"; Secure"' in writer
        # Both write sites live inside the hostname-gated function, after
        # the gate: Accept and the stored-"granted" page load.
        banner = _function_body(source, "setUpConsentBanner")
        assert banner.index('hostname !== "mobaide.com"') < banner.index("writeAdsCookie()")
        assert banner.count("writeAdsCookie()") == 2

    async def test_site_js_clears_the_attribution_cookie_on_withdrawal(self) -> None:
        source = self._source()
        assert "clearAdsCookie()" in _function_body(source, "withdrawGoogleTag")

    async def test_attribution_cookie_name_is_never_server_rendered(
        self, landing
    ) -> None:
        """The cookie is written by script after Accept; a server-rendered
        reference would mean the HTML started carrying attribution logic."""
        assert "mobaide_ads" not in landing

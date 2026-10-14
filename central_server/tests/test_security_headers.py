"""Site-wide response security headers and the per-surface CSP and Permissions-Policy.

``SecurityHeadersMiddleware`` is registered last in ``create_app``, so it is the
outermost user middleware and stamps every response the inner stack produces —
including the 404s and redirects no route handler ever sees. What it stamps
depends only on the request path, so most of what follows is a matrix: one
policy per browser surface, the strict JSON policy for everything else, and no
CSP at all on ``/docs``.

Two of these can only fail silently in a browser console, which is why they
are pinned as exact strings rather than substring checks:

- the ``SITE`` policy has to stay byte-identical to the one
  ``marketing/router.py`` set per response before this middleware existed —
  a global policy that quietly loosened it would undo what already ships. The
  one sanctioned widening is ``LANDING``, scoped to ``/`` alone for the
  consent-gated Google Ads tag (byte-identical to
  ``test_marketing.EXPECTED_CSP``); the crawler files and ``/privacy.html``
  must keep the strict ``SITE`` string, which is what the matrix below pins;
- every served template has to stay free of inline ``<script>``, ``on*=``,
  ``hx-on:`` and ``style=``, because ``script-src 'self'`` / ``style-src 'self'``
  drop them with no server-side symptom at all.

``/portal/*`` and ``/dashboard/*`` now share one policy irrespective of
sign-in config — the portal no longer composes ``'unsafe-inline'`` or a
third-party origin in for Google or Apple, because those buttons moved to the
browser client. That sign-in-config independence is pinned in
``test_portal_retirement.TestPortalPolicyIsSignInConfigIndependent``; this file
sticks to the path-to-bucket matrix.

``Cross-Origin-Opener-Policy`` and ``Permissions-Policy`` belong to the same
category and raise the stakes: neither has a report-only counterpart to soak
behind, with a clean 200 on every request this suite can make. Both are pinned
by value here, and so is the copy the prod Caddyfiles serve to the SPA host,
which mounts the same-origin-independent sign-in flow now.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.datastructures import Headers

from central_server.config import CentralServerConfig
from central_server.security_headers import SecurityHeadersMiddleware
from central_server.templates_dir import resolve_templates_dir

SITE_CSP = (
    "default-src 'self'; base-uri 'none'; form-action 'none'; "
    "object-src 'none'; frame-ancestors 'none'"
)

# Byte-identical to ``security_headers._LANDING_CSP`` and to
# ``test_marketing.EXPECTED_CSP`` on purpose — see the module docstring. Keep
# the three in sync by changing none of them.
LANDING_CSP = (
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

API_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)

# ``/portal/*`` and ``/dashboard/*`` both take this, irrespective of sign-in
# config — see ``test_portal_retirement.SAME_ORIGIN_DOCUMENT_CSP``, which pins
# the identical literal against production so a loosening edit has to be made
# twice.
SAME_ORIGIN_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)

# Never the bare ``same-origin`` — see ``TestCrossOriginOpenerPolicy``.
COOP_POLICY = "same-origin-allow-popups"

UNIVERSAL_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", COOP_POLICY),
)

# Every surface, ``/portal/*`` and ``/dashboard/*`` included now that sign-in
# moved to the browser client. Spelled out rather than composed from the
# denied-feature list production shares between the two, so a rewrite of
# either half still has to reproduce this byte for byte.
BASELINE_PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "identity-credentials-get=()"
)

# Chrome answers each of these with `Error with Permissions-Policy header:
# Unrecognized feature` on every page load: FLoC's ``interest-cohort`` went in
# 2022 and its successor ``browsing-topics`` in Chrome 150.
RETIRED_FEATURE_NAMES = ("interest-cohort", "browsing-topics")

# ``/portal/login`` is gone; ``/portal/delete-account-info`` is the surviving
# anonymous page and takes the same bucket.
CSP_BY_PATH = (
    ("/", LANDING_CSP),
    ("/privacy.html", SITE_CSP),
    ("/robots.txt", SITE_CSP),
    ("/sitemap.xml", SITE_CSP),
    ("/favicon.ico", SITE_CSP),
    ("/portal/delete-account-info", SAME_ORIGIN_CSP),
    ("/dashboard/login", SAME_ORIGIN_CSP),
    ("/api/version", API_CSP),
    ("/static/style.css", API_CSP),
    ("/static/dashboard.js", API_CSP),
    ("/no-such-path", API_CSP),
)

# Every bucket denies FedCM outright now — the concession was
# sign-in-config-gated and both configs it used to gate on are gone from this
# middleware. ``/docs`` is listed because the CSP-exempt tuple is built by an
# early return that could drop the header, and ``/`` and ``/privacy.html``
# because they are two different site tiers.
PERMISSIONS_POLICY_BY_PATH = (
    ("/", BASELINE_PERMISSIONS_POLICY),
    ("/privacy.html", BASELINE_PERMISSIONS_POLICY),
    ("/portal/delete-account-info", BASELINE_PERMISSIONS_POLICY),
    ("/dashboard/login", BASELINE_PERMISSIONS_POLICY),
    ("/api/version", BASELINE_PERMISSIONS_POLICY),
    ("/static/style.css", BASELINE_PERMISSIONS_POLICY),
    ("/docs", BASELINE_PERMISSIONS_POLICY),
    ("/no-such-path", BASELINE_PERMISSIONS_POLICY),
)

# One per bucket, plus an exempt path and a 404: the universal headers are the
# only ones that must not depend on which bucket a path lands in.
REPRESENTATIVE_PATHS = (
    "/",
    "/portal/delete-account-info",
    "/dashboard/login",
    "/api/version",
    "/static/style.css",
    "/docs",
    "/no-such-path",
)

DOCS_PATHS = ("/docs", "/docs/", "/redoc", "/redoc/")

CSP_HEADERS = ("Content-Security-Policy", "Content-Security-Policy-Report-Only")

_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_OPEN_TAG_RE = re.compile(r"<script\b[^>]*>")
# Leading whitespace keeps this off `data-style=`-style attribute names.
_INLINE_STYLE_RE = re.compile(r"\sstyle\s*=")
_EVENT_HANDLER_RE = re.compile(r"\son[a-z]+\s*=")
_HX_ON_RE = re.compile(r"\shx-on")
_SUBRESOURCE_TAG_RE = re.compile(r"<(?:link|script|img)\b[^>]*>", re.DOTALL)
_SUBRESOURCE_URL_RE = re.compile(r'\s(?:src|href)="([^"]*)"')

# ``parents[2]`` walks tests/ → central_server/ → repo root. A checkout without
# ``deploy/`` — an installed sdist, say — skips the Caddy class rather than
# failing it.
PROD_CADDY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "prod"
PROD_CADDYFILES = ("Caddyfile.LetsEncrypt", "Caddyfile.CloudflareOrigin")

_CADDY_SNIPPET_RE = re.compile(
    r"^\(app_mobaide\) \{\n(.*?)^\}$", re.DOTALL | re.MULTILINE
)

# No policy in this app permits a cross-origin subresource any more — the
# Google and Apple sign-in buttons were the only reason one ever did, and they
# live on the browser client now. Anything found here is blocked at runtime by
# the very policy that makes this suite pass.
ALLOWED_CROSS_ORIGIN_SUBRESOURCES: tuple[str, ...] = ()

# Top-level glob only: ``templates/email/`` is deliberately excluded. Mail
# clients strip <style> blocks, so those templates have to inline every
# declaration, and no CSP of ours ever applies to them.
SERVED_TEMPLATES = tuple(
    pytest.param(path, id=path.name)
    for path in sorted(resolve_templates_dir().glob("*.html"))
)


@pytest.fixture
def config(config, request):
    """The shared config with the header-relevant fields pinned per class.

    ``object.__setattr__`` is how this frozen dataclass is poked in tests (see
    ``test_api_docs_exposure.py``). Both fields are read inside
    ``create_app``, so they have to be set before the ``app`` fixture runs.
    """
    for field, value in getattr(request, "param", {}).items():
        object.__setattr__(config, field, value)
    return config


report_only = pytest.mark.parametrize(
    "config", [{"csp_report_only": True}], indirect=True, ids=["report_only"]
)
docs_enabled = pytest.mark.parametrize(
    "config", [{"enable_api_docs": True}], indirect=True, ids=["docs_on"]
)


def _template_source(path) -> str:
    """Template text with comments removed.

    Every template that lost its inline code documents the ban in prose that
    names the banned construct ("an onclick= attribute would not run here"), so
    a scan over raw bytes fails on correct files.

    ``encoding`` is pinned because ``read_text()`` defaults to the locale
    encoding: on a Windows box running cp1252 these templates' curly quotes are
    undecodable, and the whole class errors out instead of checking anything.
    """
    source = path.read_text(encoding="utf-8")
    return _HTML_COMMENT_RE.sub("", _JINJA_COMMENT_RE.sub("", source))


def _app_mobaide_snippet(filename: str) -> str:
    """The body of the ``(app_mobaide)`` snippet in one prod Caddyfile."""
    match = _CADDY_SNIPPET_RE.search(
        (PROD_CADDY_DIR / filename).read_text(encoding="utf-8")
    )
    assert match is not None, f"no (app_mobaide) snippet in {filename}"
    return match.group(1)


def _caddy_header_value(snippet: str, name: str) -> str:
    """The one value the snippet sets for ``name``, unquoted.

    Caddy accepts a double-quoted or a backtick-quoted string; the
    Permissions-Policy value has to use backticks because it embeds the double
    quotes the FedCM origin allowlist needs. Finding anything other than
    exactly one line is itself a failure: the SPA's static ``header {}`` block
    is the only place in the snippet that may set these.
    """
    values = re.findall(rf"^\s*{re.escape(name)}\s+(.+)$", snippet, re.MULTILINE)
    assert len(values) == 1, f"expected one {name} line, found {len(values)}"
    raw = values[0].strip()
    quote = raw[0]
    assert quote in '"`' and raw.endswith(quote), f"unquoted {name} value: {raw}"
    unquoted = raw[1:-1]
    return unquoted.replace('\\"', '"') if quote == '"' else unquoted


async def _drive(middleware, scope) -> list[dict]:
    """Run one request through the middleware over a stub inner app."""
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


class TestPerSurfacePolicy:
    @pytest.mark.parametrize("path,policy", CSP_BY_PATH)
    async def test_each_surface_gets_its_own_policy(
        self, client, path, policy
    ) -> None:
        response = await client.get(path)
        assert response.headers["Content-Security-Policy"] == policy

    @pytest.mark.parametrize("path,policy", PERMISSIONS_POLICY_BY_PATH)
    async def test_each_surface_gets_its_permissions_policy(
        self, client, path, policy
    ) -> None:
        """Per-bucket rather than universal so a future surface with its own
        needs (a bucket that legitimately wants FedCM, say) is one line to add
        without touching the rest. It rides along on JSON and static responses
        where it is inert, on the same trade the universal headers make: no
        document surface can be missed."""
        response = await client.get(path)
        assert response.headers["Permissions-Policy"] == policy

    @pytest.mark.parametrize("header,value", UNIVERSAL_HEADERS)
    @pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
    async def test_the_universal_headers_are_bucket_independent(
        self, client, path, header, value
    ) -> None:
        """These carry no per-surface cost, so every bucket gets them — the
        static tree and the JSON API included, where ``nosniff`` is the header
        that actually matters and COOP is inert."""
        response = await client.get(path)
        assert response.headers[header] == value

    async def test_error_responses_are_stamped_too(self, client) -> None:
        """An ``HTTPException`` body is generated inside the stack, not by a
        route, and a 404 rendering attacker-supplied path text is exactly the
        response that must not be sniffed into HTML."""
        response = await client.get("/no-such-path")
        assert response.status_code == 404
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Content-Security-Policy"] == API_CSP


class TestPathMatchingIsAnchored:
    @pytest.mark.parametrize(
        "path,policy",
        (
            ("/portal", SAME_ORIGIN_CSP),
            ("/dashboard", SAME_ORIGIN_CSP),
            ("/portalling", API_CSP),
            ("/dashboards", API_CSP),
        ),
    )
    async def test_prefixes_match_only_on_the_separator(
        self, client, path, policy
    ) -> None:
        """A bare ``startswith("/portal")`` would hand a future ``/portalling``
        route the portal's same-origin document policy, while the exact
        ``/portal`` and ``/dashboard`` spellings still have to be treated as
        the surface they name."""
        response = await client.get(path)
        assert response.headers["Content-Security-Policy"] == policy


class TestApiDocsExemption:
    @pytest.mark.parametrize("path", DOCS_PATHS)
    @pytest.mark.parametrize("header", CSP_HEADERS)
    async def test_no_csp_is_set_on_the_doc_paths(
        self, client, path, header
    ) -> None:
        """Swagger and ReDoc are rendered from a cdn.jsdelivr.net bundle with an
        inline bootstrap script: any policy worth setting breaks them. The
        exemption is keyed on the path, so it holds whether the routes are
        registered or (as here, flag off) answering 404."""
        response = await client.get(path)
        assert header not in response.headers

    @pytest.mark.parametrize("header,value", UNIVERSAL_HEADERS)
    async def test_the_universal_headers_still_apply(
        self, client, header, value
    ) -> None:
        """Exempt from the CSP only — dropping ``nosniff`` and ``DENY`` here
        would be a second, unasked-for concession."""
        response = await client.get("/docs")
        assert response.headers[header] == value

    @docs_enabled
    @pytest.mark.parametrize("header", CSP_HEADERS)
    async def test_the_rendered_swagger_page_is_exempt(
        self, client, dashboard_headers, header
    ) -> None:
        """The 404 above cannot tell an exemption from a middleware that never
        ran, so pin the real 200 as well."""
        response = await client.get("/docs", headers=dashboard_headers)
        assert response.status_code == 200
        assert header not in response.headers


class TestCrossOriginOpenerPolicy:
    """The bare ``same-origin`` is the one wrong value with no server-side
    symptom at all: both values isolate this window from a cross-origin
    *opener*, but ``same-origin`` additionally drops the handle on popups we
    open. No surface this server renders opens one any more — sign-in moved to
    the browser client — but the stricter value would still fail a popup added
    to the portal or dashboard later as a blank window with nothing to see
    server-side, so it is pinned here rather than left to drift back.
    """

    async def test_the_portal_keeps_the_handle_a_future_popup_would_reply_through(
        self, client
    ) -> None:
        response = await client.get("/portal/delete-account-info")
        assert response.headers["Cross-Origin-Opener-Policy"] == COOP_POLICY

    def test_the_pinned_value_is_never_the_bare_same_origin(self) -> None:
        """Every other COOP assertion in this file — the universal-header
        matrix, the two beside this one, the report-only case and the Caddyfile
        mirror — compares against ``COOP_POLICY``, so each pins the response
        only relative to a constant an editor can move: hardening
        ``security_headers.py``, then this constant, then both Caddyfiles turns
        the whole suite green again. This is the one assertion that does not
        survive that sequence. The split is for the parameterised spelling —
        ``same-origin; report-to="coop"`` enforces ``same-origin`` too."""
        assert COOP_POLICY.split(";")[0].strip() != "same-origin"

    async def test_the_dashboard_takes_the_same_value(self, client) -> None:
        """A recorded decision rather than an oversight: ``/dashboard/*`` opens
        no popup today, so ``same-origin`` would protect against nothing that
        exists while breaking any popup added there later. One value everywhere
        also keeps navigation between the same-origin ``/``, ``/portal/*`` and
        ``/dashboard/*`` inside a single browsing context group."""
        response = await client.get("/dashboard/login")
        assert response.headers["Cross-Origin-Opener-Policy"] == COOP_POLICY


class TestPermissionsPolicyFeatureNames:
    @pytest.mark.parametrize("feature", RETIRED_FEATURE_NAMES)
    @pytest.mark.parametrize("path", ("/portal/delete-account-info", "/"))
    async def test_no_retired_feature_name_is_advertised(
        self, client, path, feature
    ) -> None:
        """Chrome is the only engine that reads this header, and it answers a
        name it no longer knows with a console error on every page load — the
        same console the deployment soak asks you to read clean."""
        response = await client.get(path)
        assert feature not in response.headers["Permissions-Policy"]


@report_only
class TestReportOnlyMode:
    @pytest.mark.parametrize(
        "path,policy", (("/portal/delete-account-info", SAME_ORIGIN_CSP),)
    )
    async def test_the_flag_swaps_the_header_name_only(
        self, client, path, policy
    ) -> None:
        """The soak deploy must observe the policy it is about to enforce, so
        the string may not differ between the two modes — and the enforcing
        header has to be gone, or the soak enforces after all."""
        response = await client.get(path)
        assert response.headers["Content-Security-Policy-Report-Only"] == policy
        assert "Content-Security-Policy" not in response.headers

    @pytest.mark.parametrize(
        "path,policy", (("/", LANDING_CSP), ("/robots.txt", SITE_CSP))
    )
    async def test_the_proven_buckets_stay_enforced(
        self, client, path, policy
    ) -> None:
        """These surfaces already shipped enforced in production, from
        ``marketing/router.py``, before this middleware existed. A soak is for
        a policy that has never been enforced, so the flag must not de-enforce
        a live one for the length of a soak deploy — they keep the enforcing
        header even while PORTAL/DASHBOARD/API swap to report-only. LANDING
        needs no soak of its own either: it is a strict superset of the proven
        SITE policy, so enforcing it can only unblock."""
        response = await client.get(path)
        assert response.headers["Content-Security-Policy"] == policy
        assert "Content-Security-Policy-Report-Only" not in response.headers

    async def test_the_flag_does_not_reach_the_other_two_headers(
        self, client
    ) -> None:
        """Report-only counterparts exist for both, but they report through the
        Reporting API alone and this deployment configures no collector, so the
        flag has to leave both enforcing under their own names — a rename would
        show up here as a missing header. Having no readable soak mode is the
        whole reason their rollout is a staged deploy and a manual walk of the
        surfaces instead."""
        response = await client.get("/portal/delete-account-info")
        assert response.headers["Cross-Origin-Opener-Policy"] == COOP_POLICY
        assert response.headers["Permissions-Policy"] == BASELINE_PERMISSIONS_POLICY


class TestReportOnlyConfig:
    @pytest.mark.parametrize(
        "raw,expected", ((None, False), ("1", True), ("true", True), ("0", False))
    )
    def test_csp_report_only_is_read_from_the_environment(
        self, monkeypatch, raw, expected
    ) -> None:
        """Enforcing is the default on purpose: a report-only default means the
        policy silently protects nobody until someone remembers to flip it."""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
        if raw is None:
            monkeypatch.delenv("CSP_REPORT_ONLY", raising=False)
        else:
            monkeypatch.setenv("CSP_REPORT_ONLY", raw)
        assert CentralServerConfig.from_env().csp_report_only is expected


class TestMiddlewareContract:
    async def test_a_response_that_sets_its_own_value_wins(self) -> None:
        """The headers go on with ``setdefault``: a route that has a reason to
        differ (an embeddable page relaxing ``X-Frame-Options``, say) must not
        have it silently overwritten, while the rest still apply."""

        async def inner(scope, receive, send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"x-frame-options", b"SAMEORIGIN")],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        sent = await _drive(
            SecurityHeadersMiddleware(inner),
            {"type": "http", "path": "/dashboard/login"},
        )

        headers = Headers(raw=sent[0]["headers"])
        assert headers["x-frame-options"] == "SAMEORIGIN"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["content-security-policy"] == SAME_ORIGIN_CSP

    async def test_scopes_without_a_path_pass_straight_through(self) -> None:
        """``add_middleware`` puts this in front of the lifespan scope too, and
        a lifespan scope has no ``path`` key — classifying one would raise at
        startup, before a single request."""
        seen: list[str] = []

        async def inner(scope, receive, send) -> None:
            seen.append(scope["type"])
            await send({"type": "lifespan.startup.complete"})

        sent = await _drive(SecurityHeadersMiddleware(inner), {"type": "lifespan"})

        assert seen == ["lifespan"]
        assert sent == [{"type": "lifespan.startup.complete"}]


class TestTemplatesCarryNoInlineCode:
    """Under ``script-src 'self'`` / ``style-src 'self'`` these constructs are
    dropped by the browser with nothing to show for it server-side, so the whole
    served template tree is scanned rather than the handful of files this change
    happened to touch. ``allowScriptTags: false`` in both base templates makes
    the ``<script>`` case worse still: an inline block in a swapped-in fragment
    would not even reach the policy."""

    @pytest.mark.parametrize("path", SERVED_TEMPLATES)
    def test_every_script_is_external_or_a_data_block(self, path) -> None:
        """A ``ld+json`` block is data, not executable script, so CSP permits
        it; an inline JS block is refused outright."""
        inline = [
            tag
            for tag in _SCRIPT_OPEN_TAG_RE.findall(_template_source(path))
            if "src=" not in tag and 'type="application/ld+json"' not in tag
        ]
        assert inline == []

    @pytest.mark.parametrize("path", SERVED_TEMPLATES)
    def test_no_inline_event_handler_attributes(self, path) -> None:
        """``onclick=`` and ``hx-on:`` are inline script by another name —
        ``hx-on:`` doubly so, since htmx compiles it with ``new Function`` and
        ``allowEval: false`` now stops that too. Behaviour belongs in
        ``dashboard.js``, keyed off ``data-`` attributes."""
        source = _template_source(path)
        assert _EVENT_HANDLER_RE.findall(source) == []
        assert _HX_ON_RE.findall(source) == []

    @pytest.mark.parametrize("path", SERVED_TEMPLATES)
    def test_no_inline_style_attributes(self, path) -> None:
        """``style-src 'self'`` covers every served template with no
        ``'unsafe-inline'`` anywhere — that concession lived on the portal for
        Google's own injected block and left with it."""
        assert _INLINE_STYLE_RE.search(_template_source(path)) is None

    @pytest.mark.parametrize("path", SERVED_TEMPLATES)
    def test_the_only_cross_origin_subresources_are_the_identity_provider_scripts(
        self, path
    ) -> None:
        """The dashboard used to pull htmx from ``unpkg.com``; under
        ``script-src 'self'`` that is a blank page, not a slow one. No policy
        rendered by this server names a third-party origin any more — the
        Google and Apple sign-in buttons that used to justify one moved to the
        browser client, so the allowlist is empty."""
        offenders = []
        for tag in _SUBRESOURCE_TAG_RE.findall(_template_source(path)):
            url = _SUBRESOURCE_URL_RE.search(tag)
            if url is None or not url.group(1).startswith(("http://", "https://")):
                continue
            if url.group(1) not in ALLOWED_CROSS_ORIGIN_SUBRESOURCES:
                offenders.append(url.group(1))
        assert offenders == []


class TestExternalisedBundlesAreServed:
    @pytest.mark.parametrize(
        "path,cache_control",
        (
            ("/static/site/site.js", "public, max-age=604800"),
            ("/static/dashboard.js", "no-cache"),
        ),
    )
    async def test_the_new_bundles_are_reachable_on_their_cache_tier(
        self, client, path, cache_control
    ) -> None:
        """Every behaviour the dashboard template used to inline now lives in
        ``dashboard.js``, and inline fallbacks no longer exist — a 404 here is a
        dead page. ``site.js`` sits under ``site/`` so the whole-tree ``?v=``
        content hash busts it, which is what earns it the 7-day tier;
        ``dashboard.js`` carries no version query and stays on the short one."""
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == cache_control


# The SPA's own Google/Apple sign-in concessions, unrelated to the retired
# server-rendered portal: Caddy stamps these for ``app.mobaide.com`` and this
# middleware never sees that request. Spelled out here rather than imported —
# ``security_headers.py`` no longer knows about either provider at all, now
# that the portal composed no policy for them. ``microphone=(self)`` is the
# SPA's voice input; every page this middleware serves keeps ``microphone=()``.
SPA_GIS_PERMISSIONS_POLICY = (
    "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), "
    'identity-credentials-get=(self "https://accounts.google.com")'
)
SPA_APPLE_JS_SCRIPT_URL = (
    "https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/"
    "appleid.auth.js"
)


@pytest.mark.skipif(
    not PROD_CADDY_DIR.is_dir(), reason="deploy/prod is not part of this checkout"
)
class TestCaddySpaHeadersMirrorTheProviderConcessions:
    """``app.mobaide.com`` serves the SPA off disk, so Caddy stamps its headers
    and this middleware never runs — yet the SPA mounts the Google and Apple
    sign-in buttons, in popup mode, on ``/login``, ``/register`` and
    ``/account``. Both prod Caddyfiles say in prose that their ``(app_mobaide)``
    snippet has to stay identical, because the deploy copies exactly one of the
    two files to the server whole; nothing else in the repo checks that."""

    def test_both_prod_caddyfiles_carry_the_same_snippet(self) -> None:
        """A header added to one file only ships on whichever certificate mode
        that server happens to run, and the divergence stays invisible until
        somebody reads the other file."""
        letsencrypt, cloudflare = (
            _app_mobaide_snippet(name) for name in PROD_CADDYFILES
        )
        assert letsencrypt == cloudflare

    @pytest.mark.parametrize("filename", PROD_CADDYFILES)
    def test_the_spa_permissions_policy_grants_the_fedcm_allowance(
        self, filename
    ) -> None:
        """The FedCM allowance is unconditional here, for the same reason the
        CSP's Google origin already is: Caddy cannot read ``GOOGLE_CLIENT_ID``,
        and the SPA's button never mounts when ``GET /api/version`` reports no
        client id."""
        snippet = _app_mobaide_snippet(filename)
        value = _caddy_header_value(snippet, "Permissions-Policy")
        assert value == SPA_GIS_PERMISSIONS_POLICY

    @pytest.mark.parametrize("filename", PROD_CADDYFILES)
    def test_the_spa_script_src_carries_the_apple_bundle(self, filename) -> None:
        """The SPA mounts the Apple button on ``/login``, ``/register`` and
        ``/account`` and loads the SDK itself, so Caddy has to allow the exact
        URL ``appleSignIn.ts`` injects. An origin-only entry would not match
        it, and the button would render onto a script that never runs."""
        snippet = _app_mobaide_snippet(filename)
        policy = _caddy_header_value(snippet, "Content-Security-Policy")
        script_src = next(
            directive.strip()
            for directive in policy.split(";")
            if directive.strip().startswith("script-src ")
        )
        assert SPA_APPLE_JS_SCRIPT_URL in script_src.split()

    @pytest.mark.parametrize("filename", PROD_CADDYFILES)
    def test_the_spa_opener_policy_matches_the_middlewares(self, filename) -> None:
        """Same popup, same failure mode: ``same-origin`` here would sever the
        opener the SPA's own sign-in popup posts its credential back through."""
        snippet = _app_mobaide_snippet(filename)
        value = _caddy_header_value(snippet, "Cross-Origin-Opener-Policy")
        assert value == COOP_POLICY

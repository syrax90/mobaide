"""Site-wide response security headers and the per-surface CSP.

``SecurityHeadersMiddleware`` is registered last in ``create_app``, so it is the
outermost user middleware and stamps every response the inner stack produces —
including the 404s and redirects no route handler ever sees. What it stamps
depends only on the request path, so most of what follows is a matrix: one
policy per browser surface, the strict JSON policy for everything else, and no
CSP at all on ``/docs``.

Three of these can only fail silently in a browser console, which is why they
are pinned as exact strings rather than substring checks:

- the ``SITE`` policy has to stay byte-identical to the one
  ``marketing/router.py`` set per response before this middleware existed
  (``test_marketing.EXPECTED_CSP``) — a global policy that quietly loosened it
  would undo what already ships;
- ``'unsafe-inline'`` may appear in exactly one policy, on ``/portal/*`` with a
  Google client id configured, and nowhere else;
- every served template has to stay free of inline ``<script>``, ``on*=``,
  ``hx-on:`` and ``style=``, because ``script-src 'self'`` / ``style-src 'self'``
  drop them with no server-side symptom at all.
"""
from __future__ import annotations

import re

import pytest
from starlette.datastructures import Headers

from central_server.config import CentralServerConfig
from central_server.security_headers import SecurityHeadersMiddleware, portal_csp
from central_server.templates_dir import resolve_templates_dir

GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"

# Byte-identical to ``test_marketing.EXPECTED_CSP`` on purpose — see the module
# docstring. Keep the two in sync by changing neither.
SITE_CSP = (
    "default-src 'self'; base-uri 'none'; form-action 'none'; "
    "object-src 'none'; frame-ancestors 'none'"
)

API_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)

# The dashboard always, and the portal on an instance with no Google client id.
SAME_ORIGIN_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)

GOOGLE_PORTAL_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://accounts.google.com/gsi/client; "
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; "
    "img-src 'self'; font-src 'self'; "
    "connect-src 'self' https://accounts.google.com/gsi/; "
    "frame-src https://accounts.google.com/gsi/; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)

UNIVERSAL_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("X-Frame-Options", "DENY"),
)

# The conftest config carries no Google client id, so the portal is on its
# strict fallback policy here; the configured shape is covered separately.
CSP_BY_PATH = (
    ("/", SITE_CSP),
    ("/privacy.html", SITE_CSP),
    ("/robots.txt", SITE_CSP),
    ("/sitemap.xml", SITE_CSP),
    ("/favicon.ico", SITE_CSP),
    ("/portal/login", SAME_ORIGIN_CSP),
    ("/dashboard/login", SAME_ORIGIN_CSP),
    ("/api/version", API_CSP),
    ("/static/style.css", API_CSP),
    ("/static/site/portal.js", API_CSP),
    ("/no-such-path", API_CSP),
)

# One per bucket, plus an exempt path and a 404: the universal three are the
# only headers that must not depend on which bucket a path lands in.
REPRESENTATIVE_PATHS = (
    "/",
    "/portal/login",
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

# The one cross-origin subresource any policy in this app permits. Anything else
# in a template — a CDN htmx, a remote font, an analytics beacon — is blocked at
# runtime by the very policy that makes this suite pass.
ALLOWED_CROSS_ORIGIN_SUBRESOURCES = ("https://accounts.google.com/gsi/client",)

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
    ``test_api_docs_exposure.py``). All three fields are read inside
    ``create_app``, so they have to be set before the ``app`` fixture runs.
    """
    for field, value in getattr(request, "param", {}).items():
        object.__setattr__(config, field, value)
    return config


with_google = pytest.mark.parametrize(
    "config", [{"google_client_id": GOOGLE_CLIENT_ID}], indirect=True, ids=["google"]
)
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
    """
    return _HTML_COMMENT_RE.sub("", _JINJA_COMMENT_RE.sub("", path.read_text()))


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

    @pytest.mark.parametrize("header,value", UNIVERSAL_HEADERS)
    @pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
    async def test_the_universal_three_are_bucket_independent(
        self, client, path, header, value
    ) -> None:
        """These carry no per-surface cost, so every bucket gets them — the
        static tree and the JSON API included, where ``nosniff`` is the header
        that actually matters."""
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
        route the portal's relaxations — including ``'unsafe-inline'`` once
        Google is configured — while the exact ``/portal`` and ``/dashboard``
        spellings still have to be treated as the surface they name."""
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


@with_google
class TestGoogleSignInRelaxations:
    async def test_the_portal_carries_the_gis_origins(self, client) -> None:
        response = await client.get("/portal/login")
        assert response.headers["Content-Security-Policy"] == GOOGLE_PORTAL_CSP

    @pytest.mark.parametrize(
        "path", ("/", "/privacy.html", "/dashboard/login", "/api/version")
    )
    async def test_no_other_surface_inherits_them(self, client, path) -> None:
        """``google_client_id`` is instance-wide but the concession is not: the
        admin dashboard and the two htmx-only site pages must keep a policy with
        no ``'unsafe-inline'`` and no third-party origin in it."""
        policy = (await client.get(path)).headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in policy
        assert "accounts.google.com" not in policy


class TestPortalPolicyWithoutGoogle:
    @pytest.mark.parametrize("client_id", (None, ""))
    def test_an_unconfigured_client_id_earns_the_strict_policy(
        self, client_id
    ) -> None:
        """``portal_base.html`` emits the GIS ``<script>`` only under
        ``{% if google_client_id %}``, so an instance without one — including
        the ``GOOGLE_CLIENT_ID=`` spelling, which is empty rather than unset —
        loads nothing that needs the relaxations and must not be granted them.
        """
        assert portal_csp(client_id) == SAME_ORIGIN_CSP


@report_only
class TestReportOnlyMode:
    @pytest.mark.parametrize("path,policy", (("/portal/login", SAME_ORIGIN_CSP),))
    async def test_the_flag_swaps_the_header_name_only(
        self, client, path, policy
    ) -> None:
        """The soak deploy must observe the policy it is about to enforce, so
        the string may not differ between the two modes — and the enforcing
        header has to be gone, or the soak enforces after all."""
        response = await client.get(path)
        assert response.headers["Content-Security-Policy-Report-Only"] == policy
        assert "Content-Security-Policy" not in response.headers

    async def test_the_site_bucket_stays_enforced(self, client) -> None:
        """SITE already shipped enforced in production, from
        ``marketing/router.py``, before this middleware existed. A soak is for
        a policy that has never been enforced, so the flag must not de-enforce
        a live one for the length of a soak deploy — SITE keeps the enforcing
        header even while PORTAL/DASHBOARD/API swap to report-only."""
        response = await client.get("/")
        assert response.headers["Content-Security-Policy"] == SITE_CSP
        assert "Content-Security-Policy-Report-Only" not in response.headers


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
        ``portal.js`` / ``dashboard.js``, keyed off ``data-`` attributes."""
        source = _template_source(path)
        assert _EVENT_HANDLER_RE.findall(source) == []
        assert _HX_ON_RE.findall(source) == []

    @pytest.mark.parametrize("path", SERVED_TEMPLATES)
    def test_no_inline_style_attributes(self, path) -> None:
        """``style-src 'self'`` covers the dashboard and the site pages with no
        ``'unsafe-inline'``, and the portal only concedes it for Google's own
        injected block — never for markup we control."""
        assert _INLINE_STYLE_RE.search(_template_source(path)) is None

    @pytest.mark.parametrize("path", SERVED_TEMPLATES)
    def test_the_only_cross_origin_subresource_is_the_gis_client(
        self, path
    ) -> None:
        """The dashboard used to pull htmx from ``unpkg.com``; under
        ``script-src 'self'`` that is a blank page, not a slow one. Google's
        sign-in client is the single origin any policy here allows."""
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
            ("/static/site/portal.js", "public, max-age=604800"),
            ("/static/dashboard.js", "no-cache"),
        ),
    )
    async def test_the_new_bundles_are_reachable_on_their_cache_tier(
        self, client, path, cache_control
    ) -> None:
        """Every behaviour the templates used to inline now lives in these two
        files, and inline fallbacks no longer exist — a 404 here is a dead page.
        ``portal.js`` sits under ``site/`` so the whole-tree ``?v=`` content hash
        busts it, which is what earns it the 7-day tier; ``dashboard.js`` carries
        no version query and stays on the short one."""
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == cache_control

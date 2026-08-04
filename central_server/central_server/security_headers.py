"""Site-wide response security headers, with a per-surface CSP and Permissions-Policy.

Pure ASGI rather than ``BaseHTTPMiddleware``: the only work is rewriting the
headers of ``http.response.start``, and ``BaseHTTPMiddleware`` — which
``_http_logging_middleware`` already is — adds a task group and a body stream
per request on the same event loop that relays live terminal traffic. Measured
on this app's response shape, that is ~2 µs here against ~125 µs for a no-op
``BaseHTTPMiddleware`` dispatch. Non-``http`` scopes — every ``/ws/*`` route,
including the live terminal relay — pass straight through with the original
``send``, so the relay's per-message path is untouched and a WebSocket handshake
response carries none of these headers.

Headers are applied with ``setdefault``, so a route that sets its own value
still wins.

``CSP_REPORT_ONLY`` does not soak ``Cross-Origin-Opener-Policy`` or
``Permissions-Policy``. Report-only counterparts do exist for both, but they
report through the Reporting API alone rather than the console, and this
deployment configures no collector — so there would be nothing to
read. Both therefore go straight to enforcing, and the only rehearsal is a
staged deploy plus a manual Google Sign-In. Each is set to a value that keeps
that flow working; the reasoning is at the constant, because getting either
wrong breaks sign-in outright rather than degrading it.

Two surfaces are deliberately not covered:

* An unhandled exception is turned into a 500 by Starlette's
  ``ServerErrorMiddleware``, which wraps the whole user middleware stack, so
  that response never passes back through here. Every ``HTTPException`` result
  (404 / 403 / 405) does.
* Classification reads the raw ``scope["path"]`` while Starlette routes on the
  ``root_path``-stripped path. Under a non-empty ``root_path`` — none is
  configured for this deployment — a portal or dashboard page would fall to the
  ``API`` bucket. That fails closed: the strictest policy in the file, visibly
  broken rather than quietly permissive.

The policies below are sized to what each surface actually loads. Two of those
constraints are not discoverable from the docs and were read out of the shipped
libraries:

* The Google Identity Services client appends an inline ``<style>`` to the
  *parent* document at load, before any API call, on top of the
  ``accounts.google.com/gsi/style`` sheet Google documents. That inline block is
  the only reason ``'unsafe-inline'`` appears anywhere in this file, and it is
  confined to ``/portal/*`` with Google configured. GIS needs no ``img-src``
  entry, no ``gstatic`` origin and no ``'unsafe-eval'``.
* htmx injects its ``.htmx-indicator`` ``<style>`` block at init unconditionally.
  ``includeIndicatorStyles: false`` in both base templates is what keeps
  ``style-src 'self'`` viable on the dashboard and on ``/privacy.html``; those
  three rules are shipped in ``static/style.css`` and ``static/site/portal.css``
  instead. Turning that config off without adding the rules would silently
  disable any future ``hx-indicator``.
"""
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_UNIVERSAL_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    # Redundant with every policy's `frame-ancestors 'none'` on a current
    # browser, and the fallback for one that predates CSP framing control.
    ("X-Frame-Options", "DENY"),
    # NOT `same-origin`. Both values isolate us from a cross-origin *opener*;
    # only `same-origin` additionally drops the handle on popups we open, which
    # is the handle the Google Sign-In popup posts its credential back through.
    # `/dashboard/*` opens no popup today and takes the same value anyway: one
    # value across every surface keeps navigation between the same-origin `/`,
    # `/portal/*` and `/dashboard/*` inside one browsing context group, and a
    # popup added to the dashboard later would fail under the stricter value
    # as a blank window with nothing to see server-side.
    ("Cross-Origin-Opener-Policy", "same-origin-allow-popups"),
)

_CSP_HEADER = "Content-Security-Policy"
_CSP_REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"

# Anonymous, form-free HTML plus the crawler files. Byte-identical to the policy
# marketing/router.py used to set per response: the landing page is the one
# surface already proven to satisfy it, and loosening it here would silently
# weaken what already ships. `/privacy.html` qualifies because legal/router.py
# never passes `google_client_id`, so its portal_base.html shell renders no
# Google tag and loads same-origin htmx alone.
_SITE_CSP = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)

# Everything that is not a browser document: /api/*, /proxy/*, /relay/*,
# /static/*, and any 404. `default-src 'none'` covers object-src, so it is not
# repeated; `form-action` and `frame-ancestors` have no default-src fallback and
# are.
_API_CSP = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

# The admin dashboard, and the portal on an instance with no Google client id —
# their subresource needs are identical once every inline script and style is
# externalised.
_SAME_ORIGIN_DOCUMENT_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

# Google documents the exact script path and the `/gsi/` parent for frames and
# XHR, and asks that individual endpoints under it not be listed separately.
_GIS_ORIGIN = "https://accounts.google.com"
_GIS_SCRIPT_URL = f"{_GIS_ORIGIN}/gsi/client"
_GIS_STYLE_URL = f"{_GIS_ORIGIN}/gsi/style"
_GIS_PARENT_URL = f"{_GIS_ORIGIN}/gsi/"

_PERMISSIONS_POLICY_HEADER = "Permissions-Policy"

# Chromium is the only engine that reads this header at all — Firefox and
# Safari ignore it — and it answers a name it does not know with `Error with
# Permissions-Policy header: Unrecognized feature` on every page load. That is
# the console the soak in docs/deployment.md asks you to read clean, so only
# names current Chrome still recognises appear here: `interest-cohort` (FLoC,
# dropped in 2022) and its successor `browsing-topics` (removed in Chrome 150)
# are retired and deliberately absent.
_DENIED_FEATURES = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

# `identity-credentials-get` is named rather than left off the list. Its
# default allowlist is `self`, so an empty one is what denies FedCM to our own
# same-origin documents too — containment for an injected script, on surfaces
# that have no use for the API.
_BASELINE_PERMISSIONS_POLICY = f"{_DENIED_FEATURES}, identity-credentials-get=()"

# The GIS button frame is created with `allow="identity-credentials-get"`, and
# a frame's `allow` only delegates what this document's own policy grants to
# that origin. So naming the feature obliges us to name Google's origin with
# it: `identity-credentials-get=(self)` reads like the tighter choice and is
# the one that refuses the delegation and takes the FedCM path down — the same
# shape of trap as `COOP: same-origin` above.
_GIS_PERMISSIONS_POLICY = (
    f'{_DENIED_FEATURES}, identity-credentials-get=(self "{_GIS_ORIGIN}")'
)

_SITE_PATHS = frozenset(
    {"/", "/robots.txt", "/sitemap.xml", "/favicon.ico", "/privacy.html"}
)

# The subset of `_SITE_PATHS` that marketing/router.py already served in
# production, so `CSP_REPORT_ONLY` deliberately cannot reach them. `/privacy.html`
# is NOT one: legal/router.py set no security header at all before this
# middleware, which makes its policy exactly as unproven as the portal's, and it
# extends the same portal_base.html shell — the surface most likely to grow a
# subresource. It soaks with the rest.
_PROVEN_SITE_PATHS = frozenset({"/", "/robots.txt", "/sitemap.xml", "/favicon.ico"})

# FastAPI renders both from a cdn.jsdelivr.net bundle with an inline bootstrap
# script, so any policy strict enough to be worth setting would break them, and
# a policy loose enough to fit is a list of third-party origins we do not
# control. They stay CSP-free and keep the universal headers. Both are
# unregistered unless ENABLE_API_DOCS is set, and admin-gated when it is.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/docs/", "/redoc", "/redoc/"})


def portal_csp(google_client_id: str | None) -> str:
    """Return the ``/portal/*`` policy for an instance with this Google config.

    With no client id, ``portal_base.html`` emits no GIS ``<script>`` and the
    portal earns the strict same-origin policy — including a ``style-src``
    without ``'unsafe-inline'``.
    """
    if not google_client_id:
        return _SAME_ORIGIN_DOCUMENT_CSP
    return (
        "default-src 'none'; "
        f"script-src 'self' {_GIS_SCRIPT_URL}; "
        f"style-src 'self' 'unsafe-inline' {_GIS_STYLE_URL}; "
        "img-src 'self'; "
        "font-src 'self'; "
        f"connect-src 'self' {_GIS_PARENT_URL}; "
        f"frame-src {_GIS_PARENT_URL}; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )


def portal_permissions_policy(google_client_id: str | None) -> str:
    """Return the ``/portal/*`` Permissions-Policy for this Google config.

    With no client id ``portal_base.html`` mounts no Google button, so the
    portal takes the same deny-most baseline every other surface gets and an
    instance without Google configured never advertises the FedCM allowance.
    """
    if not google_client_id:
        return _BASELINE_PERMISSIONS_POLICY
    return _GIS_PERMISSIONS_POLICY


class SecurityHeadersMiddleware:
    """Attach the universal hardening headers and the path-scoped policies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        google_client_id: str | None = None,
        report_only: bool = False,
    ) -> None:
        self.app = app
        # A soak is for a policy nobody has run yet. The four `_PROVEN_SITE_PATHS`
        # already served `_SITE_CSP` enforced in production, so report-only must
        # not switch a live protection back off for the length of a soak deploy.
        # Every other policy here is new and takes `soak_header`.
        soak_header = _CSP_REPORT_ONLY_HEADER if report_only else _CSP_HEADER

        def bucket(
            policy: str | None,
            header: str,
            permissions_policy: str = _BASELINE_PERMISSIONS_POLICY,
        ) -> tuple[tuple[str, str], ...]:
            headers = _UNIVERSAL_HEADERS + (
                (_PERMISSIONS_POLICY_HEADER, permissions_policy),
            )
            if policy is None:
                return headers
            return headers + ((header, policy),)

        # Resolved once per app, so the request path only chooses between
        # already-built tuples.
        self._site_enforced = bucket(_SITE_CSP, _CSP_HEADER)
        self._site_soakable = bucket(_SITE_CSP, soak_header)
        self._portal = bucket(
            portal_csp(google_client_id),
            soak_header,
            portal_permissions_policy(google_client_id),
        )
        self._dashboard = bucket(_SAME_ORIGIN_DOCUMENT_CSP, soak_header)
        self._api = bucket(_API_CSP, soak_header)
        # `bucket` ignores the header for a `None` policy; passing `soak_header`
        # is the safe bias if this bucket is ever given one.
        self._exempt = bucket(None, soak_header)

    def _headers_for(self, path: str) -> tuple[tuple[str, str], ...]:
        # Prefix tests are anchored on the separator: a bare `startswith`
        # would also claim a future `/portalling` route.
        if path in _PROVEN_SITE_PATHS:
            return self._site_enforced
        if path in _SITE_PATHS:
            return self._site_soakable
        if path == "/portal" or path.startswith("/portal/"):
            return self._portal
        if path == "/dashboard" or path.startswith("/dashboard/"):
            return self._dashboard
        if path in _CSP_EXEMPT_PATHS:
            return self._exempt
        return self._api

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = self._headers_for(scope["path"])

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers:
                    response_headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)

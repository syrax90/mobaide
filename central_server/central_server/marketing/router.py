"""Root-mounted public marketing site (no prefix).

Sibling of :mod:`central_server.legal.router`: both serve anonymous HTML off the
apex domain from content pre-rendered into ``app.state`` during ``create_app``.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter(tags=["marketing"])

# GET *and* HEAD on every route. FastAPI's ``APIRoute`` does not imply HEAD the
# way Starlette's plain ``Route`` does, so a bare ``@router.get`` answers 405 —
# and this page exists to be probed by directory validators, link checkers and
# uptime monitors, many of which lead with HEAD.
_PUBLIC_METHODS = ["GET", "HEAD"]

# 5 minutes, same rationale as legal/router.py: short enough that a copy
# correction propagates within the window, long enough to absorb a traffic
# spike. The HTML is already pre-rendered, so this only bounds staleness.
_CACHE_CONTROL = "public, max-age=300"

# A day: the icon changes only with the brand, and a stale favicon is harmless.
_FAVICON_CACHE_CONTROL = "public, max-age=86400"

# An hour: crawler files are cheap to refetch and we want a sitemap change to
# be picked up quickly after a deploy.
_CRAWLER_CACHE_CONTROL = "public, max-age=3600"

# Cheap and unconditionally safe, so every response in this router gets it —
# `/sitemap.xml` and `/robots.txt` are the ones that actually benefit, being
# attacker-uncontrolled text served to browsers.
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}

# Scoped to the landing response, NOT installed as middleware. A global CSP would
# break `/portal/login`, which loads Google Sign-In from accounts.google.com
# (templates/portal_base.html) and has inline <script>/style= that a policy
# without 'unsafe-inline' would block. Site-wide headers are tracked separately.
#
# The landing page needs no relaxations to satisfy this policy: its CSS and JS
# are external same-origin files, its icons are inline <svg> markup rather than
# data: URIs, its font is same-origin, and the JSON-LD block is a data block
# that CSP does not treat as executable script.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "object-src 'none'; "
        "frame-ancestors 'none'"
    ),
    **_NOSNIFF,
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@router.api_route("/", methods=_PUBLIC_METHODS, response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    response = HTMLResponse(request.app.state.marketing_html)
    response.headers["Cache-Control"] = _CACHE_CONTROL
    response.headers.update(_SECURITY_HEADERS)
    return response


@router.api_route(
    "/robots.txt", methods=_PUBLIC_METHODS, response_class=PlainTextResponse
)
async def robots_txt(request: Request) -> PlainTextResponse:
    response = PlainTextResponse(request.app.state.marketing_robots)
    response.headers["Cache-Control"] = _CRAWLER_CACHE_CONTROL
    response.headers.update(_NOSNIFF)
    return response


@router.api_route("/sitemap.xml", methods=_PUBLIC_METHODS)
async def sitemap_xml(request: Request) -> Response:
    response = Response(
        request.app.state.marketing_sitemap,
        media_type="application/xml",
    )
    response.headers["Cache-Control"] = _CRAWLER_CACHE_CONTROL
    response.headers.update(_NOSNIFF)
    return response


@router.api_route("/favicon.ico", methods=_PUBLIC_METHODS)
async def favicon(request: Request) -> Response:
    """Serve the icon at the root path browsers and scrapers probe by default.

    The document also declares ``<link rel="icon">``, but plenty of clients ask
    for ``/favicon.ico`` regardless, and answering here keeps that out of the
    404 logs.

    Served from the bytes read once at startup rather than through
    ``FileResponse``: this route is anonymous and unrate-limited, and a
    per-request ``stat`` would put a blocking syscall on the same event loop
    that relays live terminal traffic.
    """
    icon = request.app.state.marketing_favicon
    if icon is None:
        return Response(status_code=404)
    response = Response(icon, media_type="image/x-icon")
    response.headers["Cache-Control"] = _FAVICON_CACHE_CONTROL
    response.headers.update(_NOSNIFF)
    return response

"""Root-mounted public legal pages (no ``/portal`` prefix)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..templates_dir import resolve_templates_dir

router = APIRouter(tags=["legal"])

templates = Jinja2Templates(directory=str(resolve_templates_dir()))

# 5 minutes: short enough that an emergency policy correction propagates
# within the cache window, long enough to absorb a traffic spike without
# re-rendering. The HTML itself is already pre-rendered into app.state.
_CACHE_CONTROL = "public, max-age=300"


@router.get("/privacy.html", response_class=HTMLResponse)
async def privacy_page(request: Request) -> HTMLResponse:
    policy_html = request.app.state.privacy_html
    policy_toc_html = request.app.state.privacy_toc_html
    response = templates.TemplateResponse(
        request,
        "privacy.html",
        {
            "policy_html": policy_html,
            "policy_toc_html": policy_toc_html,
            # This page extends portal_base.html, which stamps the shared ``?v=``
            # on both stylesheets and the wordmark. ``Jinja2Templates`` uses the
            # permissive default ``Undefined``, so omitting this key renders a
            # bare ``?v=`` and silently disables cache busting on a page whose
            # assets carry a 7-day TTL.
            "asset_version": request.app.state.asset_version,
            # Same permissive-Undefined hazard as `asset_version`: omit this and
            # the shell's Sign in button renders `href="/login"`, a same-origin
            # path that does not exist on this host.
            "app_origin": request.app.state.web_app_origin,
        },
    )
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return response

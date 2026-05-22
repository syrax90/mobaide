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
    response = templates.TemplateResponse(
        request,
        "privacy.html",
        {"policy_html": policy_html},
    )
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return response

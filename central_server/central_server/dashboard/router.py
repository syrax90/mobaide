"""Web dashboard routes — Jinja2 + HTMX."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth.dependencies import get_dashboard_user

logger = logging.getLogger(__name__)

# Templates live in central_server/templates/ (sibling of the package dir).
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/dashboard")


# -- Login/Logout --------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    auth_service = request.app.state.auth_service

    if not auth_service.verify_dashboard_credentials(str(username), str(password)):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid credentials"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = auth_service.create_dashboard_token(str(username))
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="dashboard_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=request.app.state.config.dashboard_token_expire_minutes * 60,
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(
        url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("dashboard_token")
    return response


# -- Agent list ----------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def agents_page(
    request: Request, username: str = Depends(get_dashboard_user)
):
    db = request.app.state.db
    routing = request.app.state.routing
    agents = await db.list_agents()
    for agent in agents:
        agent["online"] = routing.is_agent_online(agent["agent_id"])
    return templates.TemplateResponse(
        request,
        "agents.html",
        {"agents": agents, "username": username},
    )


# -- Agent registration (dashboard form) — MUST be before {agent_id} routes ---


@router.get("/agents/register", response_class=HTMLResponse)
async def register_agent_page(
    request: Request, username: str = Depends(get_dashboard_user)
):
    return templates.TemplateResponse(
        request, "register_agent.html", {"username": username}
    )


@router.post("/agents/register", response_class=HTMLResponse)
async def register_agent_submit(
    request: Request, username: str = Depends(get_dashboard_user)
):
    form = await request.form()
    name = str(form.get("name", ""))
    if not name:
        return templates.TemplateResponse(
            request,
            "register_agent.html",
            {"username": username, "error": "Name is required"},
        )

    db = request.app.state.db
    agent_id, raw_token = await db.register_agent(name)
    logger.info("dashboard.agent_registered agent_id=%s name=%s", agent_id, name)
    return templates.TemplateResponse(
        request,
        "register_agent.html",
        {"username": username, "agent_id": agent_id, "token": raw_token},
    )


# -- Agent detail (parameterized — AFTER static /agents/register routes) -------


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail_page(
    request: Request,
    agent_id: str,
    username: str = Depends(get_dashboard_user),
):
    db = request.app.state.db
    routing = request.app.state.routing
    pairing_service = request.app.state.pairing_service

    agent = await db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent["online"] = routing.is_agent_online(agent_id)
    pairings = await pairing_service.list_pairings(agent_id)
    active_pairings = [p for p in pairings if p.get("revoked_at") is None]
    pending_tokens = await pairing_service.list_pending_tokens(agent_id)

    return templates.TemplateResponse(
        request,
        "agent_detail.html",
        {
            "agent": agent,
            "pairings": active_pairings,
            "pending_tokens": pending_tokens,
            "username": username,
        },
    )


# -- Pairing actions (HTMX partials) ------------------------------------------


@router.post("/agents/{agent_id}/pair", response_class=HTMLResponse)
async def generate_pairing_qr(
    request: Request,
    agent_id: str,
    username: str = Depends(get_dashboard_user),
):
    pairing_service = request.app.state.pairing_service
    raw_token, expires_at = await pairing_service.create_pairing_token(agent_id)

    # Determine server URL from request
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    server_url = f"{scheme}://{host}"

    qr_data = pairing_service.generate_qr_data(raw_token, agent_id, server_url)
    qr_base64 = pairing_service.generate_qr_image_base64(qr_data)

    return templates.TemplateResponse(
        request,
        "partials/pairing_qr.html",
        {
            "qr_base64": qr_base64,
            "raw_token": raw_token,
            "expires_at": expires_at,
        },
    )


@router.delete("/pairings/{pairing_id}", response_class=HTMLResponse)
async def revoke_pairing_htmx(
    request: Request,
    pairing_id: str,
    username: str = Depends(get_dashboard_user),
):
    pairing_service = request.app.state.pairing_service
    await pairing_service.revoke_pairing(pairing_id)
    return HTMLResponse("")  # HTMX removes the row

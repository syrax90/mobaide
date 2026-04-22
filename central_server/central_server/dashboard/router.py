"""Web dashboard routes — Jinja2 + HTMX."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth.dependencies import require_role
from ..auth.models import UserTokenPayload
from ..auth.service import VerificationRequiredError
from ..templates_dir import resolve_templates_dir

_require_admin = require_role("admin")

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(resolve_templates_dir()))

router = APIRouter(prefix="/dashboard")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    email = form.get("username", "")  # HTML field name is "username", not "email"
    password = form.get("password", "")
    auth_service = request.app.state.auth_service

    try:
        user_id, role, token = await auth_service.login_user(
            str(email), str(password)
        )
    except VerificationRequiredError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Please verify your email before signing in."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    except ValueError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid credentials"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    ttl_minutes = request.app.state.config.dashboard_token_expire_minutes
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="dashboard_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        # ttl_minutes == 0 means "never expires" — omit Max-Age so the
        # browser treats this as a session cookie (RFC 6265 §4.1.2).
        # Passing Max-Age=0 would instruct the browser to delete the
        # cookie immediately (RFC 6265 §5.2.2).
        max_age=ttl_minutes * 60 if ttl_minutes > 0 else None,
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(
        url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("dashboard_token")
    return response


@router.get("", response_class=HTMLResponse)
async def agents_page(
    request: Request, user: UserTokenPayload = Depends(_require_admin)
):
    db = request.app.state.db
    routing = request.app.state.routing
    agents = await db.list_agents_with_owner()
    for agent in agents:
        agent["online"] = routing.is_agent_online(agent["agent_id"])
    return templates.TemplateResponse(
        request,
        "agents.html",
        {"agents": agents, "username": user.email},
    )


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail_page(
    request: Request,
    agent_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    db = request.app.state.db
    routing = request.app.state.routing
    pairing_service = request.app.state.pairing_service

    agent = await db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent["online"] = routing.is_agent_online(agent_id)

    owner_email = None
    if agent.get("owner_user_id"):
        owner = await db.get_user_by_id(agent["owner_user_id"])
        if owner:
            owner_email = owner["email"]

    pairings = await pairing_service.list_pairings(agent_id)
    active_pairings = [p for p in pairings if p.get("revoked_at") is None]

    return templates.TemplateResponse(
        request,
        "agent_detail.html",
        {
            "agent": agent,
            "owner_email": owner_email,
            "pairings": active_pairings,
            "username": user.email,
        },
    )


@router.delete("/pairings/{pairing_id}", response_class=HTMLResponse)
async def revoke_pairing_htmx(
    request: Request,
    pairing_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    del user  # suppress unused-variable warning; Depends() enforces the role
    pairing_service = request.app.state.pairing_service
    await pairing_service.revoke_pairing(pairing_id)
    return HTMLResponse("")  # HTMX removes the row

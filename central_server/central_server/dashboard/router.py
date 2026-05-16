"""Web dashboard routes — Jinja2 + HTMX."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth.dependencies import require_role
from ..auth.models import UserTokenPayload
from ..auth.service import VerificationRequiredError
from ..relay.routing import (
    WS_CLOSE_ADMIN_FORCE_DISCONNECT,
    WS_CLOSE_ADMIN_REVOKED,
)
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


def _enrich_users(users: list[dict], routing) -> None:
    """Annotate each user dict in-place with derived UI fields.

    Adds ``agent_count``, ``online_count``, and ``last_activity`` to each
    user, and ``online`` to each nested agent. Pure compute on already-fetched
    data — no I/O.
    """
    for user in users:
        online = 0
        last_seen: str | None = None
        for agent in user["agents"]:
            agent["online"] = routing.is_agent_online(agent["agent_id"])
            if agent["online"]:
                online += 1
            seen = agent.get("last_seen_at")
            if seen is not None and (last_seen is None or seen > last_seen):
                last_seen = seen
        user["agent_count"] = len(user["agents"])
        user["online_count"] = online
        user["last_activity"] = last_seen


@router.get("", response_class=HTMLResponse)
async def users_page(
    request: Request, user: UserTokenPayload = Depends(_require_admin)
):
    db = request.app.state.db
    routing = request.app.state.routing
    users = await db.list_users_with_agents()
    _enrich_users(users, routing)
    return templates.TemplateResponse(
        request,
        "users.html",
        {"users": users, "username": user.email, "query": ""},
    )


@router.get("/users", response_class=HTMLResponse)
async def users_table_fragment(
    request: Request,
    q: str = "",
    user: UserTokenPayload = Depends(_require_admin),
):
    """Return the users-table fragment, optionally filtered by email substring.

    Wired to the search input via ``hx-get`` + ``hx-trigger="keyup changed
    delay:300ms"`` so admins can narrow the list to a single email as they
    type. The filter is case-insensitive substring match, applied in Python
    on the already-loaded list (admin dashboards have small populations).
    """
    del user  # Depends() enforces the role; the variable is otherwise unused.
    db = request.app.state.db
    routing = request.app.state.routing
    users = await db.list_users_with_agents()
    needle = q.strip().lower()
    if needle:
        users = [u for u in users if needle in u["email"].lower()]
    _enrich_users(users, routing)
    return templates.TemplateResponse(
        request,
        "_users_table.html",
        {"users": users, "query": q},
    )


@router.get(
    "/users/{user_id}/agents", response_class=HTMLResponse
)
async def user_agents_fragment(
    request: Request,
    user_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    """Return the inline agent sub-table for a user.

    Rendered as a ``<tr class="detail-row">`` with ``hx-swap="afterend"``,
    keyed on ``user_id`` so the expansion survives parent refreshes via
    ``hx-preserve``.
    """
    del user
    db = request.app.state.db
    routing = request.app.state.routing
    target = await db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    agents = await db.list_agents_for_user(user_id)
    for agent in agents:
        agent["online"] = routing.is_agent_online(agent["agent_id"])
    return templates.TemplateResponse(
        request,
        "_user_agents_fragment.html",
        {"user": target, "agents": agents},
    )


@router.get(
    "/users/{user_id}/delete-modal", response_class=HTMLResponse
)
async def delete_user_modal(
    request: Request,
    user_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    """Server-rendered modal for the type-the-email confirm flow.

    Returns the modal body even when one of the guards (seed admin /
    self-delete) would fail on submit, so the admin sees the actual
    reason rather than a silent no-op. The DELETE endpoint re-validates
    these guards server-side.
    """
    db = request.app.state.db
    config = request.app.state.config
    target = await db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    agents = await db.list_agents_for_user(user_id)
    is_seed_admin = (
        config.admin_seed_email is not None
        and target["email"] == config.admin_seed_email
    )
    is_self = user.sub == user_id
    return templates.TemplateResponse(
        request,
        "_delete_user_modal.html",
        {
            "user": target,
            "agents": agents,
            "is_seed_admin": is_seed_admin,
            "is_self": is_self,
        },
    )


@router.delete(
    "/users/{user_id}", response_class=HTMLResponse
)
async def delete_user_htmx(
    request: Request,
    user_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    """Cascade-delete a user and tear down their live WebSockets.

    Refuses to delete the seed admin or the currently-logged-in user
    (returns 400 with a clear message). Otherwise: cascades the deletes
    in the DB layer, then closes each agent WS and every user-scoped
    mobile-client WS with code 4010 and a ``{"reason": "admin_revoked"}``
    frame.
    """
    db = request.app.state.db
    routing = request.app.state.routing
    config = request.app.state.config

    target = await db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.sub == user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete your own account while logged in",
        )
    if (
        config.admin_seed_email is not None
        and target["email"] == config.admin_seed_email
    ):
        raise HTTPException(
            status_code=400, detail="Cannot delete the seed admin account"
        )

    agent_ids = await db.delete_user(user_id)
    for agent_id in agent_ids:
        await routing.disconnect_agent(
            agent_id, WS_CLOSE_ADMIN_REVOKED, "admin_revoked"
        )
    await routing.disconnect_user(
        user_id, WS_CLOSE_ADMIN_REVOKED, "admin_revoked"
    )
    logger.info(
        "user.deleted user_id=%s by_admin=%s agents=%d",
        user_id, user.sub, len(agent_ids),
    )
    return HTMLResponse("")  # HTMX deletes the user row


@router.delete(
    "/agents/{agent_id}", response_class=HTMLResponse
)
async def admin_delete_agent_htmx(
    request: Request,
    agent_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    """Admin-path agent delete — bypasses the owner check.

    Distinct from ``DELETE /api/agents/{agent_id}`` which is owner-gated
    for the mobile app. The dashboard caller passes ``as_admin=True`` so
    the service skips ``is_agent_owner`` and proceeds straight to
    teardown.
    """
    agent_service = request.app.state.agent_service
    agent = await request.app.state.db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await agent_service.delete_agent(agent_id, user.sub, as_admin=True)
    return HTMLResponse("")  # HTMX deletes the agent row


@router.post(
    "/users/{user_id}/disconnect", response_class=HTMLResponse
)
async def force_disconnect_user(
    request: Request,
    user_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    """Force-disconnect every user-scoped WS for ``user_id`` (DB untouched).

    Used to boot a stuck or misbehaving mobile-client connection
    without revoking credentials. Refresh tokens stay valid; the client
    can reconnect.
    """
    del user
    routing = request.app.state.routing
    closed = await routing.disconnect_user(
        user_id,
        WS_CLOSE_ADMIN_FORCE_DISCONNECT,
        "admin_force_disconnect",
    )
    logger.info(
        "user.force_disconnected user_id=%s closed=%d", user_id, closed
    )
    return HTMLResponse("")


@router.post(
    "/agents/{agent_id}/disconnect", response_class=HTMLResponse
)
async def force_disconnect_agent(
    request: Request,
    agent_id: str,
    user: UserTokenPayload = Depends(_require_admin),
):
    """Force-disconnect a live agent WS (DB untouched).

    Closes the agent socket plus every paired mobile-client socket with
    code 4001. The agent's stored credentials remain valid; the next
    reconnect picks up where it left off.
    """
    del user
    routing = request.app.state.routing
    closed = await routing.disconnect_agent(
        agent_id,
        WS_CLOSE_ADMIN_FORCE_DISCONNECT,
        "admin_force_disconnect",
    )
    logger.info(
        "agent.force_disconnected agent_id=%s closed=%d",
        agent_id, closed,
    )
    return HTMLResponse("")


@router.get("/agents-status", response_class=HTMLResponse)
async def agents_status_oob(
    request: Request, user: UserTokenPayload = Depends(_require_admin)
):
    """Return OOB ``<span hx-swap-oob="true">`` fragments for live status.

    Drives the 10 s status refresh from a single hidden poller in
    ``users.html`` without churning the rest of the DOM (so expanded
    detail rows are not collapsed). Emits three classes of span by id:

    * ``agent-status-{agent_id}`` — the online/offline badge.
    * ``user-online-{user_id}`` — count of currently-online agents.
    * ``user-last-activity-{user_id}`` — max ``last_seen_at`` across
      the user's agents, or em-dash.

    ``<span>`` is chosen deliberately (not ``<tr>``/``<td>``) to sidestep
    the documented ``<tbody>`` OOB-swap quirk in htmx#1900.
    """
    del user
    db = request.app.state.db
    routing = request.app.state.routing
    users = await db.list_users_with_agents()
    _enrich_users(users, routing)
    return templates.TemplateResponse(
        request,
        "_agent_status_oob.html",
        {"users": users},
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

    manifest_store = getattr(request.app.state, "manifest_store", None)
    manifest_record = manifest_store.read() if manifest_store is not None else None
    latest_agent_version = (
        manifest_record.body.get("version") if manifest_record is not None else None
    )

    return templates.TemplateResponse(
        request,
        "agent_detail.html",
        {
            "agent": agent,
            "owner_email": owner_email,
            "pairings": active_pairings,
            "latest_agent_version": latest_agent_version,
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

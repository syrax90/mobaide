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
from .listing import (
    DEFAULT_DIRECTION,
    DEFAULT_PER_PAGE,
    DEFAULT_SORT,
    UsersListing,
)

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
        user_id, role, token, _scheduled = await auth_service.login_user(
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

    claims = auth_service.decode_dashboard_token(token)
    token = auth_service.issue_admin_dashboard_session(user_id, claims.email, role)
    ttl_minutes = request.app.state.config.admin_dashboard_token_expire_minutes
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


async def _listing_params(
    q: str = "",
    verified: str = "all",
    sort: str = DEFAULT_SORT,
    dir: str = DEFAULT_DIRECTION,
    page: str = "1",
    per_page: str = str(DEFAULT_PER_PAGE),
) -> UsersListing:
    """Dependency parsing the listing state off the query string.

    Every parameter is typed ``str`` and parsed leniently by
    ``UsersListing.from_params`` — a tampered URL degrades to the default
    view rather than a 422 swapped into the table.
    """
    return UsersListing.from_params(
        q=q, verified=verified, sort=sort, direction=dir,
        page=page, per_page=per_page,
    )


async def _load_listing(
    request: Request, listing: UsersListing
) -> tuple[list[dict], int, UsersListing]:
    """Fetch one page of enriched users for ``listing``.

    Returns ``(users, total, listing)`` where ``listing`` has its page
    clamped to the last one — the templates must render from *that*
    instance so every link and the poller URL describe the page actually
    shown.
    """
    db = request.app.state.db
    routing = request.app.state.routing
    total = await db.count_users(q=listing.q, verified=listing.verified_filter)
    listing = listing.clamp(total)
    users = await db.list_users_with_agents(
        q=listing.q,
        verified=listing.verified_filter,
        sort=listing.sort,
        descending=listing.descending,
        limit=listing.per_page,
        offset=listing.offset,
    )
    _enrich_users(users, routing)
    return users, total, listing


def _listing_context(
    users: list[dict], total: int, listing: UsersListing
) -> dict:
    return {
        "users": users,
        "total": total,
        "listing": listing,
        "query": listing.q,
        "verified": listing.verified,
    }


@router.get("", response_class=HTMLResponse)
async def users_page(
    request: Request,
    listing: UsersListing = Depends(_listing_params),
    user: UserTokenPayload = Depends(_require_admin),
):
    """The users page. Takes the same listing parameters as the fragment
    so the URLs htmx pushes and replaces reload to the same view.

    The boosted sort/pager links append the live search controls after
    their href's own query, so the request URL can carry a repeated ``q``;
    ``HX-Push-Url`` hands htmx the canonical address to push instead.
    """
    users, total, listing = await _load_listing(request, listing)
    headers = {}
    if request.headers.get("HX-Request") == "true":
        headers["HX-Push-Url"] = listing.page_url()
    return templates.TemplateResponse(
        request,
        "users.html",
        {**_listing_context(users, total, listing), "username": user.email},
        headers=headers,
    )


@router.get("/users", response_class=HTMLResponse)
async def users_table_fragment(
    request: Request,
    listing: UsersListing = Depends(_listing_params),
    user: UserTokenPayload = Depends(_require_admin),
):
    """Return the users panel: the filtered, sorted page plus its pager.

    The search ``<form>`` in ``users.html`` is the requesting element, so
    every trigger (typed search, filter-select change, Enter) serializes
    ``q`` and ``verified``, and its ``hx-include`` of the panel adds the
    hidden ``sort`` / ``dir`` inputs and the rows-per-page select. No
    hidden ``page`` exists, so a search always lands on page 1. The
    per-page select and the post-delete refresher hit this endpoint too;
    the sort headers and pager are boosted links onto the page route.

    The response carries ``HX-Replace-Url`` with the canonical page
    address so the location bar tracks the state without a history entry
    per keystroke.
    """
    del user  # Depends() enforces the role; the variable is otherwise unused.
    users, total, listing = await _load_listing(request, listing)
    return templates.TemplateResponse(
        request,
        "_users_table.html",
        _listing_context(users, total, listing),
        headers={"HX-Replace-Url": listing.page_url()},
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
    # OOB delete markers: the form's primary swap empties #modal-root
    # (closing the modal), and these OOB blocks remove the user row plus
    # any expanded detail row by id. HTMX matches OOB by id only, so
    # <div> works for a target rendered as <tr>; and a missing target
    # (collapsed row case) is a silent no-op. Using <div> also dodges
    # browser HTML parsing that strips <tr> outside a <table>.
    # The HX-Trigger header then makes the panel re-fetch its page, so the
    # "Showing X–Y of Z" line and the page boundaries stay right after the
    # row has already vanished.
    return HTMLResponse(
        f'<div id="user-row-{user_id}" hx-swap-oob="delete"></div>'
        f'<div id="user-detail-{user_id}" hx-swap-oob="delete"></div>',
        headers={"HX-Trigger": "users-changed"},
    )


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
    request: Request,
    listing: UsersListing = Depends(_listing_params),
    user: UserTokenPayload = Depends(_require_admin),
):
    """Return OOB ``<span hx-swap-oob="true">`` fragments for live status.

    Drives the 10 s status refresh from a single hidden poller inside the
    users panel without churning the rest of the DOM (so expanded detail
    rows are not collapsed). The poller's URL carries the panel's listing
    state, so only the users on the visible page are re-rendered; the
    cells update in place and the rows are never reordered, even under a
    last-activity sort — the order refreshes on the next interaction.
    Emits three classes of span by id:

    * ``agent-status-{agent_id}`` — the online/offline badge.
    * ``user-online-{user_id}`` — count of currently-online agents.
    * ``user-last-activity-{user_id}`` — max ``last_seen_at`` across
      the user's agents, or em-dash.

    ``<span>`` is chosen deliberately (not ``<tr>``/``<td>``) to sidestep
    the documented ``<tbody>`` OOB-swap quirk in htmx#1900.
    """
    del user
    users, _total, _listing = await _load_listing(request, listing)
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

    agent = await db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent["online"] = routing.is_agent_online(agent_id)

    owner_email = None
    if agent.get("owner_user_id"):
        owner = await db.get_user_by_id(agent["owner_user_id"])
        if owner:
            owner_email = owner["email"]

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
            "latest_agent_version": latest_agent_version,
            "username": user.email,
        },
    )

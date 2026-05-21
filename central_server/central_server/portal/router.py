"""End-user portal routes — Jinja2 + HTMX, parallel to the admin dashboard.

Every authenticated route filters by ``user.sub`` so a portal cookie never
exposes anything beyond the holder's own data. State-changing routes are
guarded by :func:`portal.csrf.require_csrf`.
"""
from __future__ import annotations

import html
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth.dependencies import get_portal_user
from ..auth.models import UserTokenPayload
from ..auth.service import (
    AccountDeletionAuthError,
    AccountDeletionForbiddenError,
    ExpiredResetCodeError,
    ExpiredVerificationCodeError,
    InvalidResetCodeError,
    InvalidVerificationCodeError,
    NewPasswordSameAsOldError,
    ResendRateLimitedError,
    ResetAttemptsExhaustedError,
    VerificationAttemptsExhaustedError,
    VerificationRequiredError,
)
from ..templates_dir import resolve_templates_dir
from .csrf import ensure_csrf_cookie, require_csrf

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(resolve_templates_dir()))

router = APIRouter(prefix="/portal", tags=["portal"])


def _set_portal_cookie(response, token: str, ttl_minutes: int) -> None:
    """Set the portal session cookie. ``ttl_minutes == 0`` → session cookie."""
    response.set_cookie(
        key="portal_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=ttl_minutes * 60 if ttl_minutes > 0 else None,
    )


def _portal_context(request: Request, **extra) -> dict:
    """Build a base Jinja context with CSRF + privacy-policy URL prefilled."""
    config = request.app.state.config
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "privacy_policy_url": config.privacy_policy_url,
        **extra,
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: str | None = None,
    reset: int = 0,
    deleted: int = 0,
):
    notice = None
    if reset:
        notice = "Password reset — sign in with your new password."
    elif deleted:
        notice = (
            "Your account is scheduled for deletion. Sign in again within the "
            "grace window to cancel."
        )
    return _render_with_csrf(
        request, "portal_login.html", error=error, notice=notice
    )


@router.post("/login")
async def login_submit(
    request: Request,
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    email = str(form.get("email", ""))
    password = str(form.get("password", ""))
    auth_service = request.app.state.auth_service
    try:
        _user_id, _role, token, _scheduled = await auth_service.login_user(
            email, password
        )
    except VerificationRequiredError:
        return _render_with_csrf(
            request,
            "portal_login.html",
            error="Please verify your email before signing in. Check your inbox.",
            notice=None,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    except ValueError:
        return _render_with_csrf(
            request,
            "portal_login.html",
            error="Invalid email or password.",
            notice=None,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    ttl = request.app.state.config.portal_token_expire_minutes
    response = RedirectResponse(
        url="/portal/account", status_code=status.HTTP_303_SEE_OTHER
    )
    _set_portal_cookie(response, token, ttl)
    return response


@router.post("/logout")
async def logout(_csrf: None = Depends(require_csrf)):
    response = RedirectResponse(
        url="/portal/login", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("portal_token")
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: str | None = None):
    return _render_with_csrf(
        request, "portal_signup.html", error=error
    )


@router.post("/signup")
async def signup_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    if not email or not password:
        return _render_with_csrf(
            request,
            "portal_signup.html",
            error="Email and password are required.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    auth_service = request.app.state.auth_service
    try:
        result = await auth_service.register_user(email, password, background_tasks)
    except ValueError as exc:
        return _render_with_csrf(
            request,
            "portal_signup.html",
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )

    if result.status == "verification_required":
        return RedirectResponse(
            url=f"/portal/verify-email?email={email}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    response = RedirectResponse(
        url="/portal/account", status_code=status.HTTP_303_SEE_OTHER
    )
    assert result.access_token is not None
    ttl = request.app.state.config.portal_token_expire_minutes
    _set_portal_cookie(response, result.access_token, ttl)
    return response


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request, email: str = "", error: str | None = None
):
    return _render_with_csrf(
        request, "portal_verify_email.html", email=email, error=error
    )


@router.post("/verify-email")
async def verify_email_submit(
    request: Request,
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    email = str(form.get("email", "")).strip()
    code = str(form.get("code", "")).strip()
    auth_service = request.app.state.auth_service
    try:
        _user_id, _role, token, _scheduled = await auth_service.verify_email(
            email, code
        )
    except (
        InvalidVerificationCodeError,
        ExpiredVerificationCodeError,
        VerificationAttemptsExhaustedError,
    ) as exc:
        return _render_with_csrf(
            request,
            "portal_verify_email.html",
            email=email,
            error=_friendly_verify_error(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse(
        url="/portal/account", status_code=status.HTTP_303_SEE_OTHER
    )
    ttl = request.app.state.config.portal_token_expire_minutes
    _set_portal_cookie(response, token, ttl)
    return response


def _friendly_verify_error(exc: Exception) -> str:
    if isinstance(exc, ExpiredVerificationCodeError):
        return "That code has expired. Request a new one and try again."
    if isinstance(exc, VerificationAttemptsExhaustedError):
        return "Too many attempts — request a new code."
    return "Code is invalid. Double-check the digits and try again."


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, sent: bool = False):
    return _render_with_csrf(
        request, "portal_forgot_password.html", sent=sent
    )


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    email = str(form.get("email", "")).strip()
    auth_service = request.app.state.auth_service
    client_ip = request.client.host if request.client else None
    await auth_service.request_password_reset(
        email, background_tasks=background_tasks, client_ip=client_ip
    )
    # Enumeration-resistant — always present "check your inbox" confirmation.
    return RedirectResponse(
        url="/portal/forgot-password?sent=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request, email: str = "", error: str | None = None
):
    return _render_with_csrf(
        request, "portal_reset_password.html", email=email, error=error
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    email = str(form.get("email", "")).strip()
    code = str(form.get("code", "")).strip()
    new_password = str(form.get("new_password", ""))
    auth_service = request.app.state.auth_service
    try:
        await auth_service.reset_password(email, code, new_password, background_tasks)
    except (
        InvalidResetCodeError,
        ExpiredResetCodeError,
        ResetAttemptsExhaustedError,
        NewPasswordSameAsOldError,
        ResendRateLimitedError,
    ) as exc:
        return _render_with_csrf(
            request,
            "portal_reset_password.html",
            email=email,
            error=_friendly_reset_error(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(
        url="/portal/login?reset=1", status_code=status.HTTP_303_SEE_OTHER
    )


def _friendly_reset_error(exc: Exception) -> str:
    if isinstance(exc, ExpiredResetCodeError):
        return "That code has expired. Request a new one and try again."
    if isinstance(exc, ResetAttemptsExhaustedError):
        return "Too many attempts — request a new code."
    if isinstance(exc, NewPasswordSameAsOldError):
        return "New password must differ from your current password."
    if isinstance(exc, ResendRateLimitedError):
        return "Too many requests. Please wait a few minutes and try again."
    return "Reset code is invalid. Double-check it and try again."


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: UserTokenPayload = Depends(get_portal_user),
):
    db = request.app.state.db
    user_row = await db.get_user_by_id(user.sub)
    if user_row is None:
        return RedirectResponse(
            url="/portal/login", status_code=status.HTTP_303_SEE_OTHER
        )
    agents = await db.list_agents_for_user(user.sub)
    return _render_with_csrf(
        request,
        "portal_account.html",
        user=user_row,
        agents=agents,
    )


@router.get("/account/delete-modal", response_class=HTMLResponse)
async def delete_modal(
    request: Request,
    user: UserTokenPayload = Depends(get_portal_user),
):
    db = request.app.state.db
    config = request.app.state.config
    user_row = await db.get_user_by_id(user.sub)
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")
    agents = await db.list_agents_for_user(user.sub)
    is_seed_admin = (
        config.admin_seed_email is not None
        and user_row["email"] == config.admin_seed_email
    )
    is_google = user_row.get("google_sub") is not None and user_row.get("password_hash") is None
    return _render_with_csrf(
        request,
        "_portal_delete_modal.html",
        user=user_row,
        agents=agents,
        is_seed_admin=is_seed_admin,
        is_google=is_google,
        grace_days=config.account_deletion_grace_days,
    )


@router.post("/account/delete", response_class=HTMLResponse)
async def delete_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    _csrf: None = Depends(require_csrf),
    user: UserTokenPayload = Depends(get_portal_user),
):
    from ..auth.models import DeleteAccountRequest

    form = await request.form()
    method = str(form.get("method", "password"))
    proof = DeleteAccountRequest(
        method=method,  # type: ignore[arg-type]
        password=str(form.get("password")) if form.get("password") else None,
        id_token=str(form.get("id_token")) if form.get("id_token") else None,
        nonce=str(form.get("nonce")) if form.get("nonce") else None,
    )
    auth_service = request.app.state.auth_service
    try:
        await auth_service.request_account_deletion(
            user,
            source="portal",
            proof=proof,
            background_tasks=background_tasks,
        )
    except AccountDeletionForbiddenError as exc:
        # html.escape — the exception message can include attacker-influenced
        # bytes (e.g., AccountDeletionAuthError wraps google-auth's
        # ``Invalid Google ID token: …``), and this HTMLResponse is swapped
        # straight into the DOM by HTMX with no Jinja autoescape between.
        return HTMLResponse(
            f'<div class="alert alert-error">{html.escape(str(exc))}</div>',
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except AccountDeletionAuthError as exc:
        return HTMLResponse(
            f'<div class="alert alert-error">Re-authentication failed: '
            f'{html.escape(str(exc))}</div>',
            status_code=status.HTTP_403_FORBIDDEN,
        )
    # HX-Redirect tells HTMX to do a full-page navigation rather than swap.
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = "/portal/login?deleted=1"
    response.delete_cookie("portal_token")
    return response


@router.post("/account/cancel-deletion", response_class=HTMLResponse)
async def cancel_self_deletion(
    request: Request,
    _csrf: None = Depends(require_csrf),
    user: UserTokenPayload = Depends(get_portal_user),
):
    auth_service = request.app.state.auth_service
    previous = await auth_service.cancel_account_deletion(user.sub)
    if previous is None:
        message = "No deletion was scheduled."
    else:
        message = "Scheduled deletion cancelled — your account is active again."
    # HX-Redirect refreshes /portal/account to clear the grace banner.
    response = HTMLResponse(f'<div class="alert alert-success">{message}</div>')
    response.headers["HX-Redirect"] = "/portal/account"
    return response


@router.get("/cancel-deletion", response_class=HTMLResponse)
async def cancel_deletion_via_email(
    request: Request, token: str = "", confirmed: bool = False
):
    """Public landing page for the cancel link emailed to the user.

    GET shows a "confirm cancel?" form (avoids token consumption on
    accidental hover-previews / link scanners). POST consumes the token.
    """
    if not token:
        return _render_with_csrf(
            request,
            "portal_cancel_deletion.html",
            outcome="missing_token",
        )
    return _render_with_csrf(
        request,
        "portal_cancel_deletion.html",
        outcome="confirm" if not confirmed else "confirmed",
        token=token,
    )


@router.post("/cancel-deletion", response_class=HTMLResponse)
async def cancel_deletion_consume(
    request: Request,
    _csrf: None = Depends(require_csrf),
):
    form = await request.form()
    token = str(form.get("token", ""))
    if not token:
        return _render_with_csrf(
            request, "portal_cancel_deletion.html", outcome="missing_token"
        )
    auth_service = request.app.state.auth_service
    user_id = await auth_service.cancel_account_deletion_by_token(token)
    outcome = "cancelled" if user_id else "invalid_token"
    return _render_with_csrf(
        request, "portal_cancel_deletion.html", outcome=outcome
    )


@router.get("/delete-account-info", response_class=HTMLResponse)
async def delete_account_info(request: Request):
    """Public information page about account deletion.

    This URL is the one submitted to Google Play Console's Data Safety form.
    Reachable without authentication; no PII is rendered.
    """
    config = request.app.state.config
    return _render_with_csrf(
        request,
        "portal_delete_info.html",
        grace_days=config.account_deletion_grace_days,
    )


def _render_with_csrf(
    request: Request,
    template_name: str,
    *,
    status_code: int = 200,
    **context,
) -> HTMLResponse:
    """Render a Jinja template with the CSRF cookie + token threaded in."""
    config = request.app.state.config
    # Issue (or reuse) the CSRF cookie via a throwaway response we then
    # adopt the headers from. We render the actual response with the same
    # token so cookie value === template value === HTMX header.
    probe = HTMLResponse("")
    token = ensure_csrf_cookie(request, probe, config)
    request.state.csrf_token = token
    rendered = templates.TemplateResponse(
        request,
        template_name,
        _portal_context(request, **context),
        status_code=status_code,
    )
    # Adopt the Set-Cookie from probe so the new cookie reaches the browser.
    for key, value in probe.headers.items():
        if key.lower() == "set-cookie":
            rendered.raw_headers.append((b"set-cookie", value.encode("latin-1")))
    return rendered

"""The two anonymous pages that outlived the end-user portal.

Sign-in, sign-up, verification, password reset and the account page moved to the
browser client at ``app_origin``. These two stayed because external systems name
them: the deletion email links ``/portal/cancel-deletion?token=…``, and
``/portal/delete-account-info`` is the URL registered in Google Play Console's
Data Safety form. Both are anonymous; the only state-changing route is guarded by
:func:`portal.csrf.require_csrf`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..templates_dir import resolve_templates_dir
from .csrf import ensure_csrf_cookie, require_csrf

templates = Jinja2Templates(directory=str(resolve_templates_dir()))

router = APIRouter(prefix="/portal", tags=["portal"])


def _portal_context(request: Request, **extra) -> dict:
    """Build a base Jinja context with CSRF, the browser-client origin and the
    shared asset version prefilled.

    ``asset_version`` is the ``?v=`` cache-busting query ``portal_base.html``
    stamps on both stylesheets and the wordmark. It must be the same value the
    landing page uses, which is why it is read from ``app.state`` rather than
    recomputed here. ``app_origin`` follows the same rule for the same reason:
    every surface this server renders must name one browser-client origin.
    """
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "asset_version": request.app.state.asset_version,
        "app_origin": request.app.state.web_app_origin,
        **extra,
    }


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
    **context,
) -> HTMLResponse:
    """Render a Jinja template with a CSRF token threaded in."""
    config = request.app.state.config
    # Issue (or reuse) the CSRF cookie via a throwaway response we then
    # adopt the headers from. We render the actual response with the same
    # token so cookie value === template value.
    probe = HTMLResponse("")
    token = ensure_csrf_cookie(request, probe, config)
    request.state.csrf_token = token
    rendered = templates.TemplateResponse(
        request,
        template_name,
        _portal_context(request, **context),
    )
    # Adopt the Set-Cookie from probe so the new cookie reaches the browser.
    for key, value in probe.headers.items():
        if key.lower() == "set-cookie":
            rendered.raw_headers.append((b"set-cookie", value.encode("latin-1")))
    return rendered

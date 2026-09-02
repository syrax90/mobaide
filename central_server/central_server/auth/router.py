"""REST endpoints for user authentication (register, login, Google/Apple OAuth)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status

from .dependencies import get_api_user
from .models import (
    AppleAuthRequest,
    AuthTokenResponse,
    CancelAccountDeletionResponse,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    GoogleAuthRequest,
    ResendVerificationRequest,
    ResendVerificationResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    UserLoginRequest,
    UserRefreshRequest,
    UserRegisterRequest,
    UserTokenPayload,
    VerifyEmailRequest,
)
from .session_cookie import (
    clear_session_cookies,
    read_refresh_cookie,
    require_origin_proof,
    set_session_cookies,
)
from .service import (
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _apply_refreshable_session(
    request: Request,
    payload: AuthTokenResponse,
    http_response: Response,
    *,
    refreshable: bool,
    transport: str | None,
) -> AuthTokenResponse:
    """Upgrade a freshly minted session to a refreshable one when asked.

    A no-op unless the client opted in, and also when the response carries
    no access token at all — the ``verification_required`` branch of
    ``/register`` has no session yet, so there is nothing to refresh and
    handing out a refresh token there would let an unverified account mint
    access tokens.

    Cookie mode has no such no-op. A request that mints nothing must still
    *clear* any cookie already in the jar: otherwise signing in as a second
    account over a live session leaves the first account's credential intact,
    and the next boot silently restores it. Mint or clear — never neither.

    Signing in over a live cookie also retires the token that cookie carried.
    The new one overwrites it in the jar either way, so the old row would
    otherwise stay valid and unreachable for its full 30 days — a credential
    nobody holds and nobody can revoke.

    ``http_response`` is FastAPI's injected response; its headers are merged
    into the real one only because these handlers return a model rather than a
    ``Response``. An endpoint that returns a ``Response`` — or raises — must
    set its cookies on that object instead.
    """
    config = request.app.state.config
    auth_service = request.app.state.auth_service
    cookie_mode = transport == "cookie"
    # Whatever this request does next, the cookie it arrived with is finished:
    # a mint overwrites it and a clear deletes it, so leaving the row live
    # would strand a 30-day credential nobody holds and nobody can revoke.
    superseded = read_refresh_cookie(request, config) if cookie_mode else None
    if not refreshable or payload.access_token is None:
        if cookie_mode:
            if superseded is not None:
                await auth_service.revoke_user_session(superseded)
            clear_session_cookies(http_response, config)
        return payload
    access, refresh, expires_in = await auth_service.issue_refreshable_session(
        payload.user_id, payload.email, payload.role
    )
    # Retire the old credential only once its replacement exists. Revoking
    # first and then failing to mint would leave the browser holding a cookie
    # whose row is already dead — neither minted nor cleared, which is the one
    # state this function is built to rule out.
    if superseded is not None:
        await auth_service.revoke_user_session(superseded)
    if not cookie_mode:
        return payload.model_copy(
            update={
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": expires_in,
            }
        )
    set_session_cookies(http_response, refresh, config)
    return payload.model_copy(
        update={
            "access_token": access,
            "refresh_token": None,
            "expires_in": expires_in,
            "session_transport": "cookie",
        }
    )


@router.post("/register", response_model=AuthTokenResponse)
async def register(
    request: Request,
    http_response: Response,
    body: UserRegisterRequest,
    background_tasks: BackgroundTasks,
):
    """Register a new user with email + password.

    When email verification is required, the response has
    ``status="verification_required"`` and ``access_token=None``; the client
    must call ``/api/auth/verify-email`` with the code from the OTP email.
    """
    auth_service = request.app.state.auth_service
    try:
        result = await auth_service.register_user(
            body.email,
            body.password,
            background_tasks,
            ad_attribution=body.ad_attribution,
            client_ip=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return await _apply_refreshable_session(
        request,
        AuthTokenResponse(
            access_token=result.access_token,
            user_id=result.user_id,
            email=result.email,
            role=result.role,
            status=result.status,
        ),
        http_response,
        refreshable=body.refreshable,
        transport=body.session_transport,
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    request: Request, http_response: Response, body: UserLoginRequest
):
    """Authenticate a user with email + password."""
    auth_service = request.app.state.auth_service
    try:
        user_id, role, token, scheduled_deletion_at = await auth_service.login_user(
            body.email, body.password
        )
    except VerificationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "verification_required", "email": exc.email},
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    return await _apply_refreshable_session(
        request,
        AuthTokenResponse(
            access_token=token,
            user_id=user_id,
            email=body.email,
            role=role,
            scheduled_deletion_at=scheduled_deletion_at,
        ),
        http_response,
        refreshable=body.refreshable,
        transport=body.session_transport,
    )


@router.post("/google", response_model=AuthTokenResponse)
async def google_auth(
    request: Request, http_response: Response, body: GoogleAuthRequest
):
    """Authenticate via Google ID token (from Android Credential Manager)."""
    auth_service = request.app.state.auth_service
    db = request.app.state.db
    try:
        user_id, role, token, _is_new = await auth_service.google_authenticate(
            body.id_token,
            ad_attribution=body.ad_attribution,
            client_ip=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    user = await db.get_user_by_id(user_id)
    return await _apply_refreshable_session(
        request,
        AuthTokenResponse(
            access_token=token,
            user_id=user_id,
            email=user["email"],
            role=role,
            scheduled_deletion_at=user.get("scheduled_deletion_at"),
        ),
        http_response,
        refreshable=body.refreshable,
        transport=body.session_transport,
    )


@router.post("/apple", response_model=AuthTokenResponse)
async def apple_auth(
    request: Request, http_response: Response, body: AppleAuthRequest
):
    """Authenticate via Apple identity token (Sign in with Apple on iOS)."""
    auth_service = request.app.state.auth_service
    db = request.app.state.db
    try:
        user_id, role, token, _is_new = await auth_service.apple_authenticate(
            body.id_token,
            nonce=body.nonce,
            ad_attribution=body.ad_attribution,
            client_ip=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    user = await db.get_user_by_id(user_id)
    return await _apply_refreshable_session(
        request,
        AuthTokenResponse(
            access_token=token,
            user_id=user_id,
            email=user["email"],
            role=role,
            scheduled_deletion_at=user.get("scheduled_deletion_at"),
        ),
        http_response,
        refreshable=body.refreshable,
        transport=body.session_transport,
    )


def _presented_refresh_token(
    request: Request, body: UserRefreshRequest | None
) -> tuple[str | None, bool]:
    """Resolve the refresh credential, returning ``(token, cookie_mode)``.

    A body token always wins, and takes the native-client path: it needs no
    origin proof, because a request that had to read the token out of storage
    to send it cannot have been forged by a third-party page. Falling back to
    the cookie means the caller is a browser, which can be.
    """
    body_token = body.refresh_token if body is not None else None
    if body_token is not None:
        return body_token, False
    require_origin_proof(request)
    return read_refresh_cookie(request, request.app.state.config), True


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_session(
    request: Request,
    http_response: Response,
    body: UserRefreshRequest | None = None,
):
    """Rotate a user-session refresh token into a fresh access token.

    Unauthenticated by design: the refresh token is the credential, and the
    access token this call replaces has usually already expired. The
    presented refresh token is consumed, so a replay returns 401 — clients
    must serialize concurrent refreshes rather than racing them.

    Every failure below leaves via ``raise``, which builds a fresh response and
    therefore drops anything set on ``http_response`` — exactly the required
    behaviour, since one browser holds a single shared cookie and a loser in a
    concurrent rotation must be incapable of overwriting the winner's jar.
    """
    token, cookie_mode = _presented_refresh_token(request, body)
    auth_service = request.app.state.auth_service
    result = (
        await auth_service.refresh_user_session(token)
        if token is not None
        else None
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user, access, refresh, expires_in = result
    if cookie_mode:
        set_session_cookies(http_response, refresh, request.app.state.config)
        return AuthTokenResponse(
            access_token=access,
            user_id=user["id"],
            email=user["email"],
            role=user["role"],
            scheduled_deletion_at=user.get("scheduled_deletion_at"),
            expires_in=expires_in,
            session_transport="cookie",
        )
    return AuthTokenResponse(
        access_token=access,
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        scheduled_deletion_at=user.get("scheduled_deletion_at"),
        refresh_token=refresh,
        expires_in=expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, body: UserRefreshRequest | None = None):
    """End one browser session: revoke its refresh token, clear its cookies.

    Unauthenticated for the same reason as ``/refresh`` — the access token is
    usually dead by the time a user signs out — and idempotent, so a caller
    with no credential left still gets a clean 204 and a cookie deletion.

    The cookies are set on the returned ``Response``, not on an injected one:
    FastAPI merges the injected response's headers only when the handler
    returns something that is *not* already a ``Response``.
    """
    token, _cookie_mode = _presented_refresh_token(request, body)
    if token is not None:
        await request.app.state.auth_service.revoke_user_session(token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response, request.app.state.config)
    return response


@router.post("/logout/all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    user: UserTokenPayload = Depends(get_api_user),
):
    """Revoke every browser session for the calling account, including this one.

    Browser-scoped, not account-wide: the mobile clients never opt into
    ``refreshable``, so their tokens live in a different table and survive.
    Already-issued access tokens also survive until they expire — there is no
    JWT denylist — so other browsers stop working within
    ``user_session_access_expire_minutes``, not instantly. The UI copy says
    exactly that.
    """
    await request.app.state.auth_service.revoke_all_user_sessions(user.sub)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response, request.app.state.config)
    return response


@router.post("/verify-email", response_model=AuthTokenResponse)
async def verify_email(
    request: Request, http_response: Response, body: VerifyEmailRequest
):
    """Consume a verification code and return the JWT."""
    auth_service = request.app.state.auth_service
    try:
        user_id, role, token, scheduled_deletion_at = await auth_service.verify_email(
            body.email, body.code
        )
    except ExpiredVerificationCodeError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "verification_code_expired"},
        )
    except VerificationAttemptsExhaustedError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "verification_attempts_exhausted"},
        )
    except InvalidVerificationCodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "verification_code_invalid"},
        )
    return await _apply_refreshable_session(
        request,
        AuthTokenResponse(
            access_token=token,
            user_id=user_id,
            email=body.email,
            role=role,
            scheduled_deletion_at=scheduled_deletion_at,
        ),
        http_response,
        refreshable=body.refreshable,
        transport=body.session_transport,
    )


@router.post("/resend-verification", response_model=ResendVerificationResponse)
async def resend_verification(
    request: Request,
    body: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
):
    """Issue a fresh verification code (rate-limited)."""
    auth_service = request.app.state.auth_service
    try:
        await auth_service.resend_verification(body.email, background_tasks)
    except ResendRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "resend_rate_limited",
                "cooldown_seconds_remaining": exc.cooldown_seconds_remaining,
            },
            headers={"Retry-After": str(exc.cooldown_seconds_remaining)},
        )
    return ResendVerificationResponse(status="sent", cooldown_seconds_remaining=0)


async def _require_reset_ip_quota(request: Request) -> None:
    """FastAPI dependency: enforce per-IP cap on the password-reset endpoints.

    Reads the shared ``reset_ip_limiter`` from app state. On exhaustion,
    raises HTTP 429 with a ``Retry-After`` header. Falls open if the limiter
    isn't wired (e.g. in unit tests that bypass lifespan).
    """
    limiter = getattr(request.app.state, "reset_ip_limiter", None)
    if limiter is None:
        return
    ip = request.client.host if request.client else "unknown"
    wait = await limiter.try_acquire(f"ip:{ip}")
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "reset_ip_rate_limited",
                "retry_after_seconds": wait,
            },
            headers={"Retry-After": str(wait)},
        )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    _ip: None = Depends(_require_reset_ip_quota),
):
    """Send a password-reset OTP by email. Always returns 200 to defeat enumeration."""
    auth_service = request.app.state.auth_service
    client_ip = request.client.host if request.client else None
    await auth_service.request_password_reset(
        body.email,
        background_tasks=background_tasks,
        client_ip=client_ip,
    )
    return ForgotPasswordResponse()


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    background_tasks: BackgroundTasks,
    _ip: None = Depends(_require_reset_ip_quota),
):
    """Consume a reset OTP and replace the user's password."""
    auth_service = request.app.state.auth_service
    try:
        await auth_service.reset_password(
            body.email, body.code, body.new_password, background_tasks,
        )
    except ExpiredResetCodeError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "reset_code_expired"},
        )
    except ResetAttemptsExhaustedError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "reset_attempts_exhausted"},
        )
    except InvalidResetCodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "reset_code_invalid"},
        )
    except NewPasswordSameAsOldError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "new_password_same_as_old"},
        )
    return ResetPasswordResponse()


@router.post(
    "/me/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def request_account_deletion(
    request: Request,
    body: DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    user: UserTokenPayload = Depends(get_api_user),
):
    """Schedule or immediately purge the calling user's account.

    ``body.mode`` chooses between ``'scheduled'`` (default — 14-day soft-delete
    with a cancel link) and ``'immediate'`` (irreversible cascade purge now).
    Older clients that omit ``mode`` get the legacy scheduled behavior.

    Requires a fresh credential proof (current password OR fresh Google ID
    token with matching nonce) — the session JWT alone is rejected. On
    success: writes the audit row, revokes refresh tokens, closes any live
    user/agent WebSockets with code 4010, and (scheduled mode only) emails a
    single-use cancel link before returning 204.

    Refuses to act on the seed-admin account (mirrors the admin-path guard).
    """
    auth_service = request.app.state.auth_service
    try:
        await auth_service.request_account_deletion(
            user,
            source="android",
            proof=body,
            background_tasks=background_tasks,
        )
    except AccountDeletionForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except AccountDeletionAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "reauth_required", "message": str(exc)},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/delete/cancel",
    response_model=CancelAccountDeletionResponse,
)
async def cancel_account_deletion(
    request: Request,
    user: UserTokenPayload = Depends(get_api_user),
):
    """Restore an account that is currently in the soft-delete grace window.

    Idempotent: succeeds even if no deletion was scheduled. The previous
    ``scheduled_deletion_at`` (or None) is echoed back so the client can
    confirm what was cancelled.
    """
    auth_service = request.app.state.auth_service
    previous = await auth_service.cancel_account_deletion(user.sub)
    if previous is None:
        return CancelAccountDeletionResponse(
            status="not_scheduled", scheduled_deletion_at=None
        )
    return CancelAccountDeletionResponse(
        status="cancelled", scheduled_deletion_at=previous
    )

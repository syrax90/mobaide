"""REST endpoints for user authentication (register, login, Google OAuth)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from .models import (
    AuthTokenResponse,
    GoogleAuthRequest,
    ResendVerificationRequest,
    ResendVerificationResponse,
    UserLoginRequest,
    UserRegisterRequest,
    VerifyEmailRequest,
)
from .service import (
    ExpiredVerificationCodeError,
    InvalidVerificationCodeError,
    ResendRateLimitedError,
    VerificationAttemptsExhaustedError,
    VerificationRequiredError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthTokenResponse)
async def register(
    request: Request,
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
            body.email, body.password, background_tasks
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return AuthTokenResponse(
        access_token=result.access_token,
        user_id=result.user_id,
        email=result.email,
        role=result.role,
        status=result.status,
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(request: Request, body: UserLoginRequest):
    """Authenticate a user with email + password."""
    auth_service = request.app.state.auth_service
    try:
        user_id, role, token = await auth_service.login_user(body.email, body.password)
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
    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=body.email,
        role=role,
    )


@router.post("/google", response_model=AuthTokenResponse)
async def google_auth(request: Request, body: GoogleAuthRequest):
    """Authenticate via Google ID token (from Android Credential Manager)."""
    auth_service = request.app.state.auth_service
    db = request.app.state.db
    try:
        user_id, role, token, _is_new = await auth_service.google_authenticate(
            body.id_token
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    user = await db.get_user_by_id(user_id)
    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=user["email"],
        role=role,
    )


@router.post("/verify-email", response_model=AuthTokenResponse)
async def verify_email(request: Request, body: VerifyEmailRequest):
    """Consume a verification code and return the JWT."""
    auth_service = request.app.state.auth_service
    try:
        user_id, role, token = await auth_service.verify_email(body.email, body.code)
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
    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=body.email,
        role=role,
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

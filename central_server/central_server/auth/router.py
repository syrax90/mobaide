"""REST endpoints for user authentication (register, login, Google OAuth)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from .models import AuthTokenResponse, GoogleAuthRequest, UserLoginRequest, UserRegisterRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthTokenResponse)
async def register(request: Request, body: UserRegisterRequest):
    """Register a new user with email + password."""
    auth_service = request.app.state.auth_service
    try:
        user_id, token = await auth_service.register_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=body.email,
        role="viewer",
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(request: Request, body: UserLoginRequest):
    """Authenticate a user with email + password."""
    auth_service = request.app.state.auth_service
    try:
        user_id, role, token = await auth_service.login_user(body.email, body.password)
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

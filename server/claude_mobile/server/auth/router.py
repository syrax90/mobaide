from __future__ import annotations

import logging

import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Request, status

from .models import RefreshRequest, TokenRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def login(body: TokenRequest, request: Request) -> TokenResponse:
    """Validate credentials and return JWT access + refresh tokens."""
    user_store = request.app.state.user_store
    jwt_service = request.app.state.jwt_service

    if not user_store.verify(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    return TokenResponse(
        access_token=jwt_service.create_access_token(body.username),
        refresh_token=jwt_service.create_refresh_token(body.username),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair (rotation)."""
    jwt_service = request.app.state.jwt_service

    try:
        payload = jwt_service.decode_token(body.refresh_token)
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {exc}",
        )

    if payload.token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected refresh token",
        )

    return TokenResponse(
        access_token=jwt_service.create_access_token(payload.sub),
        refresh_token=jwt_service.create_refresh_token(payload.sub),
    )

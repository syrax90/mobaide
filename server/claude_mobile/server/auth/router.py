from __future__ import annotations

import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Request, status

from ..logging import audit_event, get_logger
from .models import RefreshRequest, TokenRequest, TokenResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/token", response_model=TokenResponse)
async def login(body: TokenRequest, request: Request) -> TokenResponse:
    """Validate credentials and return JWT access + refresh tokens."""
    user_store = request.app.state.user_store
    jwt_service = request.app.state.jwt_service
    client_ip = _client_ip(request)

    if not user_store.verify(body.username, body.password):
        audit_event(
            "audit.auth.login_failure",
            actor=body.username,
            target="auth",
            outcome="failure",
            client_ip=client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    audit_event(
        "audit.auth.login_success",
        actor=body.username,
        target="auth",
        outcome="ok",
        client_ip=client_ip,
    )
    return TokenResponse(
        access_token=jwt_service.create_access_token(body.username),
        refresh_token=jwt_service.create_refresh_token(body.username),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    """Exchange a valid refresh token for a new token pair (rotation)."""
    jwt_service = request.app.state.jwt_service
    client_ip = _client_ip(request)

    try:
        payload = jwt_service.decode_token(body.refresh_token)
    except pyjwt.InvalidTokenError as exc:
        audit_event(
            "audit.auth.token_refresh",
            actor=None,
            target="auth",
            outcome="failure",
            client_ip=client_ip,
            reason="invalid_token",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {exc}",
        )

    if payload.token_type != "refresh":
        audit_event(
            "audit.auth.token_refresh",
            actor=payload.sub,
            target="auth",
            outcome="failure",
            client_ip=client_ip,
            reason="wrong_token_type",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected refresh token",
        )

    audit_event(
        "audit.auth.token_refresh",
        actor=payload.sub,
        target="auth",
        outcome="ok",
        client_ip=client_ip,
    )
    return TokenResponse(
        access_token=jwt_service.create_access_token(payload.sub),
        refresh_token=jwt_service.create_refresh_token(payload.sub),
    )

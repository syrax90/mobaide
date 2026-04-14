"""FastAPI dependencies for the three auth paths."""
from __future__ import annotations

import logging

import jwt
from fastapi import HTTPException, Request, WebSocket, status

from .models import MobileTokenPayload
from .service import AuthService

logger = logging.getLogger(__name__)


class WebSocketAuthError(Exception):
    """Raised when WebSocket authentication fails (connection already closed)."""


def _auth_failed(reason: str) -> WebSocketAuthError:
    logger.warning("WebSocket auth failed: %s", reason)
    return WebSocketAuthError(reason)


def _get_auth_service(request_or_ws: Request | WebSocket) -> AuthService:
    return request_or_ws.app.state.auth_service


# -- Mobile WebSocket auth (mirrors agent's ws_authenticate pattern) -----------


async def ws_authenticate_mobile(websocket: WebSocket) -> MobileTokenPayload:
    """Authenticate a mobile WebSocket via ``?token=`` query param.

    Calls ``websocket.accept()`` on success. Closes with code 4001 on failure.
    """
    auth_service = _get_auth_service(websocket)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token query param")
        raise _auth_failed("Missing token query param")

    try:
        payload = auth_service.decode_mobile_token(token)
    except jwt.InvalidTokenError as exc:
        await websocket.close(code=4001, reason=str(exc))
        raise _auth_failed(str(exc))

    if payload.token_type != "mobile_access":
        await websocket.close(code=4001, reason="Expected mobile_access token")
        raise _auth_failed("Expected mobile_access token")

    await websocket.accept()
    return payload


# -- Dashboard auth (cookie-based JWT) ----------------------------------------


async def get_dashboard_user(request: Request) -> str:
    """FastAPI dependency: validate dashboard JWT from cookie."""
    auth_service = _get_auth_service(request)
    token = request.cookies.get("dashboard_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dashboard/login"},
        )
    try:
        username = auth_service.decode_dashboard_token(token)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dashboard/login"},
        )
    return username


# -- Mobile REST auth (Bearer header) -----------------------------------------


async def get_mobile_user(request: Request) -> MobileTokenPayload:
    """FastAPI dependency: extract and validate mobile JWT from Authorization header."""
    auth_service = _get_auth_service(request)
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header.removeprefix("Bearer ")
    try:
        payload = auth_service.decode_mobile_token(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.token_type != "mobile_access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected mobile_access token",
        )
    return payload

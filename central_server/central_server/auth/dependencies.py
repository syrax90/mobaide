"""FastAPI dependencies for the auth paths."""
from __future__ import annotations

import logging
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request, WebSocket, status

from .models import MobileTokenPayload, UserTokenPayload
from .service import AuthService

logger = logging.getLogger(__name__)


class WebSocketAuthError(Exception):
    """Raised when WebSocket authentication fails (connection already closed)."""


def _auth_failed(reason: str) -> WebSocketAuthError:
    logger.warning("WebSocket auth failed: %s", reason)
    return WebSocketAuthError(reason)


def _get_auth_service(request_or_ws: Request | WebSocket) -> AuthService:
    return request_or_ws.app.state.auth_service


async def ws_authenticate_mobile(websocket: WebSocket) -> MobileTokenPayload:
    """Authenticate a mobile WebSocket via ``?token=`` query param.

    Calls ``websocket.accept()`` on success. Closes with code 4001 on failure.
    The close ``reason`` is a generic ``"Invalid token"`` — the specific
    PyJWT exception (``"Signature has expired"``, ``"Signature verification
    failed"``, etc.) is logged at WARN for operators but never surfaced to
    the client, which reacts only to the 4001 close code.
    """
    auth_service = _get_auth_service(websocket)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token query param")
        raise _auth_failed("Missing token query param")

    try:
        payload = auth_service.decode_mobile_token(token)
    except jwt.InvalidTokenError as exc:
        await websocket.close(code=4001, reason="Invalid token")
        raise _auth_failed(f"Invalid mobile token: {exc}")

    if payload.token_type != "mobile_access":
        await websocket.close(code=4001, reason="Invalid token")
        raise _auth_failed("Expected mobile_access token")

    await websocket.accept()
    return payload


async def ws_authenticate_user(websocket: WebSocket) -> UserTokenPayload:
    """Authenticate a user-scoped WebSocket via ``?token=`` query param.

    On success, calls ``websocket.accept()`` and returns the decoded payload.
    Closes with 4001 on failure. The close ``reason`` is a generic
    ``"Invalid token"`` — specific PyJWT exception text is logged at WARN for
    operators but not exposed to the client, which reacts only to the 4001
    close code.
    """
    auth_service = _get_auth_service(websocket)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token query param")
        raise _auth_failed("Missing token query param")

    try:
        payload = auth_service.decode_dashboard_token(token)
    except jwt.InvalidTokenError as exc:
        await websocket.close(code=4001, reason="Invalid token")
        raise _auth_failed(f"Invalid dashboard token: {exc}")

    await websocket.accept()
    return payload


async def get_dashboard_user(request: Request) -> UserTokenPayload:
    """Validate the dashboard JWT from the session cookie."""
    auth_service = _get_auth_service(request)
    token = request.cookies.get("dashboard_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dashboard/login"},
        )
    try:
        user_payload = auth_service.decode_dashboard_token(token)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dashboard/login"},
        )
    return user_payload


def require_role(*allowed_roles: str) -> Callable:
    """Dependency factory: require the dashboard user to have one of the given roles.

    Usage:
        @router.get("/admin-only", dependencies=[Depends(require_role("admin"))])
    """

    async def _check_role(
        user: UserTokenPayload = Depends(get_dashboard_user),
    ) -> UserTokenPayload:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(allowed_roles)}",
            )
        return user

    return _check_role


async def get_api_user(request: Request) -> UserTokenPayload:
    """Extract and validate the user JWT from the Authorization Bearer header."""
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
        user_payload = auth_service.decode_dashboard_token(token)
    except jwt.InvalidTokenError as exc:
        logger.warning("dashboard_token_rejected reason=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_payload


async def get_non_admin_api_user(
    user: UserTokenPayload = Depends(get_api_user),
) -> UserTokenPayload:
    """Variant of :func:`get_api_user` that rejects admin-role tokens.

    Used on agent-ownership endpoints: admins are platform operators and
    must not own or manage their own agents. They should use the web
    dashboard for platform-wide visibility instead.
    """
    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin accounts cannot own or manage agents. "
            "Use the web dashboard for platform operations.",
        )
    return user


async def get_mobile_user(request: Request) -> MobileTokenPayload:
    """Extract and validate the mobile JWT from the Authorization Bearer header."""
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
        logger.warning("mobile_token_rejected reason=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.token_type != "mobile_access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return payload

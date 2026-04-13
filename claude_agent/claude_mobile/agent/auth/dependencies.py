from __future__ import annotations

import logging

import jwt
from fastapi import HTTPException, Request, WebSocket, status

from .jwt_service import JwtService

logger = logging.getLogger(__name__)



def _get_jwt_service(request_or_ws: Request | WebSocket) -> JwtService:
    return request_or_ws.app.state.jwt_service


async def get_current_user(request: Request) -> str:
    """FastAPI dependency: extract and validate JWT from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header.removeprefix("Bearer ")
    jwt_service = _get_jwt_service(request)
    try:
        payload = jwt_service.decode_token(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.username = payload.sub
    try:
        from ..logging import bind_request_context
        bind_request_context(username=payload.sub)
    except Exception:
        pass
    return payload.sub


async def ws_authenticate(websocket: WebSocket) -> str:
    """Authenticate a WebSocket connection via the ``?token=`` query param.

    The previous first-message JSON auth path was removed when the wire
    protocol switched to binary frames (CLA-13) — there is no longer a JSON
    layer to carry an auth handshake. Closes the connection with code 4001
    on failure.
    """
    jwt_service = _get_jwt_service(websocket)

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token query param")
        raise _auth_failed("Missing token query param")

    try:
        payload = jwt_service.decode_token(token)
    except jwt.InvalidTokenError as exc:
        await websocket.close(code=4001, reason=str(exc))
        raise _auth_failed(str(exc))

    if payload.token_type != "access":
        await websocket.close(code=4001, reason="Expected access token")
        raise _auth_failed("Expected access token")

    await websocket.accept()
    try:
        from ..logging import bind_request_context
        bind_request_context(username=payload.sub)
    except Exception:
        pass
    return payload.sub


class WebSocketAuthError(Exception):
    """Raised when WebSocket authentication fails (connection already closed)."""


def _auth_failed(reason: str) -> WebSocketAuthError:
    logger.warning("WebSocket auth failed: %s", reason)
    return WebSocketAuthError(reason)

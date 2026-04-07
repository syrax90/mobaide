from __future__ import annotations

import asyncio
import logging

import jwt
from fastapi import HTTPException, Request, WebSocket, status

from .jwt_service import JwtService

logger = logging.getLogger(__name__)

WS_AUTH_TIMEOUT_SECONDS = 5


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
    """Authenticate a WebSocket connection.

    Tries query param ``?token=`` first, then waits for a first-message auth
    payload ``{"type": "auth", "token": "..."}`` with a 5-second timeout.
    Closes the connection with code 4001 on failure.
    """
    jwt_service = _get_jwt_service(websocket)

    # Path 1: query-param token
    token = websocket.query_params.get("token")
    if token:
        try:
            payload = jwt_service.decode_token(token)
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
        except jwt.InvalidTokenError as exc:
            await websocket.close(code=4001, reason=str(exc))
            raise _auth_failed(str(exc))

    # Path 2: first-message auth
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=WS_AUTH_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, Exception):
        await websocket.close(code=4001, reason="Auth timeout")
        raise _auth_failed("Auth message timeout")

    if raw.get("type") != "auth" or "token" not in raw:
        await websocket.close(code=4001, reason="Expected auth message")
        raise _auth_failed("Expected auth message")

    try:
        payload = jwt_service.decode_token(raw["token"])
        if payload.token_type != "access":
            await websocket.close(code=4001, reason="Expected access token")
            raise _auth_failed("Expected access token")
        try:
            from ..logging import bind_request_context
            bind_request_context(username=payload.sub)
        except Exception:
            pass
        return payload.sub
    except jwt.InvalidTokenError as exc:
        await websocket.close(code=4001, reason=str(exc))
        raise _auth_failed(str(exc))


class WebSocketAuthError(Exception):
    """Raised when WebSocket authentication fails (connection already closed)."""


def _auth_failed(reason: str) -> WebSocketAuthError:
    logger.warning("WebSocket auth failed: %s", reason)
    return WebSocketAuthError(reason)

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


def _get_db(request_or_ws: Request | WebSocket):
    return request_or_ws.app.state.db


async def _user_still_exists(request_or_ws: Request | WebSocket, user_id: str) -> bool:
    """Return ``True`` iff the user row is still present in the DB.

    Anchors the JWT to current account state: after an admin unregisters a
    user (``DELETE /dashboard/users/{user_id}``), the cryptographic signature
    of any already-issued dashboard JWT remains valid, so without this check
    the bearer would continue to authenticate against a vanished account
    until the token's natural expiry. Cheap point-lookup on a PK column.
    """
    db = _get_db(request_or_ws)
    return await db.get_user_by_id(user_id) is not None


async def _agent_still_exists(request_or_ws: Request | WebSocket, agent_id: str) -> bool:
    """Return ``True`` iff the agent row is still present in the DB.

    Equivalent of :func:`_user_still_exists` for mobile tokens. Cascade
    deletion guarantees that an unregistered user's agents are removed in
    the same transaction, so checking the agent row is enough to catch
    both "user deleted" and "agent deleted by owner" revocations.
    """
    db = _get_db(request_or_ws)
    return await db.get_agent(agent_id) is not None


async def ws_authenticate_mobile(websocket: WebSocket) -> MobileTokenPayload:
    """Authenticate a mobile WebSocket via ``?token=`` query param.

    Calls ``websocket.accept()`` on success. Closes with code 4001 on
    signature/type failure or with code 4010 (``admin_revoked``) when the
    referenced agent has been cascade-deleted. Splitting the two cases
    lets the Android client render a user-specific "account removed"
    message in the offline-reconnect path — the same message it would
    have seen had the device been online to receive the synchronous
    teardown that ``routing.disconnect_agent`` issues on revocation.
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

    if not await _agent_still_exists(websocket, payload.agent_id):
        await websocket.close(code=4010, reason="admin_revoked")
        raise _auth_failed(f"Mobile token references missing agent: {payload.agent_id}")

    await websocket.accept()
    return payload


async def ws_authenticate_user(websocket: WebSocket) -> UserTokenPayload:
    """Authenticate a user-scoped WebSocket via ``?token=`` query param.

    On success, calls ``websocket.accept()`` and returns the decoded payload.
    Closes with 4001 on signature/type failure or with 4010
    (``admin_revoked``) when the underlying user account has been
    cascade-deleted, matching the close code that
    ``routing.disconnect_user`` emits to live sockets — so the Android
    client's 4010 handler logs the user out with the "account removed"
    message regardless of whether the device was online at the moment
    of unregister.
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

    if not await _user_still_exists(websocket, payload.sub):
        await websocket.close(code=4010, reason="admin_revoked")
        raise _auth_failed(f"Dashboard token references missing user: {payload.sub}")

    await websocket.accept()
    return payload


async def get_dashboard_user(request: Request) -> UserTokenPayload:
    """Validate the dashboard JWT from the session cookie.

    Redirects to ``/dashboard/login`` on signature failure **or** when the
    user no longer exists (cascade-deleted by another admin) — the dashboard
    UI handles both by re-prompting for credentials.
    """
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
    if not await _user_still_exists(request, user_payload.sub):
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
    """Extract and validate the user JWT from the Authorization Bearer header.

    Rejects tokens whose ``sub`` user no longer exists with the same 401
    shape as a signature failure, so the Android ``CentralAuthInterceptor``
    invalidates the session on the next REST call after an offline-period
    admin unregister.
    """
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
    if not await _user_still_exists(request, user_payload.sub):
        logger.info(
            "dashboard_token_rejected reason=user_not_found sub=%s",
            user_payload.sub,
        )
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
    """Extract and validate the mobile JWT from the Authorization Bearer header.

    Rejects tokens whose referenced agent no longer exists (cascade-deleted
    when the owner is unregistered) with the same 401 shape as a signature
    failure.
    """
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
    if not await _agent_still_exists(request, payload.agent_id):
        logger.info(
            "mobile_token_rejected reason=agent_not_found agent_id=%s",
            payload.agent_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload

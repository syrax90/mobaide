"""Mobile REST proxy — forwards HTTP requests to agents via the relay tunnel.

Mobile clients authenticate with their JWT, and the proxy converts each
REST request into a ``proxy_request`` control message sent through the
agent's tunnel.  The agent executes the operation locally and sends back
a ``proxy_response`` control message, which this module routes to the
waiting HTTP handler.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ..auth.dependencies import get_mobile_user
from ..auth.models import MobileTokenPayload
from .envelope import CH_CONTROL, encode
from .routing import RoutingTable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy")


class MobileProxy:
    """Request-response correlation for proxy calls through the agent tunnel."""

    def __init__(self, routing: RoutingTable) -> None:
        self._routing = routing
        self._pending: dict[str, asyncio.Future[dict]] = {}

    async def forward(
        self,
        agent_id: str,
        method: str,
        path: str,
        query: dict | None = None,
        body: object | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """Send a proxy request to the agent and await the response.

        Returns the agent's response dict (contains ``status`` and ``body``).
        Raises HTTPException on timeout or agent offline.
        """
        agent_conn = self._routing.get_agent(agent_id)
        if agent_conn is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent is offline",
            )

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending[request_id] = future

        control_msg: dict[str, object] = {
            "type": "proxy_request",
            "request_id": request_id,
            "method": method,
            "path": path,
        }
        if query:
            control_msg["query"] = query
        if body is not None:
            control_msg["body"] = body

        envelope = encode(CH_CONTROL, "", json.dumps(control_msg).encode())
        try:
            agent_conn.send_queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self._pending.pop(request_id, None)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent queue full",
            )

        try:
            result = await asyncio.wait_for(future, timeout)
            return result
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Agent did not respond in time",
            )
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, response: dict) -> None:
        """Called by agent_ws when a proxy_response control message arrives."""
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(response)


# ---------------------------------------------------------------------------
# Generic catch-all proxy route
# ---------------------------------------------------------------------------


@router.api_route(
    "/{agent_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
)
async def proxy_to_agent(
    request: Request,
    agent_id: str,
    path: str,
    payload: MobileTokenPayload = Depends(get_mobile_user),
) -> JSONResponse:
    """Forward any REST request to the agent through the relay tunnel."""
    # Verify the JWT's agent_id matches the requested agent
    if payload.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token not valid for this agent",
        )

    # Verify user owns this agent
    db = request.app.state.db
    if not await db.is_agent_owner(agent_id, payload.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not own this agent",
        )

    # Read request body for non-GET methods
    body = None
    if request.method in ("POST", "PUT", "DELETE"):
        raw = await request.body()
        if raw:
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                body = None

    # Collect query params
    query = dict(request.query_params) if request.query_params else None

    # Forward through the proxy
    mobile_proxy: MobileProxy = request.app.state.mobile_proxy
    result = await mobile_proxy.forward(
        agent_id=agent_id,
        method=request.method,
        path=f"/{path}",
        query=query,
        body=body,
    )

    return JSONResponse(
        status_code=result.get("status", 200),
        content=result.get("body"),
    )

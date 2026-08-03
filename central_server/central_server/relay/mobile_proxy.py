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
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ..auth.dependencies import get_mobile_user
from ..auth.models import MobileTokenPayload
from ..monitoring import record_send_queue_drop
from .envelope import CH_CONTROL, encode
from .routing import RoutingTable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy")

_CUSTOM_KIND_RE = re.compile(r"^custom_[0-9a-f]{12}$")

# Agents below this handshake protocol version silently ignore the injected
# ``resolved_*`` fields and would open a bare shell instead of the stored
# command, so custom-kind creates are refused for them.
_MIN_AGENT_PROTO_FOR_CUSTOM_KINDS = 7


class _ProxyRejection(Exception):
    """Short-circuit for interception failures whose body shape is part of
    the relay wire contract. Raising ``HTTPException`` with a dict detail
    would nest the payload under FastAPI's ``{"detail": ...}`` wrapper;
    relay-path marker bodies (``agent_update_required``, mirroring the
    agent-authored ``<cli>_cli_missing`` / ``<cli>_auth_required`` shapes)
    must stay top-level."""

    def __init__(self, status_code: int, body: dict) -> None:
        super().__init__(body.get("detail"))
        self.status_code = status_code
        self.body = body


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
            record_send_queue_drop("proxy_request")
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


async def _resolve_custom_kind(
    request: Request,
    agent_id: str,
    payload: MobileTokenPayload,
    body: dict,
) -> dict:
    """Strip-then-inject the server-only spawn fields on session creates.

    ``resolved_command`` / ``resolved_name`` are dropped from every client
    body unconditionally — only the relay may author them (the agent trusts
    them precisely because mobile clients have no other path that reaches
    its control channel). For a ``cli`` matching ``custom_<12hex>`` the
    stored command/name are looked up in the caller's registry and injected
    into a fresh dict; the forwarded body is never the client dict mutated
    in place.

    A type deleted after this lookup but before the agent spawns still
    launches with the snapshot command — it was live when read, matching
    the ``kind_label`` snapshot semantics.
    """
    body = {
        k: v
        for k, v in body.items()
        if k not in ("resolved_command", "resolved_name")
    }
    cli = body.get("cli")
    if not isinstance(cli, str) or not cli.startswith("custom_"):
        return body
    if _CUSTOM_KIND_RE.fullmatch(cli) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid custom agent type id",
        )
    service = request.app.state.custom_agent_type_service
    row = await service.resolve_for_launch(
        cli.removeprefix("custom_"), payload.user_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom agent type not found",
        )
    # Gate on the LIVE tunnel's negotiated version (stamped synchronously at
    # registration), not the DB handshake row — overlapping tunnels can
    # leave the row describing a different connection than the one this
    # request will be forwarded to. An offline agent skips the gate here;
    # MobileProxy.forward answers 503 for it immediately after.
    agent_conn = request.app.state.routing.get_agent(agent_id)
    if agent_conn is not None:
        proto = agent_conn.protocol_version
        if proto is None or proto < _MIN_AGENT_PROTO_FOR_CUSTOM_KINDS:
            raise _ProxyRejection(
                status.HTTP_409_CONFLICT,
                {
                    "agent_update_required": True,
                    "detail": (
                        "Agent update required to launch custom agent types"
                    ),
                },
            )
    body["resolved_command"] = row["command"]
    body["resolved_name"] = row["name"]
    return body


@router.api_route(
    "/{agent_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_to_agent(
    request: Request,
    agent_id: str,
    path: str,
    payload: MobileTokenPayload = Depends(get_mobile_user),
) -> JSONResponse:
    """Forward any REST request to the agent through the relay tunnel."""
    if payload.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token not valid for this agent",
        )

    db = request.app.state.db
    if not await db.is_agent_owner(agent_id, payload.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not own this agent",
        )

    body = None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        raw = await request.body()
        if raw:
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                body = None

    if (
        request.method == "POST"
        and path == "api/sessions"
        and isinstance(body, dict)
    ):
        try:
            body = await _resolve_custom_kind(request, agent_id, payload, body)
        except _ProxyRejection as rejection:
            return JSONResponse(
                status_code=rejection.status_code,
                content=rejection.body,
            )

    query = dict(request.query_params) if request.query_params else None

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

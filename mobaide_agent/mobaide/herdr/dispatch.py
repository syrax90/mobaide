"""``/api/herdr`` for the relay transport.

The FastAPI router (:mod:`mobaide.herdr.router`) answers the same paths with
the same operations, models and error bodies. The operations live here so the
two transports cannot drift apart.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from . import errors
from .models import (
    TEXT_MAX_LINES,
    HerdrCloseRequest,
    HerdrControlRequest,
    HerdrCreateRequest,
    HerdrCreateResponse,
    HerdrOkResponse,
    HerdrOpenPaneResponse,
    HerdrPaneTarget,
    HerdrPaneTextResponse,
    HerdrRenameRequest,
    HerdrSnapshot,
    HerdrStartAgentRequest,
)
from .service import HerdrService

HERDR_PATH_PREFIX = "/api/herdr"
DEFAULT_TEXT_LINES = 300


def require_service(service: object) -> HerdrService:
    if not isinstance(service, HerdrService):
        raise errors.unavailable()
    return service


async def op_snapshot(service: HerdrService) -> HerdrSnapshot:
    return service.snapshot()


async def op_open_pane(service: HerdrService, body: HerdrPaneTarget) -> HerdrOpenPaneResponse:
    return HerdrOpenPaneResponse(
        session_id=await service.open_pane(body.server_id, body.pane_id)
    )


async def op_control(
    service: HerdrService, body: HerdrControlRequest, *, actor: str | None = None
) -> HerdrOkResponse:
    await service.set_control(
        body.server_id, body.pane_id, body.control, body.client_id, actor=actor
    )
    return HerdrOkResponse()


async def op_text(
    service: HerdrService, server_id: str, pane_id: str, lines: int
) -> HerdrPaneTextResponse:
    text, truncated = await service.read_text(server_id, pane_id, lines)
    return HerdrPaneTextResponse(text=text, truncated=truncated)


async def op_create(
    service: HerdrService, body: HerdrCreateRequest, *, actor: str | None = None
) -> HerdrCreateResponse:
    pane_id, agent_error = await service.create(body, actor=actor)
    return HerdrCreateResponse(pane_id=pane_id, agent_error=agent_error)


async def op_start_agent(
    service: HerdrService, body: HerdrStartAgentRequest, *, actor: str | None = None
) -> HerdrOkResponse:
    await service.start_agent(body.server_id, body.pane_id, body.agent, actor=actor)
    return HerdrOkResponse()


async def op_rename(
    service: HerdrService, body: HerdrRenameRequest, *, actor: str | None = None
) -> HerdrOkResponse:
    await service.rename(body, actor=actor)
    return HerdrOkResponse()


async def op_close(
    service: HerdrService, body: HerdrCloseRequest, *, actor: str | None = None
) -> HerdrOkResponse:
    await service.close(body, actor=actor)
    return HerdrOkResponse()


def parse_lines(raw: object) -> int:
    try:
        lines = int(raw) if raw is not None else DEFAULT_TEXT_LINES  # type: ignore[call-overload]
    except (TypeError, ValueError):
        lines = DEFAULT_TEXT_LINES
    return max(1, min(lines, TEXT_MAX_LINES))


_POST_ROUTES: dict[str, tuple[type[BaseModel], Any]] = {
    "/pane/open": (HerdrPaneTarget, op_open_pane),
    "/pane/control": (HerdrControlRequest, op_control),
    "/pane/agent": (HerdrStartAgentRequest, op_start_agent),
    "/terminals": (HerdrCreateRequest, op_create),
    "/rename": (HerdrRenameRequest, op_rename),
    "/close": (HerdrCloseRequest, op_close),
}


async def dispatch_herdr(
    service: object,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    query: dict[str, Any] | None,
) -> dict[str, Any]:
    """Answer one proxied ``/api/herdr`` request as ``{"status", "body"}``."""
    route = path[len(HERDR_PATH_PREFIX):] if path.startswith(HERDR_PATH_PREFIX) else None
    try:
        herdr = require_service(service)
        result: BaseModel
        if method == "GET" and route == "":
            result = await op_snapshot(herdr)
        elif method == "GET" and route == "/pane/text":
            target = HerdrPaneTarget.model_validate(
                {
                    "server_id": (query or {}).get("server_id"),
                    "pane_id": (query or {}).get("pane_id"),
                }
            )
            result = await op_text(
                herdr, target.server_id, target.pane_id, parse_lines((query or {}).get("lines"))
            )
        elif method == "POST" and route in _POST_ROUTES:
            model, operation = _POST_ROUTES[route]
            result = await operation(herdr, model.model_validate(body or {}))
        else:
            return {
                "status": 404,
                "body": {"detail": f"Unknown proxy route: {method} {path}"},
            }
    except ValidationError as exc:
        return {
            "status": 422,
            "body": {
                "detail": [
                    {"loc": list(error.get("loc", ())), "msg": error.get("msg", "")}
                    for error in exc.errors(include_input=False, include_url=False)
                ]
            },
        }
    except errors.HerdrError as exc:
        return {"status": exc.status, "body": {"detail": exc.detail()}}
    return {"status": 200, "body": result.model_dump(mode="json")}

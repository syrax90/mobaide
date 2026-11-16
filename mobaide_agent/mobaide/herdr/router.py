"""``/api/herdr`` routes (direct transport).

The relay dispatcher (:func:`mobaide.herdr.dispatch.dispatch_herdr`) answers
the same paths with the same operations, models and error bodies.
"""
from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from mobaide.agent.auth.dependencies import get_current_user

from . import dispatch, errors
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

router = APIRouter(
    prefix=dispatch.HERDR_PATH_PREFIX,
    tags=["herdr"],
    dependencies=[Depends(get_current_user)],
)

T = TypeVar("T")


def _service(request: Request) -> HerdrService:
    try:
        return dispatch.require_service(getattr(request.app.state, "herdr", None))
    except errors.HerdrError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc


def _actor(request: Request) -> str | None:
    """The user of a direct request, for the audit record of an action."""
    return getattr(request.state, "username", None)


async def _run(operation: Awaitable[T]) -> T:
    try:
        return await operation
    except errors.HerdrError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc


@router.get("", response_model=HerdrSnapshot)
async def herdr_snapshot(request: Request) -> HerdrSnapshot:
    return await _run(dispatch.op_snapshot(_service(request)))


@router.get("/pane/text", response_model=HerdrPaneTextResponse)
async def herdr_pane_text(
    request: Request,
    server_id: str = Query(min_length=1, max_length=200),
    pane_id: str = Query(min_length=1, max_length=200),
    lines: int = Query(default=dispatch.DEFAULT_TEXT_LINES, ge=1, le=TEXT_MAX_LINES),
) -> HerdrPaneTextResponse:
    return await _run(dispatch.op_text(_service(request), server_id, pane_id, lines))


@router.post("/pane/open", response_model=HerdrOpenPaneResponse)
async def herdr_open_pane(request: Request, body: HerdrPaneTarget) -> HerdrOpenPaneResponse:
    return await _run(dispatch.op_open_pane(_service(request), body))


@router.post("/pane/control", response_model=HerdrOkResponse)
async def herdr_pane_control(request: Request, body: HerdrControlRequest) -> HerdrOkResponse:
    return await _run(dispatch.op_control(_service(request), body, actor=_actor(request)))


@router.post("/pane/agent", response_model=HerdrOkResponse)
async def herdr_start_agent(request: Request, body: HerdrStartAgentRequest) -> HerdrOkResponse:
    return await _run(dispatch.op_start_agent(_service(request), body, actor=_actor(request)))


@router.post("/terminals", response_model=HerdrCreateResponse)
async def herdr_create(request: Request, body: HerdrCreateRequest) -> HerdrCreateResponse:
    return await _run(dispatch.op_create(_service(request), body, actor=_actor(request)))


@router.post("/rename", response_model=HerdrOkResponse)
async def herdr_rename(request: Request, body: HerdrRenameRequest) -> HerdrOkResponse:
    return await _run(dispatch.op_rename(_service(request), body, actor=_actor(request)))


@router.post("/close", response_model=HerdrOkResponse)
async def herdr_close(request: Request, body: HerdrCloseRequest) -> HerdrOkResponse:
    return await _run(dispatch.op_close(_service(request), body, actor=_actor(request)))

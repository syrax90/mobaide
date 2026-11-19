from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from mobaide.core import MaxSessionsError
from mobaide.linear.scanner import (
    AutoScanAlreadyRunning,
    AutoScanCapacityReached,
    AutoScanSessionManager,
)
from mobaide.linear.sources import AutoScanSourceError

from ..auth.dependencies import WebSocketAuthError, get_current_user, ws_authenticate
from ..logging import get_logger
from ..ws.logging import (
    log_ws_authenticated,
    log_ws_connect,
    log_ws_disconnect,
    log_ws_error,
)
from ..sessions.models import SessionResponse
from .models import (
    AutoScanInstanceStatusResponse,
    AutoScanListResponse,
    AutoScanTemplatesResponse,
    GitHubLabelsResponse,
    GitHubSetupRequest,
    GitHubStatusResponse,
    RunNowRequest,
    RunNowResponse,
    SaveTemplateRequest,
    StartScanRequest,
    StopScanRequest,
)
from .service import (
    delete_template,
    github_labels_for,
    github_status_for,
    list_templates,
    save_template,
    start_github_setup,
    template_not_found,
)
from .templates import TemplateLimitError
from .stream import run_autoscan_stream

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/autoscan",
    tags=["autoscan"],
    dependencies=[Depends(get_current_user)],
)


def _get_manager(request: Request) -> AutoScanSessionManager:
    mgr = getattr(request.app.state, "autoscan_manager", None)
    if mgr is None:
        # Manager is always wired in at startup; absence means a misconfigured app.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auto scan manager is not initialised",
        )
    return mgr


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_scan(body: StartScanRequest, request: Request) -> dict:
    """Start an auto scan for a workspace. 409 if already running, 429 if at
    cap, 400 when a choice is missing or the source or agent is not usable."""
    mgr = _get_manager(request)
    claude_options = (
        body.claude_options.to_options()
        if body.claude_options is not None
        else None
    )
    try:
        instance = await mgr.start(
            body.project_name, client_options=claude_options, request=body.to_start(),
        )
    except AutoScanSourceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except AutoScanAlreadyRunning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except AutoScanCapacityReached as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": f"Auto scan started for workspace '{instance.project_name}'"}


@router.post("/stop", status_code=status.HTTP_200_OK)
async def stop_scan(body: StopScanRequest, request: Request) -> dict:
    mgr = _get_manager(request)
    try:
        await mgr.stop(body.project_name)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": f"Auto scan stopped for workspace '{body.project_name}'"}


@router.post("/run-now", response_model=RunNowResponse, status_code=status.HTTP_200_OK)
async def run_now(body: RunNowRequest, request: Request) -> RunNowResponse:
    """Trigger an immediate scan cycle for a running auto scan.

    Returns the freshly anchored ``next_scan_at`` so the caller can
    rebind its countdown synchronously, without waiting for the bus event.
    """
    mgr = _get_manager(request)
    instance = mgr.get(body.project_name)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active auto scan for workspace '{body.project_name}'",
        )
    instance.request_immediate_scan()
    next_at = instance.to_dto().get("next_scan_at")
    return RunNowResponse(
        ok=True,
        project_name=body.project_name,
        next_scan_at=next_at,
    )


@router.get("/list", response_model=AutoScanListResponse)
async def list_scans(request: Request) -> AutoScanListResponse:
    mgr = _get_manager(request)
    return AutoScanListResponse(**mgr.list_response())


@router.get("/status", response_model=AutoScanInstanceStatusResponse)
async def scan_status(
    request: Request, workspace_name: str = Query(..., alias="project_name")
) -> AutoScanInstanceStatusResponse:
    mgr = _get_manager(request)
    instance = mgr.get(workspace_name)
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active auto scan for workspace '{workspace_name}'",
        )
    return AutoScanInstanceStatusResponse(**instance.get_status())


@router.get("/github/status", response_model=GitHubStatusResponse)
async def github_status(
    request: Request, workspace_name: str = Query(..., alias="project_name"),
) -> GitHubStatusResponse:
    """What a GitHub scan of the workspace still needs (``gh``, sign-in, repo)."""
    try:
        return await github_status_for(request.app.state, workspace_name)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'"))


@router.get("/github/labels", response_model=GitHubLabelsResponse)
async def github_labels(
    request: Request, workspace_name: str = Query(..., alias="project_name"),
) -> GitHubLabelsResponse:
    """Labels and issue types of the workspace's GitHub repository."""
    try:
        return await github_labels_for(request.app.state, workspace_name)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'"))
    except AutoScanSourceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/templates", response_model=AutoScanTemplatesResponse)
async def get_templates(request: Request) -> AutoScanTemplatesResponse:
    """The Auto Scan templates saved on this PC, sorted by name."""
    return list_templates(request.app.state)


@router.post("/templates", response_model=AutoScanTemplatesResponse)
async def post_template(body: SaveTemplateRequest, request: Request) -> AutoScanTemplatesResponse:
    """Save a template; one with the same name (ignoring case) is replaced.
    409 when a new name would pass the cap. Answers every template."""
    try:
        return save_template(request.app.state, body)
    except TemplateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/templates/{template_id}", response_model=AutoScanTemplatesResponse)
async def remove_template(template_id: str, request: Request) -> AutoScanTemplatesResponse:
    """Delete a template; answers every template left."""
    try:
        return delete_template(request.app.state, template_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=template_not_found(template_id),
        )


@router.post(
    "/github/setup", response_model=SessionResponse, status_code=status.HTTP_201_CREATED,
)
async def github_setup(body: GitHubSetupRequest, request: Request) -> SessionResponse:
    """Open a terminal session that installs (``install``) or signs in
    (``sign_in``) the GitHub CLI; the response is the new session."""
    try:
        info = await start_github_setup(
            request.app.state, body.project_name, body.action, body.rows, body.cols,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'"))
    except AutoScanSourceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except MaxSessionsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except OSError as exc:
        # A missing workspace folder: the same 400 the relay answers.
        detail = exc.strerror or "cannot start the setup session"
        if exc.filename:
            detail = f"{detail}: {exc.filename}"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    return SessionResponse.from_info(info)


ws_router = APIRouter(tags=["autoscan-ws"])


@ws_router.websocket("/ws/autoscan")
async def ws_autoscan(websocket: WebSocket) -> None:
    """Stream auto-scan events over a direct mobile-to-agent WebSocket.

    Authenticates, resolves the ``AutoScanSessionManager``, then delegates
    the wire protocol to :func:`run_autoscan_stream` so the relay-mode
    bridge can share the same code path.
    """
    channel = "autoscan"
    log_ws_connect(channel, websocket)

    try:
        username = await ws_authenticate(websocket)
    except WebSocketAuthError as exc:
        log_ws_error(channel, exc, stage="auth")
        return
    log_ws_authenticated(channel, username)

    mgr: AutoScanSessionManager | None = getattr(
        websocket.app.state, "autoscan_manager", None
    )
    if mgr is None:
        log_ws_disconnect(channel, reason="not_configured", username=username)
        await websocket.close(code=4002, reason="Auto scan not configured")
        return

    msg_count = 0
    try:
        msg_count = await run_autoscan_stream(
            mgr,
            websocket.send_text,
            websocket.receive_text,
            grace_timeout_s=0.2,
        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_ws_error(channel, exc, stage="stream")
    finally:
        log_ws_disconnect(channel, username=username, msg_count=msg_count)

"""HTTP route for the user-triggered "install now" auto-update path.

The phone-side ``AgentOutdatedBanner`` exposes an ``Install now`` CTA.
Tapping it calls this endpoint via the central_server proxy hop, which
invokes ``StagedBinaryUpdater.force_update_now`` — the same code path as
``mobaide-agent update``, but skipping the activity-awareness deferral so
the user can pre-empt the wait.

The ``staged`` outcome path defers ``request_restart`` into a FastAPI
``BackgroundTask`` so the response is flushed before the agent stops —
without it the phone sees a connection error even on a successful stage.
A bare ``sys.exit`` there would not stop the agent: the task runs in a
worker thread and uvicorn swallows the ``SystemExit``.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from .auth.dependencies import get_current_user
from .logging import get_logger
from .restart import request_restart

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/agent/update",
    tags=["update"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/force_now")
async def force_update_now(
    request: Request, background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Trigger the staged-binary updater immediately, bypassing idle deferral.

    Returns the updater outcome string verbatim (``"staged"``,
    ``"signature_failed"``, ``"no_change"``, ``"deferred"``) so the client
    can surface a meaningful toast. On ``"staged"`` the supervisor restart
    is scheduled as a FastAPI background task that runs *after* the HTTP
    response is flushed, so the client receives the 200 reliably before
    the agent exits with code ``EXIT_RESTART_REQUESTED``.
    """
    updater = getattr(request.app.state, "updater", None)
    relay_client = getattr(request.app.state, "relay_client", None)
    if updater is None or relay_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auto-updater is not configured on this agent.",
        )
    manifest = relay_client.latest_manifest
    if not manifest or not manifest.get("manifest_body") or not manifest.get("version"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No manifest pointer available — agent has not received a rollout yet.",
        )

    outcome = await updater.force_update_now(
        manifest["manifest_body"],
        manifest.get("manifest_signature", ""),
        manifest["version"],
        exit_after_stage=False,
    )
    logger.info("updater.force_update_now outcome=%s", outcome)
    if outcome == "staged":
        background_tasks.add_task(request_restart)
    return {"outcome": outcome}

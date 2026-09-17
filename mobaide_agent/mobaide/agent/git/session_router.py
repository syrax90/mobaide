"""Session-scoped git endpoints backing the in-session Changes viewer:
``GET /api/sessions/{session_id}/git/status`` and ``.../git/diff`` read,
``POST .../git/stage|unstage|discard|commit`` plus ``GET .../git/commit/context``
mutate the staged set, ``POST .../git/fetch|pull|push`` start background
jobs that ``GET .../git/jobs/{job_id}`` and ``POST .../git/jobs/{job_id}/cancel``
follow, and ``POST .../git/rebase`` plus ``.../rebase/continue|skip|abort``
drive a rebase.

The directory is the session's own spawn cwd (project root or linked
worktree) — clients never send a path to run in. For the reads, repo
*conditions* are 200 answers with ``unavailable_reason``; a mutation on such
a repo is a ``409`` refusal instead, because there is no partial answer to
give. Session-not-found, bad scope/path, missing git and timeouts are HTTP
errors on every route.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..auth.dependencies import get_current_user
from .changes_jobs import job_registry
from .changes_models import (
    GitCommitContextResponse,
    GitCommitRequest,
    GitCommitResponse,
    GitDiffResponse,
    GitDiscardRequest,
    GitJobResponse,
    GitMutationResponse,
    GitPathsRequest,
    GitPullRequest,
    GitPushRequest,
    GitRebaseRequest,
    GitRebaseResponse,
    GitStatusResponse,
)
from .changes_mutations import (
    commit_session,
    discard_session_paths,
    get_session_commit_context,
    stage_session_paths,
    unstage_session_paths,
)
from .changes_rebase import (
    abort_session_rebase,
    continue_session_rebase,
    skip_session_rebase,
    start_session_rebase,
)
from .changes_service import (
    get_session_git_diff,
    get_session_git_status,
    http_error_for,
)

router = APIRouter(
    prefix="/api/sessions/{session_id}/git",
    tags=["git"],
    dependencies=[Depends(get_current_user)],
)


async def _session_target(
    request: Request, session_id: str
) -> tuple[str | None, str | None]:
    """``(cwd, project_name)`` of the session, 404 when it does not exist."""
    manager = getattr(request.app.state, "session_manager", None)
    session = await manager.get_session(session_id) if manager else None
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )
    info = session.to_info()
    return info.cwd, getattr(info, "project_name", None)


async def _session_cwd(request: Request, session_id: str) -> str | None:
    cwd, _ = await _session_target(request, session_id)
    return cwd


def _event_bus(request: Request):
    return getattr(request.app.state, "event_bus", None)


def _raise_mapped(exc: Exception) -> None:
    mapped = http_error_for(exc)
    if mapped is None:
        raise exc
    raise HTTPException(status_code=mapped[0], detail=mapped[1]["detail"])


@router.get("/status", response_model=GitStatusResponse)
async def session_git_status(
    session_id: str,
    request: Request,
    etag: str | None = None,
) -> GitStatusResponse:
    cwd = await _session_cwd(request, session_id)
    try:
        return await get_session_git_status(
            session_id=session_id, cwd=cwd, etag=etag
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.get("/diff", response_model=GitDiffResponse)
async def session_git_diff(
    session_id: str,
    request: Request,
    path: str | None = None,
    scope: str | None = None,
    context: str = "3",
    orig_path: str | None = None,
) -> GitDiffResponse:
    cwd = await _session_cwd(request, session_id)
    try:
        return await get_session_git_diff(
            cwd=cwd,
            path=path,
            scope=scope,
            context=context,
            orig_path=orig_path,
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/stage", response_model=GitMutationResponse)
async def session_git_stage(
    session_id: str, request: Request, body: GitPathsRequest
) -> GitMutationResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await stage_session_paths(
            cwd=cwd,
            paths=body.paths,
            project_name=project_name,
            event_bus=_event_bus(request),
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/unstage", response_model=GitMutationResponse)
async def session_git_unstage(
    session_id: str, request: Request, body: GitPathsRequest
) -> GitMutationResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await unstage_session_paths(
            cwd=cwd,
            paths=body.paths,
            project_name=project_name,
            event_bus=_event_bus(request),
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/discard", response_model=GitMutationResponse)
async def session_git_discard(
    session_id: str, request: Request, body: GitDiscardRequest
) -> GitMutationResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await discard_session_paths(
            cwd=cwd,
            paths=body.paths,
            scope=body.scope,
            project_name=project_name,
            event_bus=_event_bus(request),
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.get("/commit/context", response_model=GitCommitContextResponse)
async def session_git_commit_context(
    session_id: str, request: Request
) -> GitCommitContextResponse:
    cwd = await _session_cwd(request, session_id)
    try:
        return await get_session_commit_context(cwd=cwd)
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/commit", response_model=GitCommitResponse)
async def session_git_commit(
    session_id: str, request: Request, body: GitCommitRequest
) -> GitCommitResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await commit_session(
            cwd=cwd,
            request=body,
            project_name=project_name,
            event_bus=_event_bus(request),
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/fetch", response_model=GitJobResponse)
async def session_git_fetch(session_id: str, request: Request) -> GitJobResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await job_registry.start(
            "fetch",
            cwd=cwd,
            session_id=session_id,
            project_name=project_name,
            event_bus=_event_bus(request),
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/pull", response_model=GitJobResponse)
async def session_git_pull(
    session_id: str, request: Request, body: GitPullRequest | None = None
) -> GitJobResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await job_registry.start(
            "pull",
            cwd=cwd,
            session_id=session_id,
            project_name=project_name,
            event_bus=_event_bus(request),
            pull_mode=(body or GitPullRequest()).mode,
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/push", response_model=GitJobResponse)
async def session_git_push(
    session_id: str, request: Request, body: GitPushRequest | None = None
) -> GitJobResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await job_registry.start(
            "push",
            cwd=cwd,
            session_id=session_id,
            project_name=project_name,
            event_bus=_event_bus(request),
            force=(body or GitPushRequest()).force,
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/rebase", response_model=GitRebaseResponse)
async def session_git_rebase(
    session_id: str, request: Request, body: GitRebaseRequest
) -> GitRebaseResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await start_session_rebase(
            cwd=cwd,
            onto=body.onto,
            project_name=project_name,
            event_bus=_event_bus(request),
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/rebase/continue", response_model=GitRebaseResponse)
async def session_git_rebase_continue(
    session_id: str, request: Request
) -> GitRebaseResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await continue_session_rebase(
            cwd=cwd, project_name=project_name, event_bus=_event_bus(request)
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/rebase/skip", response_model=GitRebaseResponse)
async def session_git_rebase_skip(
    session_id: str, request: Request
) -> GitRebaseResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await skip_session_rebase(
            cwd=cwd, project_name=project_name, event_bus=_event_bus(request)
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/rebase/abort", response_model=GitRebaseResponse)
async def session_git_rebase_abort(
    session_id: str, request: Request
) -> GitRebaseResponse:
    cwd, project_name = await _session_target(request, session_id)
    try:
        return await abort_session_rebase(
            cwd=cwd, project_name=project_name, event_bus=_event_bus(request)
        )
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.get("/jobs/{job_id}", response_model=GitJobResponse)
async def session_git_job(session_id: str, job_id: str, request: Request) -> GitJobResponse:
    await _session_target(request, session_id)
    try:
        return job_registry.get(job_id)
    except Exception as exc:
        _raise_mapped(exc)
        raise


@router.post("/jobs/{job_id}/cancel", response_model=GitJobResponse)
async def session_git_job_cancel(
    session_id: str, job_id: str, request: Request
) -> GitJobResponse:
    await _session_target(request, session_id)
    try:
        return await job_registry.cancel(job_id)
    except Exception as exc:
        _raise_mapped(exc)
        raise

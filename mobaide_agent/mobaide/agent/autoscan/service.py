"""Auto Scan helpers shared by the REST router and the relay proxy.

Each function raises typed errors that both transports map to the same
status codes: ``KeyError`` → 404 (unknown workspace or template),
``AutoScanSourceError`` → 400 (the message says what to fix),
``MaxSessionsError`` and ``TemplateLimitError`` → 409.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from mobaide.core import SessionConfig, SessionKind
from mobaide.core.models import SessionInfo
from mobaide.github.gh import GhBinaryNotFoundError, GhCli, GhError
from mobaide.github.setup import (
    github_host,
    github_status,
    install_command,
    remote_hosts,
    sign_in_command,
    sign_in_env,
)
from mobaide.linear.sources import AutoScanSourceError

from ..logging import get_logger
from .models import (
    AutoScanTemplatesResponse,
    GitHubIssueTypeModel,
    GitHubLabelModel,
    GitHubLabelsResponse,
    GitHubStatusResponse,
    SaveTemplateRequest,
)
from .templates import AutoScanTemplatesStore

logger = get_logger(__name__)


def _workspace_path(app_state: Any, project_name: str) -> str:
    store = getattr(app_state, "workspaces_store", None)
    workspace = store.get(project_name) if store is not None else None
    if workspace is None:
        raise KeyError(f"Workspace '{project_name}' not found")
    return workspace.path


def _gh(app_state: Any) -> GhCli:
    mgr = getattr(app_state, "autoscan_manager", None)
    gh = getattr(mgr, "gh", None)
    return gh if isinstance(gh, GhCli) else GhCli()


async def github_status_for(app_state: Any, project_name: str) -> GitHubStatusResponse:
    path = _workspace_path(app_state, project_name)
    return GitHubStatusResponse(**await github_status(_gh(app_state), path))


async def _issue_types(gh: GhCli, repo) -> list[dict[str, str]]:
    # Optional: a host without issue types (an older GitHub Enterprise
    # Server) must not cost the wizard its labels.
    try:
        return await gh.list_issue_types(repo)
    except GhError as exc:
        logger.info("autoscan.github.issue_types_unavailable repo=%s: %s", repo.name_with_owner, exc)
        return []


async def github_labels_for(app_state: Any, project_name: str) -> GitHubLabelsResponse:
    """Labels and issue types of the workspace's GitHub repository, for the
    wizard's pickers."""
    path = _workspace_path(app_state, project_name)
    gh = _gh(app_state)
    try:
        repo = await gh.repo_of(path)
        labels, issue_types = await asyncio.gather(gh.list_labels(repo), _issue_types(gh, repo))
    except GhBinaryNotFoundError as exc:
        raise AutoScanSourceError("GitHub CLI (gh) is not installed on the PC.") from exc
    except GhError as exc:
        raise AutoScanSourceError(str(exc)) from exc
    return GitHubLabelsResponse(
        labels=[GitHubLabelModel(**label) for label in labels],
        issue_types=[GitHubIssueTypeModel(**item) for item in issue_types],
    )


def _templates(app_state: Any) -> AutoScanTemplatesStore:
    store = getattr(app_state, "autoscan_templates", None)
    if store is None:
        raise RuntimeError("Auto Scan templates store is not initialised")
    return store


def list_templates(app_state: Any) -> AutoScanTemplatesResponse:
    return AutoScanTemplatesResponse(templates=_templates(app_state).list())


def save_template(app_state: Any, body: SaveTemplateRequest) -> AutoScanTemplatesResponse:
    """Save a template; ``TemplateLimitError`` when a new name passes the cap."""
    return AutoScanTemplatesResponse(templates=_templates(app_state).save(body.name, body.settings))


def template_not_found(template_id: str) -> str:
    """The ``404`` detail for an unknown template id, on both transports."""
    return f"Template '{template_id}' not found"


def delete_template(app_state: Any, template_id: str) -> AutoScanTemplatesResponse:
    """Delete a template; ``KeyError`` for an unknown id."""
    return AutoScanTemplatesResponse(templates=_templates(app_state).delete(template_id))


async def start_github_setup(
    app_state: Any, project_name: str, action: str, rows: int, cols: int,
) -> SessionInfo:
    """Open the terminal session that installs or signs in the GitHub CLI.

    The session runs in the workspace folder and ends with its command; the
    wizard checks the status again when the user comes back.
    """
    path = _workspace_path(app_state, project_name)
    if not os.path.isdir(path):
        raise AutoScanSourceError(f"Workspace folder not found: {path}")
    env: dict[str, str] | None = None
    if action == "install":
        command = install_command()
        if command is None:
            raise AutoScanSourceError(
                "Mobaide knows no installer for this PC. "
                "Install GitHub CLI from https://cli.github.com, then check again."
            )
    else:
        gh = _gh(app_state)
        try:
            gh_bin = gh.resolve_binary()
        except GhBinaryNotFoundError as exc:
            raise AutoScanSourceError("Install GitHub CLI first.") from exc
        hosts = await remote_hosts(path)
        command = sign_in_command(gh_bin, github_host(hosts) or "github.com")
        env = sign_in_env(gh_bin)

    manager = app_state.session_manager
    config = SessionConfig(
        session_id=uuid.uuid4().hex[:12],
        command=command.argv,
        env=env,
        rows=rows,
        cols=cols,
        project_name=project_name,
        kind=SessionKind.SHELL,
        exits_when_done=True,
    )
    session = await manager.create_session(config)
    broadcaster = getattr(app_state, "broadcaster", None)
    if broadcaster is not None:
        await broadcaster.notify(manager.list_sessions())
    return session.to_info()

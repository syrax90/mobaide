"""Auto Scan GitHub helpers shared by the REST router and the relay proxy.

Each function raises typed errors that both transports map to the same
status codes: ``KeyError`` → 404 (unknown workspace),
``AutoScanSourceError`` → 400 (the message says what to fix),
``MaxSessionsError`` → 409.
"""
from __future__ import annotations

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

from .models import GitHubLabelModel, GitHubStatusResponse


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


async def github_labels_for(app_state: Any, project_name: str) -> list[GitHubLabelModel]:
    """Labels of the workspace's GitHub repository, for the wizard's picker."""
    path = _workspace_path(app_state, project_name)
    gh = _gh(app_state)
    try:
        repo = await gh.repo_of(path)
        labels = await gh.list_labels(repo)
    except GhBinaryNotFoundError as exc:
        raise AutoScanSourceError("GitHub CLI (gh) is not installed on the PC.") from exc
    except GhError as exc:
        raise AutoScanSourceError(str(exc)) from exc
    return [GitHubLabelModel(**label) for label in labels]


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

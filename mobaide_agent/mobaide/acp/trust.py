"""Folder trust for chat sessions.

A coding agent run over ACP is non-interactive, so it skips the "Do you
trust the files in this folder?" prompt its terminal UI shows before it
loads the folder's own settings, hooks and MCP servers. A chat session asks
the same question in the chat UI instead, before the agent opens the folder.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from mobaide.agent._env_writer import atomic_write


class WorkspaceTrust(Protocol):
    """Whether the agent may load a folder's own configuration."""

    prompt_title: str
    prompt_text: str

    def is_trusted(self) -> bool: ...

    def grant(self) -> bool:
        """Record the trust; False when it holds for this session only."""
        ...


def claude_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Where the Claude Code CLI started with ``env`` keeps its global state."""
    config_dir = (env if env is not None else os.environ).get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


def _spellings(folder: str) -> tuple[str, ...]:
    # Windows builds of the CLI have keyed folders both as C:\x and C:/x.
    if sys.platform == "win32":
        return (folder, folder.replace("\\", "/"))
    return (folder,)


def _repo_root(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def _trust_candidates(cwd: str) -> list[str]:
    """The folders whose trust covers ``cwd``, as the CLI walks them: the
    real path and its parents, but not above the enclosing repository, so a
    trusted parent never vouches for a repository cloned inside it."""
    folder = Path(os.path.realpath(cwd))
    root = _repo_root(folder)
    out: list[str] = []
    for candidate in (folder, *folder.parents):
        out.extend(_spellings(str(candidate)))
        if candidate == root:
            break
    return out


def too_broad_to_record(cwd: str) -> bool:
    """The home folder, a folder above it, or a filesystem root: recording
    trust there would trust every folder below it, so the CLI never does."""
    folder = Path(os.path.realpath(cwd))
    home = Path(os.path.realpath(os.path.expanduser("~")))
    return folder.parent == folder or folder == home or folder in home.parents


class ClaudeFolderTrust:
    """Claude Code's own trust record: ``projects[<dir>].hasTrustDialogAccepted``
    in ``~/.claude.json``, where a trusted parent folder covers its children
    up to the enclosing repository root."""

    prompt_title = "Trust this folder?"

    def __init__(self, cwd: str, config_path: Path | None = None) -> None:
        self._cwd = cwd
        self._config_path = config_path or claude_config_path()

    @property
    def prompt_text(self) -> str:
        text = (
            "Claude Code will use this folder's settings, hooks and MCP servers. "
            "They can run commands on your PC. Only trust a folder whose files you know."
        )
        if too_broad_to_record(self._cwd):
            text += " Trust for this folder lasts for this session only."
        return text

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def is_trusted(self) -> bool:
        projects = self._load().get("projects")
        if not isinstance(projects, dict):
            return False
        for folder in _trust_candidates(self._cwd):
            entry = projects.get(folder)
            if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
                return True
        return False

    def grant(self) -> bool:
        """Record trust for the folder the way the CLI does (atomic rewrite).

        Returns False, and writes nothing, for a folder too broad to record.
        """
        if too_broad_to_record(self._cwd):
            return False
        path = self._config_path
        data: dict[str, Any] = {}
        if path.exists():
            # Never rewrite a file this code cannot read back whole.
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} is not a JSON object")
            data = loaded
        projects = data.get("projects")
        if not isinstance(projects, dict):
            projects = {}
            data["projects"] = projects
        folder = os.path.realpath(self._cwd)
        entry = projects.get(folder)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["hasTrustDialogAccepted"] = True
        projects[folder] = entry
        mode = path.stat().st_mode & 0o777 if path.exists() else None
        atomic_write(path, json.dumps(data, indent=2), mode=mode)
        return True

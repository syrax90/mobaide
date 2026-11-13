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
import threading
import time
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from mobaide.agent._env_writer import atomic_write
from mobaide.core.codex_auth import codex_home

from .presence import default_app_data_dir


_config_locks: dict[str, threading.Lock] = {}
_config_locks_guard = threading.Lock()


def _config_lock(path: Path) -> threading.Lock:
    """One lock per config file: concurrent grants for different folders
    each read, extend and replace the whole file, and would drop each other's
    record."""
    key = os.path.realpath(path)
    with _config_locks_guard:
        return _config_locks.setdefault(key, threading.Lock())


class WorkspaceTrust(Protocol):
    """Whether the agent may load a folder's own configuration."""

    @property
    def prompt_title(self) -> str: ...

    @property
    def prompt_text(self) -> str: ...

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
        with _config_lock(self._config_path):
            return self._grant_locked()

    def _grant_locked(self) -> bool:
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


def codex_config_path(env: Mapping[str, str] | None = None) -> Path:
    """The ``config.toml`` of the Codex CLI started with ``env``."""
    return Path(codex_home(env if env is not None else os.environ)) / "config.toml"


def _append_codex_trust(text: str, folder: str) -> str:
    # A JSON string is a valid TOML basic string for any real path.
    block = f'[projects.{json.dumps(folder, ensure_ascii=False)}]\ntrust_level = "trusted"\n'
    if not text:
        return block
    return text + ("\n" if text.endswith("\n") else "\n\n") + block


def _main_worktree_root(root: Path) -> Path | None:
    """The main checkout of the linked git worktree at ``root``, or None.

    A linked worktree's ``.git`` is a file naming ``<main>/.git/worktrees/<n>``;
    Codex trusts such a worktree through its main checkout.
    """
    dotgit = root / ".git"
    try:
        text = dotgit.read_text(encoding="utf-8").strip() if dotgit.is_file() else ""
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(os.path.realpath(root / text[len("gitdir:"):].strip()))
    try:
        common = Path(os.path.realpath(gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()))
    except OSError:
        if gitdir.parent.name != "worktrees":
            return None
        common = gitdir.parent.parent
    if common.name != ".git" or common.parent == root:
        return None
    return common.parent


def _codex_trust_candidates(cwd: str) -> list[str]:
    """The keys Codex looks up, in its order: the folder (real path, then as
    given), then its git repository root, then the main checkout of a linked
    worktree. No other parent folder counts."""
    folder = Path(os.path.realpath(cwd))
    keys = [*_spellings(str(folder)), *_spellings(os.path.abspath(cwd))]
    root = _repo_root(folder)
    if root is not None:
        if root != folder:
            keys.extend(_spellings(str(root)))
        main = _main_worktree_root(root)
        if main is not None:
            keys.extend(_spellings(str(main)))
    return list(dict.fromkeys(keys))


class CodexFolderTrust:
    """Codex's own trust record: ``[projects."<dir>"] trust_level`` in
    ``config.toml``. The first recorded key decides (see
    :func:`_codex_trust_candidates`), and Codex records a repository by its
    root."""

    prompt_title = "Trust this folder?"

    def __init__(self, cwd: str, config_path: Path | None = None) -> None:
        self._cwd = cwd
        self._config_path = config_path or codex_config_path()

    def _record_folder(self) -> str:
        folder = Path(os.path.realpath(self._cwd))
        root = _repo_root(folder)
        if root is None:
            return str(folder)
        return str(_main_worktree_root(root) or root)

    @property
    def prompt_text(self) -> str:
        text = (
            "Codex will use this folder's settings, hooks and MCP servers. "
            "They can run commands on your PC. Only trust a folder whose files you know."
        )
        if too_broad_to_record(self._record_folder()):
            text += " Trust for this folder lasts for this session only."
        return text

    def _load(self) -> dict[str, Any]:
        try:
            return tomllib.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def is_trusted(self) -> bool:
        projects = self._load().get("projects")
        if not isinstance(projects, dict):
            return False
        for folder in _codex_trust_candidates(self._cwd):
            if folder in projects:
                entry = projects[folder]
                return isinstance(entry, dict) and entry.get("trust_level") == "trusted"
        return False

    def grant(self) -> bool:
        """Append the trust record (atomic rewrite).

        Returns False, and writes nothing, for a folder too broad to record
        or one the file already lists: an edit of an existing table could
        break the user's own formatting, so that trust lasts for the session.
        """
        folder = self._record_folder()
        if too_broad_to_record(folder):
            return False
        with _config_lock(self._config_path):
            return self._grant_locked(folder)

    def _grant_locked(self, folder: str) -> bool:
        path = self._config_path
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        # Never rewrite a file this code cannot read back whole.
        before = tomllib.loads(text)
        projects = before.get("projects", {})
        if not isinstance(projects, dict):
            raise ValueError(f"{path}: projects is not a table")
        if folder in projects:
            return False
        updated = _append_codex_trust(text, folder)
        expected = {**before, "projects": {**projects, folder: {"trust_level": "trusted"}}}
        if tomllib.loads(updated) != expected:
            raise ValueError(f"{path}: could not add the trust record safely")
        mode = path.stat().st_mode & 0o777 if path.exists() else None
        atomic_write(path, updated, mode=mode)
        return True


def antigravity_settings_path(app_data_dir: Path | None = None) -> Path:
    """The ``settings.json`` of the Antigravity CLI (``agy``)."""
    return (app_data_dir or default_app_data_dir()) / "settings.json"


def _same_folder(a: str, b: str) -> bool:
    if sys.platform == "win32":
        return os.path.normcase(a) == os.path.normcase(b)
    return a == b


class AntigravityFolderTrust:
    """Antigravity's own trust record: the ``trustedWorkspaces`` list in its
    ``settings.json``. ``agy`` trusts exactly the listed folders (real path):
    neither a listed parent nor the repository root covers a subfolder."""

    prompt_title = "Trust this folder?"

    def __init__(self, cwd: str, settings_path: Path | None = None) -> None:
        self._cwd = cwd
        self._settings_path = settings_path or antigravity_settings_path()

    @property
    def prompt_text(self) -> str:
        text = (
            "Antigravity will read, edit and run files in this folder, and use its "
            "rules, skills and MCP servers. Only trust a folder whose files you know."
        )
        if too_broad_to_record(self._cwd):
            text += " Trust for this folder lasts for this session only."
        return text

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def is_trusted(self) -> bool:
        listed = self._load().get("trustedWorkspaces")
        if not isinstance(listed, list):
            return False
        folder = os.path.realpath(self._cwd)
        for entry in listed:
            if isinstance(entry, str) and entry and (
                _same_folder(entry, folder) or _same_folder(os.path.realpath(entry), folder)
            ):
                return True
        return False

    def grant(self) -> bool:
        """Add the folder to ``trustedWorkspaces`` as ``agy`` does (atomic
        rewrite). Returns False, and writes nothing, for a folder too broad to
        record."""
        if too_broad_to_record(self._cwd):
            return False
        with _config_lock(self._settings_path):
            return self._grant_locked()

    def _grant_locked(self) -> bool:
        path = self._settings_path
        data: dict[str, Any] = {}
        if path.exists():
            # Never rewrite a file this code cannot read back whole.
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} is not a JSON object")
            data = loaded
        listed = data.get("trustedWorkspaces")
        if listed is None:
            listed = []
        if not isinstance(listed, list):
            raise ValueError(f"{path}: trustedWorkspaces is not a list")
        folder = os.path.realpath(self._cwd)
        if any(isinstance(e, str) and _same_folder(e, folder) for e in listed):
            return True
        data["trustedWorkspaces"] = [*listed, folder]
        # agy keeps the file private to the user.
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode=mode)
        return True


def copilot_config_path(env: Mapping[str, str] | None = None) -> Path:
    """The ``config.json`` of the GitHub Copilot CLI started with ``env``: the
    state Copilot manages itself, trusted folders included."""
    home = (env if env is not None else os.environ).get("COPILOT_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".copilot"
    return base / "config.json"


# The comment lines Copilot writes above the JSON object.
_COPILOT_CONFIG_HEADER = (
    "// User settings belong in settings.json.\n// This file is managed automatically.\n"
)


def _split_copilot_config(text: str) -> tuple[str, str]:
    """``(comment lines at the top, JSON object)`` of Copilot's ``config.json``."""
    lines = text.splitlines(keepends=True)
    body_at = 0
    while body_at < len(lines) and (
        not lines[body_at].strip() or lines[body_at].lstrip().startswith("//")
    ):
        body_at += 1
    return "".join(lines[:body_at]), "".join(lines[body_at:])


def _inside(folder: str, parent: str) -> bool:
    if sys.platform == "win32":
        folder, parent = os.path.normcase(folder), os.path.normcase(parent)
    try:
        return os.path.commonpath([folder, parent]) == parent
    except ValueError:
        # Different drives, or a relative entry.
        return False


class CopilotFolderTrust:
    """GitHub Copilot's own trust record: the ``trustedFolders`` list in its
    ``config.json``. A listed folder also covers the folders inside it."""

    prompt_title = "Trust this folder?"

    def __init__(self, cwd: str, config_path: Path | None = None) -> None:
        self._cwd = cwd
        self._config_path = config_path or copilot_config_path()

    @property
    def prompt_text(self) -> str:
        text = (
            "GitHub Copilot will use this folder's instructions, hooks and MCP servers. "
            "They can run commands on your PC. Only trust a folder whose files you know."
        )
        if too_broad_to_record(self._cwd):
            text += " Trust for this folder lasts for this session only."
        else:
            text += " The trust also covers every folder inside it."
        return text

    def _load(self) -> dict[str, Any]:
        try:
            body = _split_copilot_config(self._config_path.read_text(encoding="utf-8"))[1]
            data = json.loads(body)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def is_trusted(self) -> bool:
        listed = self._load().get("trustedFolders")
        if not isinstance(listed, list):
            return False
        folder = os.path.realpath(self._cwd)
        for entry in listed:
            if not isinstance(entry, str) or not os.path.isabs(entry):
                continue
            if _inside(folder, os.path.normpath(entry)) or _inside(folder, os.path.realpath(entry)):
                return True
        return False

    def grant(self) -> bool:
        """Add the folder to ``trustedFolders`` as Copilot does (atomic
        rewrite). Returns False, and writes nothing, for a folder too broad to
        record."""
        if too_broad_to_record(self._cwd):
            return False
        with _config_lock(self._config_path):
            return self._grant_locked()

    def _grant_locked(self) -> bool:
        path = self._config_path
        header = _COPILOT_CONFIG_HEADER
        data: dict[str, Any] = {}
        if path.exists():
            header, body = _split_copilot_config(path.read_text(encoding="utf-8"))
            # Never rewrite a file this code cannot read back whole.
            loaded = json.loads(body) if body.strip() else {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} is not a JSON object")
            data = loaded
        listed = data.get("trustedFolders")
        if listed is None:
            listed = []
        if not isinstance(listed, list):
            raise ValueError(f"{path}: trustedFolders is not a list")
        folder = os.path.realpath(self._cwd)
        if any(isinstance(e, str) and _same_folder(e, folder) for e in listed):
            return True
        data["trustedFolders"] = [*listed, folder]
        # Copilot keeps the file private to the user.
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        atomic_write(path, header + json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode=mode)
        return True


# What makes Pi ask its trust question: these entries of a folder's ``.pi``,
# or an ``.agents/skills`` folder in it or above it.
_PI_TRUST_RESOURCES = (
    "settings.json",
    "mcp.json",
    "extensions",
    "skills",
    "prompts",
    "themes",
    "SYSTEM.md",
    "APPEND_SYSTEM.md",
)
# Pi holds this folder-lock while it rewrites its trust file, and takes one
# older than this from a process that died.
_PI_LOCK_STALE_SECONDS = 10.0
_PI_LOCK_WAIT_SECONDS = 2.0


def pi_agent_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where the Pi started with ``env`` keeps its settings and trust file."""
    custom = (env if env is not None else os.environ).get("PI_CODING_AGENT_DIR")
    if custom:
        return Path(os.path.abspath(os.path.expanduser(custom)))
    return Path.home() / ".pi" / "agent"


class PiFolderTrust:
    """Pi's own trust record: ``trust.json`` in its folder maps a real path
    to ``true`` or ``false``, and the closest entry of a folder or its
    parents decides.

    Pi asks only for a folder with resources it would load (see
    :data:`_PI_TRUST_RESOURCES`), and only while its ``defaultProjectTrust``
    setting is ``ask``; without a terminal it skips them instead. So the chat
    asks in exactly that case. A saved "do not trust" is a decision too: Pi
    then starts without the folder's resources, and the chat does not ask
    again.
    """

    prompt_title = "Trust this folder?"
    prompt_text = (
        "Pi will load this folder's settings, extensions, skills and MCP servers "
        "(its .pi folder and .agents/skills). They can run commands on your PC. "
        "Only trust a folder whose files you know. The trust also covers every "
        "folder inside it."
    )

    def __init__(self, cwd: str, agent_dir: Path | None = None) -> None:
        self._cwd = cwd
        self._agent_dir = agent_dir or pi_agent_dir()

    @property
    def _trust_path(self) -> Path:
        return self._agent_dir / "trust.json"

    def _has_resources(self) -> bool:
        folder = Path(os.path.realpath(self._cwd))
        if any(os.path.lexists(folder / ".pi" / name) for name in _PI_TRUST_RESOURCES):
            return True
        # The user's own ``~/.agents/skills`` is theirs, not a project's.
        own = Path(os.path.realpath(os.path.expanduser("~"))) / ".agents" / "skills"
        for candidate in (folder, *folder.parents):
            skills = candidate / ".agents" / "skills"
            if skills != own and os.path.lexists(skills):
                return True
        return False

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._trust_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _saved_decision(self) -> bool | None:
        data = self._load()
        folder = Path(os.path.realpath(self._cwd))
        for candidate in (folder, *folder.parents):
            for key, value in data.items():
                if isinstance(value, bool) and _same_folder(key, str(candidate)):
                    return value
        return None

    def _asks(self) -> bool:
        try:
            settings = json.loads(
                (self._agent_dir / "settings.json").read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError):
            return True
        mode = settings.get("defaultProjectTrust") if isinstance(settings, dict) else None
        return mode not in ("always", "never")

    def is_trusted(self) -> bool:
        """Whether Pi needs no answer for this folder: nothing in it to
        trust, a saved decision, or a setting that decides for every folder."""
        if not self._has_resources():
            return True
        return self._saved_decision() is not None or not self._asks()

    def grant(self) -> bool:
        """Save the trust as Pi's ``/trust`` does (atomic rewrite under Pi's
        own lock)."""
        with _config_lock(self._trust_path):
            lock = Path(f"{self._trust_path}.lock")
            self._agent_dir.mkdir(parents=True, exist_ok=True)
            held = self._acquire(lock)
            try:
                self._grant_locked()
            finally:
                if held:
                    try:
                        lock.rmdir()
                    except OSError:
                        pass
        return True

    @staticmethod
    def _acquire(lock: Path) -> bool:
        deadline = time.monotonic() + _PI_LOCK_WAIT_SECONDS
        while True:
            try:
                lock.mkdir()
                return True
            except FileExistsError:
                try:
                    stale = time.time() - lock.stat().st_mtime > _PI_LOCK_STALE_SECONDS
                except OSError:
                    # Gone again, or not a folder anyone can take.
                    stale = False
                if stale or time.monotonic() >= deadline:
                    # A lock nobody refreshes, or one held too long to wait
                    # for: the rewrite below is atomic either way.
                    return False
                time.sleep(0.05)

    def _grant_locked(self) -> None:
        path = self._trust_path
        data: dict[str, Any] = {}
        if path.exists():
            # Never rewrite a file this code cannot read back whole.
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} is not a JSON object")
            data = loaded
        folder = os.path.realpath(self._cwd)
        data = {key: value for key, value in data.items() if not _same_folder(key, folder)}
        data[folder] = True
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        atomic_write(
            path,
            json.dumps(dict(sorted(data.items())), indent=2, ensure_ascii=False) + "\n",
            mode=mode,
        )

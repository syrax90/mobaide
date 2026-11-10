"""Mutable, file-backed store for project definitions.

The disk format is a JSON list of project records, each with an explicit
``order`` field. On first load, if ``projects.json`` is missing and the
``PROJECTS`` env var is set, the store seeds itself from the env var and
persists; the env var is ignored on every subsequent load.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from .projects_config import AutoScanFilter, ProjectConfig

logger = logging.getLogger(__name__)

# Single source of truth for the historical-equivalent filter values used as
# fallbacks when a stored record is missing or malforms a field. ``AutoScanFilter``
# is a frozen dataclass so this shared instance is safe to read concurrently.
_FILTER_DEFAULTS = AutoScanFilter()


def safe_path_exists(p: Path) -> bool:
    """``p.exists()`` that swallows ``OSError`` instead of propagating it.

    A user's project path may legitimately be unreadable by the agent
    process — e.g. another user's home with mode-0700, an NFS mount that's
    bouncing, or a broken symlink. ``Path.exists()`` raises ``PermissionError``
    in those cases, which is fine for opportunistic probes (``is this a git
    repo?``, ``does the path resolve?``) but ruinous when the probe is on
    the hot path of ``GET /api/projects``: a single un-stat-able row poisons
    the whole list with HTTP 500 and the mobile client surfaces nothing.
    Treating ``OSError`` as "no" keeps the rest of the response intact;
    operations that actually need the path (worktree creation, ``git``
    invocations) will fail loudly with the real OS error at their own
    call site.
    """
    try:
        return p.exists()
    except OSError:
        return False


def normalize_project_path(raw: str) -> str:
    """Return ``raw`` as a canonical POSIX path string.

    Windows paths like ``D:\\ML\\Projects\\x`` become ``D:/ML/Projects/x``.
    The forward-slash form works on both Windows (``CreateFile``, ``pywinpty``,
    ``subprocess``) and POSIX, and survives JSON / ``repr`` / log formatting
    without any escape layer — which is what kept ``[Errno 2]`` messages
    rendering with quadruple backslashes on the mobile client.

    Idempotent: calling this on an already-normalized path is a no-op, so
    repeated loads of ``projects.json`` do not oscillate.
    """
    stripped = raw.strip()
    if not stripped:
        raise ValueError("project path must be non-empty")
    # PurePath collapses doubled separators ("D:\\\\ML" on Windows,
    # "////home" on POSIX) without touching the filesystem.
    return PurePath(stripped).as_posix()


@dataclass
class ProjectRecord:
    """In-memory mutable representation of a project row.

    Never escapes the store: callers receive ``ProjectConfig`` snapshots or
    client-safe dicts via the public read APIs.
    """

    name: str
    path: str
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] = field(default_factory=dict)
    order: int = 0
    auto_scan_filter: AutoScanFilter = field(default_factory=AutoScanFilter)
    auto_scan_prompt: str | None = None
    # Base branch of the last worktree a client created in this project;
    # agent-private, so every client and device preselects the same one.
    last_worktree_base: str | None = None


@dataclass
class ProjectUpdatePatch:
    """Typed patch consumed by :meth:`ProjectsStore.update`.

    Semantics match the GitHub/Stripe "null = keep" convention so an edit
    round-trip cannot accidentally wipe a secret:

    * ``name`` / ``path``: ``None`` keeps, a string replaces.
    * ``linear_api_key`` / ``conda_env``: ``None`` keeps, ``""`` clears
      (stored as ``None``), any other string replaces.
    * ``default_flags``: ``None`` keeps, a dict replaces.
    * ``auto_scan_filter``: ``None`` keeps, a filter replaces wholesale.
    * ``auto_scan_prompt``: ``None`` keeps, ``""`` clears (stored as ``None``,
      so the engine-wide default prompt is used), any other string replaces.
    """

    name: str | None = None
    path: str | None = None
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] | None = None
    auto_scan_filter: AutoScanFilter | None = None
    auto_scan_prompt: str | None = None


class ProjectsStore:
    """JSON-file backed mutable projects store.

    Concurrency: in-memory mutations + atomic disk writes are serialised with
    a :class:`threading.Lock`. The writer set is assumed to be the single
    agent process — cross-process flocks are out of scope.

    Durability: each mutation calls ``_save_unlocked`` which writes to a
    ``.tmp`` sibling and then ``os.replace``-s onto the target, so the store
    is never observed in a half-written state.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._projects: dict[str, ProjectRecord] = {}
        self._lock = threading.Lock()

    def load(self, seed_json: str | None = None) -> None:
        """Populate the store from disk, seeding from JSON if needed.

        On the first boot (``projects.json`` absent) the ``seed_json`` arg
        is parsed with the same rules the legacy ``ProjectsConfig.from_dict``
        used and the result — including an empty list — is persisted. From
        that point on, the seed is ignored and the file is the source of
        truth.

        ``seed_json`` defaults to the ``PROJECTS`` env var. Callers that
        load the agent config via ``pydantic-settings`` should pass
        ``config.projects`` explicitly: pydantic-settings reads `.env` into
        the settings instance but does NOT mutate ``os.environ``, so reading
        the env var here would silently miss values defined only in `.env`
        on platforms where the supervisor doesn't pre-load it (e.g. the
        Windows Service launcher.cmd).
        """
        with self._lock:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                items = json.loads(raw) if raw.strip() else []
                if not isinstance(items, list):
                    raise RuntimeError(
                        f"projects store at {self._path} is not a JSON list",
                    )
                self._projects = {}
                for item in items:
                    rec = _record_from_dict(item)
                    if rec is None:
                        continue
                    self._projects[rec.name] = rec
                self._renumber_unlocked()
                logger.info(
                    "projects_store.loaded path=%s count=%d",
                    self._path, len(self._projects),
                )
                return

            seeded = _seed_from_env(seed_json)
            for rec in seeded:
                self._projects[rec.name] = rec
            self._save_unlocked()
            logger.info(
                "projects_store.%s path=%s count=%d",
                "seeded_from_env" if seeded else "created_empty",
                self._path, len(seeded),
            )

    def save(self) -> None:
        """Persist the current in-memory state to disk."""
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        """Atomic write. Must be called with ``self._lock`` held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = [
            _record_to_dict(r)
            for r in sorted(self._projects.values(), key=lambda r: r.order)
        ]
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def _renumber_unlocked(self) -> None:
        """Compact ``order`` to a 0..n-1 dense range preserving current order."""
        for i, rec in enumerate(
            sorted(self._projects.values(), key=lambda r: r.order),
        ):
            rec.order = i

    def list_public(self) -> list[dict[str, object]]:
        """Return all projects as client-safe dicts (no ``linear_api_key``)."""
        with self._lock:
            records = sorted(self._projects.values(), key=lambda r: r.order)
            return [_record_to_public(r) for r in records]

    def list_with_linear_key(self) -> list[str]:
        """Names of projects with a non-empty ``linear_api_key``, in order."""
        with self._lock:
            return [
                r.name
                for r in sorted(self._projects.values(), key=lambda r: r.order)
                if r.linear_api_key
            ]

    def get(self, name: str) -> ProjectConfig | None:
        """Snapshot a record as an immutable launcher-facing ``ProjectConfig``."""
        with self._lock:
            r = self._projects.get(name)
            return _record_to_config(r) if r is not None else None

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return isinstance(name, str) and name in self._projects

    def __len__(self) -> int:
        with self._lock:
            return len(self._projects)

    def create(self, record: ProjectRecord) -> ProjectConfig:
        """Insert a new project; ``order`` is assigned automatically.

        Raises:
            KeyError: a project with the same name already exists.
        """
        with self._lock:
            if record.name in self._projects:
                raise KeyError(f"Project '{record.name}' already exists")
            record.order = len(self._projects)
            self._projects[record.name] = record
            self._save_unlocked()
            return _record_to_config(record)

    def update(self, name: str, patch: ProjectUpdatePatch) -> ProjectConfig:
        """Apply ``patch`` to the project ``name``.

        Raises:
            KeyError: ``name`` is not found.
            ValueError: rename target collides with an existing project.
        """
        with self._lock:
            record = self._projects.get(name)
            if record is None:
                raise KeyError(f"Project '{name}' not found")

            new_name = record.name
            if patch.name is not None and patch.name != record.name:
                if not patch.name:
                    raise ValueError("renamed project name must be non-empty")
                if patch.name in self._projects:
                    raise ValueError(
                        f"Cannot rename '{name}' to '{patch.name}': "
                        "target name already exists",
                    )
                new_name = patch.name

            if patch.path is not None:
                record.path = patch.path

            if patch.linear_api_key is not None:
                record.linear_api_key = (
                    None if patch.linear_api_key == ""
                    else patch.linear_api_key
                )

            if patch.conda_env is not None:
                record.conda_env = (
                    None if patch.conda_env == "" else patch.conda_env
                )

            if patch.default_flags is not None:
                record.default_flags = dict(patch.default_flags)

            if patch.auto_scan_filter is not None:
                record.auto_scan_filter = patch.auto_scan_filter

            if patch.auto_scan_prompt is not None:
                record.auto_scan_prompt = (
                    None if patch.auto_scan_prompt == "" else patch.auto_scan_prompt
                )

            if new_name != record.name:
                self._projects.pop(record.name)
                record.name = new_name
                self._projects[new_name] = record

            self._save_unlocked()
            return _record_to_config(record)

    def get_last_worktree_base(self, name: str) -> str | None:
        with self._lock:
            r = self._projects.get(name)
            return r.last_worktree_base if r is not None else None

    def set_last_worktree_base(self, name: str, base: str | None) -> None:
        """Remember ``base`` for ``name`` (``None`` = the default branch);
        an unknown project is a no-op."""
        with self._lock:
            r = self._projects.get(name)
            if r is None or r.last_worktree_base == base:
                return
            r.last_worktree_base = base
            self._save_unlocked()

    def delete(self, name: str) -> None:
        """Remove ``name`` and compact the remaining ``order`` values.

        Raises:
            KeyError: ``name`` is not found.
        """
        with self._lock:
            if name not in self._projects:
                raise KeyError(f"Project '{name}' not found")
            del self._projects[name]
            self._renumber_unlocked()
            self._save_unlocked()

    def reorder(self, names: list[str]) -> None:
        """Replace the project ordering atomically.

        ``names`` must be a permutation of the currently stored set.

        Raises:
            ValueError: duplicates, missing names, or unknown names.
        """
        with self._lock:
            if len(names) != len(set(names)):
                raise ValueError(
                    "reorder request contains duplicate project names",
                )
            current = set(self._projects.keys())
            requested = set(names)
            if current != requested:
                missing = sorted(current - requested)
                extra = sorted(requested - current)
                parts: list[str] = []
                if missing:
                    parts.append(f"missing={missing!r}")
                if extra:
                    parts.append(f"unknown={extra!r}")
                raise ValueError("reorder set mismatch: " + ", ".join(parts))
            for i, n in enumerate(names):
                self._projects[n].order = i
            self._save_unlocked()


def _filter_from_dict(data: object) -> AutoScanFilter:
    """Parse a stored ``auto_scan_filter`` blob.

    Tolerates legacy records (missing field) by returning the historical
    default. Unknown / malformed values are silently coerced to defaults so
    a single corrupted entry does not block boot.
    """
    if not isinstance(data, dict):
        return AutoScanFilter()
    state_names = data.get("state_names")
    label_names = data.get("label_names")
    # Element-level filtering mirrors ``AutoScanFilterModel._reject_empty_elements`` and
    # ``_reject_blank_string`` in ``agent/projects/models.py`` so a hand-edited
    # ``projects.json`` cannot land an empty/whitespace-only string in the
    # store and crash the next ``GET /api/projects`` (which validates each
    # row through ``ProjectReadModel``). Lenient on type, strict on content.
    return AutoScanFilter(
        state_names=(
            [s for s in state_names if isinstance(s, str) and s.strip()]
            if isinstance(state_names, list)
            else list(_FILTER_DEFAULTS.state_names)
        ),
        label_names=(
            [s for s in label_names if isinstance(s, str) and s.strip()]
            if isinstance(label_names, list)
            else list(_FILTER_DEFAULTS.label_names)
        ),
        active_state=(
            data["active_state"]
            if isinstance(data.get("active_state"), str)
            and data["active_state"].strip()
            else _FILTER_DEFAULTS.active_state
        ),
        done_state=(
            data["done_state"]
            if isinstance(data.get("done_state"), str)
            and data["done_state"].strip()
            else _FILTER_DEFAULTS.done_state
        ),
        removal_label=(
            data["removal_label"]
            if isinstance(data.get("removal_label"), str)
            and data["removal_label"].strip()
            else _FILTER_DEFAULTS.removal_label
        ),
    )


def _filter_to_dict(f: AutoScanFilter) -> dict[str, object]:
    return {
        "state_names": list(f.state_names),
        "label_names": list(f.label_names),
        "active_state": f.active_state,
        "done_state": f.done_state,
        "removal_label": f.removal_label,
    }


def _record_from_dict(data: object) -> ProjectRecord | None:
    """Parse one stored record. Returns ``None`` if invalid."""
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    path = data.get("path")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(path, str):
        return None
    try:
        normalized_path = normalize_project_path(path)
    except ValueError:
        return None
    api_key = data.get("linear_api_key")
    conda_env = data.get("conda_env")
    default_flags = data.get("default_flags") or {}
    order = data.get("order")
    prompt = data.get("auto_scan_prompt")
    last_base = data.get("last_worktree_base")
    return ProjectRecord(
        name=name,
        path=normalized_path,
        linear_api_key=api_key if isinstance(api_key, str) else None,
        conda_env=conda_env if isinstance(conda_env, str) else None,
        default_flags=default_flags if isinstance(default_flags, dict) else {},
        order=order if isinstance(order, int) else 0,
        auto_scan_filter=_filter_from_dict(data.get("auto_scan_filter")),
        auto_scan_prompt=prompt if isinstance(prompt, str) and prompt.strip() else None,
        last_worktree_base=(
            last_base if isinstance(last_base, str) and last_base else None
        ),
    )


def _record_to_dict(r: ProjectRecord) -> dict[str, object]:
    d: dict[str, object] = {
        "name": r.name,
        "path": r.path,
        "order": r.order,
    }
    if r.linear_api_key is not None:
        d["linear_api_key"] = r.linear_api_key
    if r.conda_env is not None:
        d["conda_env"] = r.conda_env
    if r.default_flags:
        d["default_flags"] = dict(r.default_flags)
    if r.auto_scan_prompt is not None:
        d["auto_scan_prompt"] = r.auto_scan_prompt
    d["auto_scan_filter"] = _filter_to_dict(r.auto_scan_filter)
    if r.last_worktree_base is not None:
        d["last_worktree_base"] = r.last_worktree_base
    return d


def _record_to_config(r: ProjectRecord) -> ProjectConfig:
    return ProjectConfig(
        name=r.name,
        path=r.path,
        linear_api_key=r.linear_api_key,
        conda_env=r.conda_env,
        default_flags=dict(r.default_flags),
        auto_scan_filter=r.auto_scan_filter,
        auto_scan_prompt=r.auto_scan_prompt,
    )


def _record_to_public(r: ProjectRecord) -> dict[str, object]:
    p = Path(r.path)
    return {
        "name": r.name,
        "path": r.path,
        "conda_env": r.conda_env,
        "default_flags": dict(r.default_flags),
        "order": r.order,
        "has_linear_key": r.linear_api_key is not None,
        "is_git": safe_path_exists(p / ".git"),
        "path_exists": safe_path_exists(p),
        "auto_scan_filter": _filter_to_dict(r.auto_scan_filter),
        "auto_scan_prompt": r.auto_scan_prompt,
    }


def _seed_from_env(raw: str | None = None) -> list[ProjectRecord]:
    """Parse a ``PROJECTS``-style JSON string into records.

    Defaults to ``os.getenv("PROJECTS")`` for backward compat with callers
    that haven't migrated to passing the value through. After CLA-48 the
    canonical caller is ``ProjectsStore.load(seed_json=config.projects)``
    where ``config.projects`` is the pydantic-settings-resolved value.

    String values, non-dict values, and entries without a string ``path``
    are silently dropped. Returns an empty list when the input is unset,
    empty, or parses to a non-dict. Raises ``RuntimeError`` on invalid JSON
    to surface misconfiguration early rather than silently seeding an empty
    store.
    """
    if raw is None:
        raw = os.getenv("PROJECTS")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"PROJECTS environment variable contains invalid JSON: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        return []
    records: list[ProjectRecord] = []
    for name, value in parsed.items():
        if isinstance(value, str):
            continue
        if not isinstance(value, dict):
            continue
        path = value.get("path")
        if not isinstance(path, str):
            continue
        try:
            normalized_path = normalize_project_path(path)
        except ValueError:
            continue
        api_key = value.get("linear_api_key")
        conda_env = value.get("conda_env")
        default_flags = value.get("default_flags") or {}
        prompt = value.get("auto_scan_prompt")
        records.append(
            ProjectRecord(
                name=name,
                path=normalized_path,
                linear_api_key=api_key if isinstance(api_key, str) else None,
                conda_env=conda_env if isinstance(conda_env, str) else None,
                default_flags=(
                    default_flags if isinstance(default_flags, dict) else {}
                ),
                auto_scan_filter=_filter_from_dict(value.get("auto_scan_filter")),
                auto_scan_prompt=(
                    prompt if isinstance(prompt, str) and prompt.strip() else None
                ),
            ),
        )
    for i, r in enumerate(records):
        r.order = i
    return records

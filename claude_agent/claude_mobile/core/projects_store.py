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

from .projects_config import ProjectConfig

logger = logging.getLogger(__name__)


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


@dataclass
class ProjectUpdatePatch:
    """Typed patch consumed by :meth:`ProjectsStore.update`.

    Semantics match the GitHub/Stripe "null = keep" convention so an edit
    round-trip cannot accidentally wipe a secret:

    * ``name`` / ``path``: ``None`` keeps, a string replaces.
    * ``linear_api_key`` / ``conda_env``: ``None`` keeps, ``""`` clears
      (stored as ``None``), any other string replaces.
    * ``default_flags``: ``None`` keeps, a dict replaces.
    """

    name: str | None = None
    path: str | None = None
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] | None = None


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

    def load(self) -> None:
        """Populate the store from disk, seeding from ``PROJECTS`` if needed.

        On the first boot (``projects.json`` absent) the env var is parsed
        with the same rules the legacy ``ProjectsConfig.from_dict`` used and
        the result — including an empty list — is persisted. From that point
        on, the env var is ignored and the file is the source of truth.
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

            seeded = _seed_from_env()
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

            if new_name != record.name:
                self._projects.pop(record.name)
                record.name = new_name
                self._projects[new_name] = record

            self._save_unlocked()
            return _record_to_config(record)

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
    return ProjectRecord(
        name=name,
        path=normalized_path,
        linear_api_key=api_key if isinstance(api_key, str) else None,
        conda_env=conda_env if isinstance(conda_env, str) else None,
        default_flags=default_flags if isinstance(default_flags, dict) else {},
        order=order if isinstance(order, int) else 0,
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
    return d


def _record_to_config(r: ProjectRecord) -> ProjectConfig:
    return ProjectConfig(
        name=r.name,
        path=r.path,
        linear_api_key=r.linear_api_key,
        conda_env=r.conda_env,
        default_flags=dict(r.default_flags),
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
        "is_git": (p / ".git").exists(),
        "path_exists": p.exists(),
    }


def _seed_from_env() -> list[ProjectRecord]:
    """Parse the ``PROJECTS`` env var into records.

    String values, non-dict values, and entries without a string ``path``
    are silently dropped. Returns an empty list if ``PROJECTS`` is unset,
    empty, or parses to a non-dict. Raises ``RuntimeError`` on invalid JSON
    to surface misconfiguration early rather than silently seeding an empty
    store.
    """
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
        records.append(
            ProjectRecord(
                name=name,
                path=normalized_path,
                linear_api_key=api_key if isinstance(api_key, str) else None,
                conda_env=conda_env if isinstance(conda_env, str) else None,
                default_flags=(
                    default_flags if isinstance(default_flags, dict) else {}
                ),
            ),
        )
    for i, r in enumerate(records):
        r.order = i
    return records

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from mobaide.core.workspaces_store import normalize_workspace_path

try:
    from mobaide.agent.logging import audit_event, get_logger
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def audit_event(*args, **kwargs): pass

logger = get_logger(__name__)


@dataclass
class ActiveWorkItem:
    """An issue being worked on by auto-scan.

    Persisted to ``active_workitems.json`` so the scanner can later
    (once the issue is done in its tracker) remove the associated git
    worktree. Only a scan of the same ``source`` checks an item.
    """

    id: str  # Human-readable identifier, e.g. "TEAM-31" or "#42"
    uuid: str  # Tracker key for single-issue queries: Linear UUID, GitHub number
    slug: str  # Worktree directory name
    worktree_path: str
    project_root: str
    added_at: str  # ISO8601 UTC
    source: str = "linear"
    # GitHub only: the ``--repo`` selector the issue number belongs to.
    repo: str = ""

    @property
    def identity(self) -> tuple[str, str, str]:
        """What makes two items the same issue: ``id`` repeats across sources,
        and a GitHub number repeats across repositories."""
        return (self.source, self.repo, self.id)

    @classmethod
    def from_dict(cls, data: dict) -> "ActiveWorkItem":
        # Normalize both paths to POSIX on load so legacy backslash entries
        # match the canonical form — without this, the cleanup filter silently
        # drops pre-existing items after the path reconciliation migration.
        return cls(
            id=str(data["id"]),
            uuid=str(data["uuid"]),
            slug=str(data["slug"]),
            worktree_path=normalize_workspace_path(str(data["worktree_path"])),
            project_root=normalize_workspace_path(str(data["project_root"])),
            added_at=str(data["added_at"]),
            source=str(data.get("source") or "linear"),
            repo=str(data.get("repo") or ""),
        )


def _slug_workspace_name(name: str) -> str:
    """Sanitize a workspace name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9._-]", "_", name).strip("._-")
    return slug.lower() or "default"


def active_workitems_path(state_dir: str | os.PathLike, workspace_name: str) -> Path:
    """Return the absolute path to a workspace's active_workitems.json file."""
    return (Path(state_dir) / _slug_workspace_name(workspace_name) / "active_workitems.json").resolve()


def _path_key(path: str) -> str | None:
    try:
        normalized = normalize_workspace_path(path)
    except ValueError:
        return None
    return os.path.normcase(os.path.realpath(normalized))


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveWorkItemsStore:
    """Async-safe JSON store of active work items for one workspace.

    Persists a top-level JSON array to ``active_workitems.json``.
    One store instance per workspace — do not share across workspaces.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        logger.info(
            "autoscan.workitems.store_path resolved=%s", self._path,
        )

    @property
    def path(self) -> Path:
        return self._path

    async def read(self) -> list[ActiveWorkItem]:
        async with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> list[ActiveWorkItem]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "autoscan.workitems.read_failed path=%s error=%s",
                self._path, exc,
            )
            return []
        if not raw.strip():
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "autoscan.workitems.parse_failed path=%s error=%s — file will be overwritten on next write",
                self._path, exc,
            )
            return []
        if not isinstance(parsed, list):
            logger.warning(
                "autoscan.workitems.unexpected_shape path=%s — expected array, got %s",
                self._path, type(parsed).__name__,
            )
            return []
        items: list[ActiveWorkItem] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                items.append(ActiveWorkItem.from_dict(entry))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "autoscan.workitems.invalid_entry path=%s error=%s entry=%s",
                    self._path, exc, entry,
                )
        return items

    async def write(self, items: list[ActiveWorkItem]) -> None:
        async with self._lock:
            self._write_unlocked(items)

    def _write_unlocked(self, items: list[ActiveWorkItem]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([asdict(i) for i in items], indent=2, ensure_ascii=False)
        self._path.write_text(payload, encoding="utf-8")

    async def add(self, item: ActiveWorkItem) -> bool:
        """Add ``item`` if no existing entry shares its ``identity``.

        Returns True when the item was persisted, False when it was a
        duplicate. A duplicate is not an error — it just means the
        scanner is re-processing an issue that was already tracked.
        """
        async with self._lock:
            items = self._read_unlocked()
            if any(existing.identity == item.identity for existing in items):
                logger.info(
                    "autoscan.workitem.duplicate id=%s — already tracked, skipping",
                    item.id,
                )
                return False
            items.append(item)
            self._write_unlocked(items)
        logger.info(
            "autoscan.workitem.tracked id=%s uuid=%s worktree=%s",
            item.id, item.uuid, item.worktree_path,
        )
        audit_event(
            "audit.autoscan.workitem.added",
            target=item.id,
            outcome="ok",
            worktree_path=item.worktree_path,
        )
        return True

    async def remove(self, item: ActiveWorkItem) -> bool:
        """Remove the entry with ``item``'s ``identity``.

        Returns True if an entry was removed, False if none matched.
        """
        async with self._lock:
            items = self._read_unlocked()
            kept = [i for i in items if i.identity != item.identity]
            if len(kept) == len(items):
                return False
            self._write_unlocked(kept)
        audit_event(
            "audit.autoscan.workitem.removed",
            target=item.id,
            outcome="ok",
        )
        return True

    async def remove_at_path(self, worktree_path: str) -> int:
        """Remove every entry whose worktree is ``worktree_path``; returns
        how many went."""
        target = _path_key(worktree_path)
        if target is None:
            return 0
        async with self._lock:
            items = self._read_unlocked()
            gone = [i for i in items if _path_key(i.worktree_path) == target]
            if not gone:
                return 0
            self._write_unlocked([i for i in items if i not in gone])
        for item in gone:
            audit_event(
                "audit.autoscan.workitem.removed",
                target=item.id,
                outcome="ok",
                worktree_path=item.worktree_path,
            )
        return len(gone)

    async def count(self) -> int:
        async with self._lock:
            return len(self._read_unlocked())

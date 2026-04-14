from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from claude_mobile.agent.logging import audit_event, get_logger
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def audit_event(*args, **kwargs): pass

logger = get_logger(__name__)


@dataclass
class ActiveWorkItem:
    """A Linear issue being worked on by auto-scan.

    Persisted to ``active_workitems.json`` so the scanner can later
    (once the issue reaches the configured Done state) remove the
    associated git worktree.
    """

    id: str  # Linear identifier, e.g. "CLA-31"
    uuid: str  # Linear internal UUID (used for single-issue queries)
    slug: str  # Worktree directory name
    worktree_path: str
    project_root: str
    added_at: str  # ISO8601 UTC

    @classmethod
    def from_dict(cls, data: dict) -> "ActiveWorkItem":
        return cls(
            id=str(data["id"]),
            uuid=str(data["uuid"]),
            slug=str(data["slug"]),
            worktree_path=str(data["worktree_path"]),
            project_root=str(data["project_root"]),
            added_at=str(data["added_at"]),
        )


def _slug_project_name(name: str) -> str:
    """Sanitize a project name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9._-]", "_", name).strip("._-")
    return slug.lower() or "default"


def active_workitems_path(state_dir: str | os.PathLike, project_name: str) -> Path:
    """Return the absolute path to a project's active_workitems.json file."""
    return (Path(state_dir) / _slug_project_name(project_name) / "active_workitems.json").resolve()


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveWorkItemsStore:
    """Async-safe JSON store of active work items for one project.

    The file format mirrors the PowerShell reference implementation
    (``active_workitems.json``): a top-level JSON array of objects
    keyed by the Linear issue identifier.

    One store instance is created per ``AutoScanInstance``; callers
    must not share stores across projects (the file is per-project).
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
        """Add ``item`` if no existing entry shares its ``id``.

        Returns True when the item was persisted, False when it was a
        duplicate. A duplicate is not an error — it just means the
        scanner is re-processing an issue that was already tracked.
        """
        async with self._lock:
            items = self._read_unlocked()
            if any(existing.id == item.id for existing in items):
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

    async def remove(self, item_id: str) -> bool:
        """Remove the entry whose ``id == item_id``.

        Returns True if an entry was removed, False if none matched.
        """
        async with self._lock:
            items = self._read_unlocked()
            kept = [i for i in items if i.id != item_id]
            if len(kept) == len(items):
                return False
            self._write_unlocked(kept)
        audit_event(
            "audit.autoscan.workitem.removed",
            target=item_id,
            outcome="ok",
        )
        return True

    async def count(self) -> int:
        async with self._lock:
            return len(self._read_unlocked())

    def peek_count(self) -> int:
        """Synchronous best-effort size read.

        Intended for DTO renderers where an ``async`` context is
        unavailable (e.g. ``AutoScanInstance.to_dto``). Does NOT take
        the lock — a concurrent ``write`` may produce a slightly stale
        number, which is acceptable for status reporting.
        """
        try:
            return len(self._read_unlocked())
        except Exception:
            return 0

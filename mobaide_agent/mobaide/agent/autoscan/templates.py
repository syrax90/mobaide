"""Auto Scan templates: named sets of wizard choices, saved on this PC.

One JSON file next to the workspaces store holds every template. Writes are
atomic (a ``.tmp`` sibling, then ``os.replace``) and serialised with a lock,
like :class:`mobaide.core.workspaces_store.WorkspacesStore`. A stored entry
the current models reject is left out of every answer, so one bad entry never
hides the others, but each write keeps it as it was: an older agent must not
erase a newer agent's template. A file that is not a JSON list is copied to
``<name>.bak`` before the first write replaces it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .models import TEMPLATES_MAX, AutoScanTemplate, AutoScanTemplateSettings

logger = logging.getLogger(__name__)

TEMPLATES_FILE_NAME = "autoscan_templates.json"
# Every answer carries the whole list in one relay frame; the central server
# closes the tunnel above 16 MiB.
TEMPLATES_BYTES_MAX = 8 * 1024 * 1024


class TemplateLimitError(Exception):
    """Saving would pass ``TEMPLATES_MAX`` templates or ``TEMPLATES_BYTES_MAX``."""


class AutoScanTemplatesStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._templates: list[AutoScanTemplate] | None = None
        # Stored entries the current models reject, written back unchanged.
        self._unread: list[object] = []
        self._backup_before_write = False

    def _loaded_unlocked(self) -> list[AutoScanTemplate]:
        if self._templates is None:
            self._templates = self._read_unlocked()
        return self._templates

    def _read_unlocked(self) -> list[AutoScanTemplate]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError):
            logger.warning("autoscan_templates.read_failed path=%s", self._path, exc_info=True)
            self._backup_before_write = True
            return []
        try:
            items = json.loads(raw) if raw.strip() else []
        except ValueError:
            items = None
        if not isinstance(items, list):
            logger.warning("autoscan_templates.not_a_list path=%s", self._path)
            self._backup_before_write = True
            return []
        templates: list[AutoScanTemplate] = []
        for item in items:
            try:
                templates.append(AutoScanTemplate.model_validate(item))
            except ValidationError:
                logger.warning("autoscan_templates.entry_kept_unread path=%s", self._path)
                self._unread.append(item)
        return templates

    def _write_unlocked(self, templates: list[AutoScanTemplate]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._backup_before_write:
            try:
                os.replace(self._path, self._path.with_name(self._path.name + ".bak"))
            except FileNotFoundError:
                pass
            self._backup_before_write = False
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = [template.model_dump(mode="json") for template in templates] + self._unread
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        self._templates = templates

    def list(self) -> list[AutoScanTemplate]:
        with self._lock:
            return _sorted(self._loaded_unlocked())

    def save(self, name: str, settings: AutoScanTemplateSettings) -> list[AutoScanTemplate]:
        """Save ``settings`` as ``name``, replacing a template of the same
        name (ignoring case); returns every template.

        Raises ``TemplateLimitError`` when a new name would pass the cap, or
        the list would pass ``TEMPLATES_BYTES_MAX`` bytes.
        """
        with self._lock:
            current = list(self._loaded_unlocked())
            key = _name_key(name)
            index = next(
                (i for i, t in enumerate(current) if _name_key(t.name) == key), None,
            )
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if index is None:
                if len(current) >= TEMPLATES_MAX:
                    raise TemplateLimitError(
                        f"You can save at most {TEMPLATES_MAX} templates. Delete one first."
                    )
                current.append(
                    AutoScanTemplate(
                        id=uuid.uuid4().hex[:12], name=name, updated_at=now, settings=settings,
                    )
                )
            else:
                current[index] = AutoScanTemplate(
                    id=current[index].id, name=name, updated_at=now, settings=settings,
                )
            size = len(json.dumps([t.model_dump(mode="json") for t in current]))
            if size > TEMPLATES_BYTES_MAX:
                raise TemplateLimitError(
                    "The templates are too large to save. "
                    "Shorten the instructions or delete a template."
                )
            self._write_unlocked(current)
            return _sorted(current)

    def delete(self, template_id: str) -> list[AutoScanTemplate]:
        """Delete the template ``template_id``; returns every template left.

        Raises ``KeyError(template_id)`` for an unknown id.
        """
        with self._lock:
            current = self._loaded_unlocked()
            kept = [t for t in current if t.id != template_id]
            if len(kept) == len(current):
                raise KeyError(template_id)
            self._write_unlocked(kept)
            return _sorted(kept)


def _name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _sorted(templates: list[AutoScanTemplate]) -> list[AutoScanTemplate]:
    return sorted(templates, key=lambda t: _name_key(t.name))

"""Shared CRUD helpers for the projects store.

The HTTP router (``agent/projects/router.py``) and the central-server relay
proxy (``relay/client.py::_dispatch_proxy``) both mutate the projects store,
need identical conflict detection against the auto-scan manager, and must
both publish ``projects.changed`` on the shared event bus. Keeping that
logic in one place ensures mobile-via-central-server stays in sync with
direct HTTP.

The service raises structured exceptions (``DuplicateProjectError``,
``ProjectInUseError``, ``ReorderSetMismatchError``, ``KeyError``) that the
callers map to the appropriate HTTP status codes.
"""
from __future__ import annotations

from typing import Any, cast

from mobaide.core.projects_store import (
    ProjectRecord,
    ProjectsStore,
    ProjectUpdatePatch,
    normalize_project_path,
)

from ..logging import audit_event, get_logger
from .models import ProjectUpdateModel, ProjectWriteModel, ReorderRequest

logger = get_logger(__name__)


class DuplicateProjectError(Exception):
    """Raised when POST /api/projects hits an existing name (→ 409)."""


class ProjectInUseError(Exception):
    """Raised on delete/rename while an auto-scan is active (→ 409)."""


class ReorderSetMismatchError(Exception):
    """Raised when the provided reorder list does not match the set (→ 400)."""


def _publish_projects_changed(
    event_bus: Any | None,
    store: ProjectsStore,
) -> None:
    if event_bus is None:
        return
    try:
        event_bus.publish(
            "projects.changed",
            {"projects": store.list_public()},
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("projects.publish_failed")


def _check_scan_active(
    autoscan_manager: Any | None,
    project_name: str,
    *,
    verb: str,
) -> None:
    """Raise ProjectInUseError if an auto-scan is running for ``project_name``.

    ``verb`` ("delete", "rename") appears only in the error message so
    clients can surface a specific reason to the user.
    """
    if autoscan_manager is None:
        return
    get = getattr(autoscan_manager, "get", None)
    if get is None:
        return
    if get(project_name) is not None:
        raise ProjectInUseError(
            f"Cannot {verb} project '{project_name}': auto-scan is active. "
            "Stop the scan first.",
        )


def _patch_from_update_model(model: ProjectUpdateModel) -> ProjectUpdatePatch:
    """Build a store patch from the Pydantic update DTO.

    ``None`` in the DTO means "keep existing"; empty-string secret means
    "clear the key" — both must round-trip faithfully to the store. The
    ``auto_scan_filter`` field is wholesale (replace or keep — there's no
    meaningful "clear" operation, since the filter must always be present
    to keep the scanner running).
    """
    if model.linear_api_key is None:
        lak: str | None = None
    else:
        lak = model.linear_api_key.get_secret_value()
    # Normalize only when the caller is actually replacing the path; ``None``
    # must round-trip as ``None`` to preserve the "keep existing" contract.
    normalized_path = (
        normalize_project_path(model.path) if model.path is not None else None
    )
    auto_scan_filter = (
        model.auto_scan_filter.to_dataclass()
        if model.auto_scan_filter is not None
        else None
    )
    return ProjectUpdatePatch(
        name=model.name,
        path=normalized_path,
        linear_api_key=lak,
        conda_env=model.conda_env,
        default_flags=cast("dict[str, object] | None", model.default_flags),
        auto_scan_filter=auto_scan_filter,
    )


def _record_from_write_model(model: ProjectWriteModel) -> ProjectRecord:
    lak = (
        model.linear_api_key.get_secret_value()
        if model.linear_api_key is not None
        else None
    )
    record = ProjectRecord(
        name=model.name,
        path=normalize_project_path(model.path),
        linear_api_key=lak or None,
        conda_env=model.conda_env or None,
        default_flags=cast("dict[str, object]", dict(model.default_flags)),
    )
    if model.auto_scan_filter is not None:
        record.auto_scan_filter = model.auto_scan_filter.to_dataclass()
    return record


def _public_view(store: ProjectsStore, name: str) -> dict[str, object]:
    """Look up the public dict for ``name`` after a mutation."""
    for entry in store.list_public():
        if entry["name"] == name:
            return entry
    # Should never happen — the caller just wrote this record.
    raise KeyError(f"Project '{name}' not found after mutation")


def list_projects(store: ProjectsStore) -> list[dict[str, object]]:
    return store.list_public()


def create_project(
    store: ProjectsStore,
    body: ProjectWriteModel,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> dict[str, object]:
    """Insert a new project. Raises :class:`DuplicateProjectError` on conflict."""
    record = _record_from_write_model(body)
    try:
        store.create(record)
    except KeyError as exc:
        raise DuplicateProjectError(str(exc)) from exc
    view = _public_view(store, record.name)
    audit_event(
        "audit.project.created",
        actor=actor,
        target=record.name,
        outcome="ok",
        has_linear_key=record.linear_api_key is not None,
        path=record.path,
    )
    _publish_projects_changed(event_bus, store)
    return view


def update_project(
    store: ProjectsStore,
    autoscan_manager: Any | None,
    name: str,
    body: ProjectUpdateModel,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> dict[str, object]:
    """Apply a patch. Renames while an auto-scan is active are rejected.

    Raises:
        KeyError: ``name`` is not in the store (→ 404).
        ProjectInUseError: rename requested while a scan is active (→ 409).
        ValueError: rename target collides with an existing project (→ 409).
    """
    is_rename = (
        body.name is not None and body.name != name and body.name != ""
    )
    if is_rename:
        _check_scan_active(autoscan_manager, name, verb="rename")

    patch = _patch_from_update_model(body)
    updated = store.update(name, patch)
    view = _public_view(store, updated.name)
    audit_event(
        "audit.project.updated",
        actor=actor,
        target=updated.name,
        outcome="ok",
        renamed_from=name if is_rename else None,
        has_linear_key=updated.linear_api_key is not None,
    )
    _publish_projects_changed(event_bus, store)
    return view


def delete_project(
    store: ProjectsStore,
    autoscan_manager: Any | None,
    name: str,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> None:
    """Remove a project. Rejects if an auto-scan is active for ``name``.

    Raises:
        KeyError: ``name`` is not in the store (→ 404).
        ProjectInUseError: auto-scan active for ``name`` (→ 409).
    """
    _check_scan_active(autoscan_manager, name, verb="delete")
    store.delete(name)
    audit_event(
        "audit.project.deleted",
        actor=actor,
        target=name,
        outcome="ok",
    )
    _publish_projects_changed(event_bus, store)


def reorder_projects(
    store: ProjectsStore,
    body: ReorderRequest,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> list[dict[str, object]]:
    """Atomically replace project ordering.

    Raises:
        ReorderSetMismatchError: duplicates, missing, or unknown names (→ 400).
    """
    try:
        store.reorder(body.names)
    except ValueError as exc:
        raise ReorderSetMismatchError(str(exc)) from exc
    projects = store.list_public()
    audit_event(
        "audit.project.reordered",
        actor=actor,
        target=None,
        outcome="ok",
        order=list(body.names),
    )
    _publish_projects_changed(event_bus, store)
    return projects

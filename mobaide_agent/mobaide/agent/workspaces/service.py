"""Shared CRUD helpers for the workspaces store.

The HTTP router (``agent/workspaces/router.py``) and the central-server relay
proxy (``relay/client.py::_dispatch_proxy``) both mutate the workspaces store,
need identical conflict detection against the auto-scan manager, and must
both publish ``projects.changed`` on the shared event bus. Keeping that
logic in one place ensures mobile-via-central-server stays in sync with
direct HTTP.

The service raises structured exceptions (``DuplicateWorkspaceError``,
``WorkspaceInUseError``, ``ReorderSetMismatchError``, ``KeyError``) that the
callers map to the appropriate HTTP status codes.
"""
from __future__ import annotations

from typing import Any, cast

from mobaide.core.workspaces_store import (
    WorkspaceRecord,
    WorkspacesStore,
    WorkspaceUpdatePatch,
    normalize_workspace_path,
)

from ..logging import audit_event, get_logger
from .models import WorkspaceUpdateModel, WorkspaceWriteModel, ReorderRequest

logger = get_logger(__name__)


class DuplicateWorkspaceError(Exception):
    """Raised when POST /api/projects hits an existing name (→ 409)."""


class WorkspaceInUseError(Exception):
    """Raised on delete/rename while an auto-scan is active (→ 409)."""


class ReorderSetMismatchError(Exception):
    """Raised when the provided reorder list does not match the set (→ 400)."""


def _publish_workspaces_changed(
    event_bus: Any | None,
    store: WorkspacesStore,
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
    workspace_name: str,
    *,
    verb: str,
) -> None:
    """Raise WorkspaceInUseError if an auto-scan is running for ``workspace_name``.

    ``verb`` ("delete", "rename") appears only in the error message so
    clients can surface a specific reason to the user.
    """
    if autoscan_manager is None:
        return
    get = getattr(autoscan_manager, "get", None)
    if get is None:
        return
    if get(workspace_name) is not None:
        raise WorkspaceInUseError(
            f"Cannot {verb} workspace '{workspace_name}': auto-scan is active. "
            "Stop the scan first.",
        )


def _patch_from_update_model(model: WorkspaceUpdateModel) -> WorkspaceUpdatePatch:
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
        normalize_workspace_path(model.path) if model.path is not None else None
    )
    auto_scan_filter = (
        model.auto_scan_filter.to_dataclass()
        if model.auto_scan_filter is not None
        else None
    )
    return WorkspaceUpdatePatch(
        name=model.name,
        path=normalized_path,
        linear_api_key=lak,
        conda_env=model.conda_env,
        default_flags=cast("dict[str, object] | None", model.default_flags),
        auto_scan_filter=auto_scan_filter,
    )


def _record_from_write_model(model: WorkspaceWriteModel) -> WorkspaceRecord:
    lak = (
        model.linear_api_key.get_secret_value()
        if model.linear_api_key is not None
        else None
    )
    record = WorkspaceRecord(
        name=model.name,
        path=normalize_workspace_path(model.path),
        linear_api_key=lak or None,
        conda_env=model.conda_env or None,
        default_flags=cast("dict[str, object]", dict(model.default_flags)),
    )
    if model.auto_scan_filter is not None:
        record.auto_scan_filter = model.auto_scan_filter.to_dataclass()
    return record


def _public_view(store: WorkspacesStore, name: str) -> dict[str, object]:
    """Look up the public dict for ``name`` after a mutation."""
    for entry in store.list_public():
        if entry["name"] == name:
            return entry
    # Should never happen — the caller just wrote this record.
    raise KeyError(f"Workspace '{name}' not found after mutation")


def list_workspaces(store: WorkspacesStore) -> list[dict[str, object]]:
    return store.list_public()


def create_workspace(
    store: WorkspacesStore,
    body: WorkspaceWriteModel,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> dict[str, object]:
    """Insert a new workspace. Raises :class:`DuplicateWorkspaceError` on conflict."""
    record = _record_from_write_model(body)
    try:
        store.create(record)
    except KeyError as exc:
        raise DuplicateWorkspaceError(str(exc)) from exc
    view = _public_view(store, record.name)
    audit_event(
        "audit.workspace.created",
        actor=actor,
        target=record.name,
        outcome="ok",
        has_linear_key=record.linear_api_key is not None,
        path=record.path,
    )
    _publish_workspaces_changed(event_bus, store)
    return view


def update_workspace(
    store: WorkspacesStore,
    autoscan_manager: Any | None,
    name: str,
    body: WorkspaceUpdateModel,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> dict[str, object]:
    """Apply a patch. Renames while an auto-scan is active are rejected.

    Raises:
        KeyError: ``name`` is not in the store (→ 404).
        WorkspaceInUseError: rename requested while a scan is active (→ 409).
        ValueError: rename target collides with an existing workspace (→ 409).
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
        "audit.workspace.updated",
        actor=actor,
        target=updated.name,
        outcome="ok",
        renamed_from=name if is_rename else None,
        has_linear_key=updated.linear_api_key is not None,
    )
    _publish_workspaces_changed(event_bus, store)
    return view


def delete_workspace(
    store: WorkspacesStore,
    autoscan_manager: Any | None,
    name: str,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> None:
    """Remove a workspace. Rejects if an auto-scan is active for ``name``.

    Raises:
        KeyError: ``name`` is not in the store (→ 404).
        WorkspaceInUseError: auto-scan active for ``name`` (→ 409).
    """
    _check_scan_active(autoscan_manager, name, verb="delete")
    store.delete(name)
    audit_event(
        "audit.workspace.deleted",
        actor=actor,
        target=name,
        outcome="ok",
    )
    _publish_workspaces_changed(event_bus, store)


def reorder_workspaces(
    store: WorkspacesStore,
    body: ReorderRequest,
    *,
    event_bus: Any | None = None,
    actor: str | None = None,
) -> list[dict[str, object]]:
    """Atomically replace workspace ordering.

    Raises:
        ReorderSetMismatchError: duplicates, missing, or unknown names (→ 400).
    """
    try:
        store.reorder(body.names)
    except ValueError as exc:
        raise ReorderSetMismatchError(str(exc)) from exc
    workspaces = store.list_public()
    audit_event(
        "audit.workspace.reordered",
        actor=actor,
        target=None,
        outcome="ok",
        order=list(body.names),
    )
    _publish_workspaces_changed(event_bus, store)
    return workspaces

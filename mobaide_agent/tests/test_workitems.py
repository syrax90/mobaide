"""Tests for mobaide.linear.workitems.ActiveWorkItemsStore."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import PureWindowsPath

import pytest

from mobaide.core import workspaces_store
from mobaide.linear.workitems import (
    ActiveWorkItem,
    ActiveWorkItemsStore,
    now_iso_utc,
)


@pytest.fixture
def windows_paths(monkeypatch):
    """Parse paths with Windows rules, as the agent does on a Windows host.

    On POSIX a backslash is a legal filename character, so the backslash
    normalization only happens on Windows.
    """
    monkeypatch.setattr(workspaces_store, "PurePath", PureWindowsPath)


def _item(id_: str = "CLA-1", uuid: str = "uuid-1") -> ActiveWorkItem:
    return ActiveWorkItem(
        id=id_,
        uuid=uuid,
        slug=id_.lower(),
        worktree_path=f"/tmp/wt/{id_}",
        project_root="/tmp/repo",
        added_at=now_iso_utc(),
    )


class TestStoreReadWrite:

    @pytest.mark.asyncio
    async def test_write_then_read_roundtrip(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        items = [_item("CLA-1", "u1"), _item("CLA-2", "u2")]
        await store.write(items)

        again = await store.read()
        assert [i.id for i in again] == ["CLA-1", "CLA-2"]
        assert [i.uuid for i in again] == ["u1", "u2"]

    @pytest.mark.asyncio
    async def test_write_creates_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "items.json"
        store = ActiveWorkItemsStore(path)
        await store.write([_item()])
        assert path.exists()

    @pytest.mark.asyncio
    async def test_corrupt_json_returns_empty_with_warning(self, tmp_path):
        path = tmp_path / "items.json"
        path.write_text("{not json", encoding="utf-8")
        store = ActiveWorkItemsStore(path)
        assert await store.read() == []

    @pytest.mark.asyncio
    async def test_unexpected_shape_returns_empty(self, tmp_path):
        path = tmp_path / "items.json"
        path.write_text(json.dumps({"oops": "object instead of array"}), encoding="utf-8")
        store = ActiveWorkItemsStore(path)
        assert await store.read() == []

    @pytest.mark.asyncio
    async def test_read_skips_invalid_entries(self, tmp_path):
        path = tmp_path / "items.json"
        good = {
            "id": "CLA-1", "uuid": "u1", "slug": "cla-1",
            "worktree_path": "/wt", "project_root": "/r", "added_at": "now",
        }
        path.write_text(json.dumps([good, {"missing": "fields"}, "string", 42]), encoding="utf-8")
        store = ActiveWorkItemsStore(path)
        items = await store.read()
        assert len(items) == 1
        assert items[0].id == "CLA-1"


class TestStoreAddRemove:
    @pytest.mark.asyncio
    async def test_add_persists_new_item(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        added = await store.add(_item("CLA-1"))
        assert added is True
        assert await store.count() == 1

    @pytest.mark.asyncio
    async def test_add_dedups_by_id(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        await store.add(_item("CLA-1", uuid="u-old"))
        added = await store.add(_item("CLA-1", uuid="u-new"))
        assert added is False
        items = await store.read()
        assert len(items) == 1
        # Existing entry is preserved (no overwrite of uuid).
        assert items[0].uuid == "u-old"

    @pytest.mark.asyncio
    async def test_the_same_github_number_in_two_repositories_is_two_items(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        upstream = replace(_item("#42", uuid="42"), source="github", repo="acme/app")
        fork = replace(upstream, repo="octocat/app", worktree_path="/tmp/wt/fork-42")

        assert await store.add(upstream) is True
        assert await store.add(fork) is True
        assert await store.remove(upstream) is True
        assert [i.repo for i in await store.read()] == ["octocat/app"]

    @pytest.mark.asyncio
    async def test_remove_drops_matching_id(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        await store.add(_item("CLA-1"))
        await store.add(_item("CLA-2"))

        removed = await store.remove(_item("CLA-1"))
        assert removed is True

        remaining = await store.read()
        assert [i.id for i in remaining] == ["CLA-2"]


    @pytest.mark.asyncio
    async def test_remove_at_path_drops_only_items_at_that_worktree(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        here = replace(_item("CLA-1", "u1"), worktree_path=str(tmp_path / "wt" / "cla-1"))
        other = replace(_item("CLA-2", "u2"), worktree_path=str(tmp_path / "wt" / "cla-2"))
        await store.write([here, other])

        assert await store.remove_at_path(str(tmp_path / "wt" / "cla-1") + "/") == 1
        assert [i.id for i in await store.read()] == ["CLA-2"]
        assert await store.remove_at_path(str(tmp_path / "wt" / "gone")) == 0
        assert [i.id for i in await store.read()] == ["CLA-2"]


class TestStoreConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_adds_serialise_under_lock(self, tmp_path):
        store = ActiveWorkItemsStore(tmp_path / "items.json")
        await asyncio.gather(*(
            store.add(_item(f"CLA-{i}", uuid=f"u-{i}")) for i in range(20)
        ))
        items = await store.read()
        assert len({i.id for i in items}) == 20


@pytest.mark.usefixtures("windows_paths")
class TestLegacyWorkspaceRootMigration:
    """Pre-existing ``active_workitems.json`` files may hold OS-native
    backslash paths in ``workspace_root`` / ``worktree_path`` from before the
    workspace-path reconciliation. ``scanner._cleanup_completed_worktrees``
    filters tracked items by string equality against the POSIX-form workspace
    path, so these legacy entries would be silently dropped and the
    associated worktrees would leak until manually cleaned up. The
    ``ActiveWorkItem.from_dict`` loader therefore normalizes both fields to
    POSIX on load so the comparison succeeds.
    """

    def test_from_dict_normalizes_backslash_workspace_root(self):
        item = ActiveWorkItem.from_dict(
            {
                "id": "CLA-99",
                "uuid": "u-99",
                "slug": "cla-99",
                "worktree_path": "D:\\ML\\Projects\\x\\wt\\cla-99",
                "project_root": "D:\\ML\\Projects\\x",
                "added_at": "2026-04-20T12:00:00+00:00",
            },
        )

        assert item.project_root == "D:/ML/Projects/x"
        assert item.worktree_path == "D:/ML/Projects/x/wt/cla-99"

    @pytest.mark.asyncio
    async def test_legacy_file_on_disk_is_normalized_on_read(self, tmp_path):
        """End-to-end: a file that pre-dates the migration produces normalized
        items when ``ActiveWorkItemsStore.read`` loads it."""
        path = tmp_path / "items.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "id": "CLA-42",
                        "uuid": "u-42",
                        "slug": "cla-42",
                        "worktree_path": "D:\\repo\\wt\\cla-42",
                        "project_root": "D:\\repo",
                        "added_at": "2026-04-20T12:00:00+00:00",
                    },
                ],
            ),
            encoding="utf-8",
        )
        store = ActiveWorkItemsStore(path)

        items = await store.read()

        assert len(items) == 1
        assert items[0].project_root == "D:/repo"
        assert items[0].worktree_path == "D:/repo/wt/cla-42"

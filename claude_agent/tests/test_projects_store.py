"""Unit tests for ``claude_mobile.core.projects_store.ProjectsStore``.

Pure in-process coverage — no FastAPI, no HTTP. Router-level coverage lives
in ``test_rest_projects.py``.
"""
from __future__ import annotations

import json
import threading
from typing import cast

import pytest

from claude_mobile.core.projects_config import ProjectConfig
from claude_mobile.core.projects_store import (
    ProjectRecord,
    ProjectsStore,
    ProjectUpdatePatch,
    _seed_from_env,
    normalize_project_path,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PROJECTS", raising=False)


_ = _clean_env  # silence pyright "not accessed" hint for the autouse fixture.


# ---------------------------------------------------------------------------
# Loading / seeding
# ---------------------------------------------------------------------------


class TestLoadMissing:
    def test_missing_file_creates_empty_store_when_env_unset(self, tmp_path):
        path = tmp_path / "projects.json"
        assert not path.exists()

        store = ProjectsStore(str(path))
        store.load()

        assert path.exists()
        assert json.loads(path.read_text()) == []
        assert list(store.list_public()) == []

    def test_missing_file_seeds_from_env_when_set(self, tmp_path, monkeypatch):
        path = tmp_path / "projects.json"
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "Alpha": {
                        "path": "C:/work/a",
                        "linear_api_key": "lin_abc",
                    },
                    "Beta": {"path": "C:/work/b"},
                },
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        on_disk = json.loads(path.read_text())
        assert len(on_disk) == 2
        names = {entry["name"] for entry in on_disk}
        assert names == {"Alpha", "Beta"}
        # Orders assigned by env-iteration order (dict insertion order in 3.7+).
        by_name = {entry["name"]: entry for entry in on_disk}
        assert by_name["Alpha"]["order"] == 0
        assert by_name["Beta"]["order"] == 1
        # Secrets are persisted in the file (not on the wire).
        assert by_name["Alpha"]["linear_api_key"] == "lin_abc"

    def test_seed_empty_result_still_writes_empty_file(
        self, tmp_path, monkeypatch,
    ):
        path = tmp_path / "projects.json"
        # Legacy-form string values are skipped by the seed parser; the
        # result is an empty list — persist it anyway so the env var is
        # not retried on next boot.
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"Legacy": "C:/old", "NoPath": {"x": 1}}),
        )

        store = ProjectsStore(str(path))
        store.load()

        assert path.exists()
        assert json.loads(path.read_text()) == []

    def test_existing_file_takes_precedence_over_env(
        self, tmp_path, monkeypatch,
    ):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "OnDisk",
                        "path": "C:/disk",
                        "order": 0,
                    },
                ],
            ),
        )
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"FromEnv": {"path": "C:/env"}}),
        )

        store = ProjectsStore(str(path))
        store.load()

        public = store.list_public()
        names = [p["name"] for p in public]
        assert names == ["OnDisk"]
        # Env projects are ignored once the file exists.
        assert "FromEnv" not in names

    def test_invalid_json_env_raises_runtime_error(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("PROJECTS", "{not valid json")

        store = ProjectsStore(str(tmp_path / "projects.json"))
        with pytest.raises(RuntimeError, match="invalid JSON"):
            store.load()

    def test_disk_non_list_raises(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(json.dumps({"not": "a list"}))

        store = ProjectsStore(str(path))
        with pytest.raises(RuntimeError, match="not a JSON list"):
            store.load()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_and_persists(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()

        proj = store.create(
            ProjectRecord(name="Alpha", path="C:/a", linear_api_key="lin"),
        )

        assert isinstance(proj, ProjectConfig)
        assert proj.name == "Alpha"
        # Persisted.
        on_disk = json.loads((tmp_path / "projects.json").read_text())
        assert on_disk[0]["name"] == "Alpha"
        assert on_disk[0]["order"] == 0

    def test_duplicate_name_raises_key_error(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(ProjectRecord(name="Alpha", path="C:/a"))

        with pytest.raises(KeyError, match="already exists"):
            store.create(ProjectRecord(name="Alpha", path="C:/different"))


class TestUpdate:
    def _seed(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(
            ProjectRecord(
                name="Alpha",
                path="C:/a",
                linear_api_key="lin_original",
                conda_env="ml",
                default_flags={"verbose": True, "retries": 3},
            ),
        )
        return store

    def test_updates_path(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update(
            "Alpha", ProjectUpdatePatch(path="C:/new"),
        )

        assert updated.path == "C:/new"
        # Other fields preserved.
        assert updated.linear_api_key == "lin_original"

    def test_linear_api_key_none_keeps_existing(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update(
            "Alpha", ProjectUpdatePatch(linear_api_key=None),
        )

        assert updated.linear_api_key == "lin_original"

    def test_linear_api_key_empty_string_clears(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update(
            "Alpha", ProjectUpdatePatch(linear_api_key=""),
        )

        assert updated.linear_api_key is None

    def test_linear_api_key_value_replaces(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update(
            "Alpha", ProjectUpdatePatch(linear_api_key="lin_new"),
        )

        assert updated.linear_api_key == "lin_new"

    def test_rename(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update("Alpha", ProjectUpdatePatch(name="Beta"))

        assert updated.name == "Beta"
        assert store.get("Alpha") is None
        assert store.get("Beta") is not None

    def test_rename_collision_raises(self, tmp_path):
        store = self._seed(tmp_path)
        store.create(ProjectRecord(name="Beta", path="C:/b"))

        with pytest.raises(ValueError, match="target name already exists"):
            store.update("Alpha", ProjectUpdatePatch(name="Beta"))

    def test_default_flags_replaces(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update(
            "Alpha",
            ProjectUpdatePatch(default_flags={"different": "value"}),
        )

        assert updated.default_flags == {"different": "value"}

    def test_default_flags_none_keeps(self, tmp_path):
        store = self._seed(tmp_path)

        updated = store.update("Alpha", ProjectUpdatePatch(default_flags=None))

        assert updated.default_flags == {"verbose": True, "retries": 3}

    def test_unknown_name_raises_key_error(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()

        with pytest.raises(KeyError, match="not found"):
            store.update("Ghost", ProjectUpdatePatch(path="C:/x"))


class TestDelete:
    def test_removes_and_persists(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(ProjectRecord(name="Alpha", path="C:/a"))
        store.create(ProjectRecord(name="Beta", path="C:/b"))

        store.delete("Alpha")

        public = store.list_public()
        names = [p["name"] for p in public]
        assert names == ["Beta"]
        # Order was compacted.
        assert public[0]["order"] == 0

    def test_unknown_name_raises_key_error(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()

        with pytest.raises(KeyError, match="not found"):
            store.delete("Ghost")


class TestReorder:
    def _seed(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(ProjectRecord(name="A", path="C:/a"))
        store.create(ProjectRecord(name="B", path="C:/b"))
        store.create(ProjectRecord(name="C", path="C:/c"))
        return store

    def test_replaces_ordering(self, tmp_path):
        store = self._seed(tmp_path)

        store.reorder(["C", "A", "B"])

        names = [p["name"] for p in store.list_public()]
        assert names == ["C", "A", "B"]

    def test_rejects_duplicates(self, tmp_path):
        store = self._seed(tmp_path)

        with pytest.raises(ValueError, match="duplicate"):
            store.reorder(["A", "B", "A"])

    def test_rejects_missing_names(self, tmp_path):
        store = self._seed(tmp_path)

        with pytest.raises(ValueError, match="missing"):
            store.reorder(["A", "B"])  # C missing

    def test_rejects_unknown_names(self, tmp_path):
        store = self._seed(tmp_path)

        with pytest.raises(ValueError, match="unknown"):
            store.reorder(["A", "B", "C", "Ghost"])


# ---------------------------------------------------------------------------
# Read APIs / data shape
# ---------------------------------------------------------------------------


class TestListPublic:
    def test_no_linear_api_key_in_response(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(
            ProjectRecord(
                name="Alpha",
                path="C:/a",
                linear_api_key="lin_SECRET_DO_NOT_LEAK",
            ),
        )

        entries = store.list_public()
        entry = entries[0]

        assert "linear_api_key" not in entry
        assert entry["has_linear_key"] is True
        for v in entry.values():
            assert "lin_SECRET_DO_NOT_LEAK" not in str(v)

    def test_exposes_is_git_and_path_exists(self, tmp_path):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        (project_root / ".git").mkdir()

        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(ProjectRecord(name="Alpha", path=str(project_root)))
        store.create(
            ProjectRecord(name="Missing", path=str(tmp_path / "nowhere")),
        )

        by_name = {e["name"]: e for e in store.list_public()}
        assert by_name["Alpha"]["is_git"] is True
        assert by_name["Alpha"]["path_exists"] is True
        assert by_name["Missing"]["is_git"] is False
        assert by_name["Missing"]["path_exists"] is False

    def test_default_flags_preserve_typed_values(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(
            ProjectRecord(
                name="Alpha",
                path="C:/a",
                default_flags={
                    "verbose": True,
                    "retries": 3,
                    "ratio": 0.5,
                    "model": "opus",
                },
            ),
        )

        flags = cast(dict, store.list_public()[0]["default_flags"])
        assert flags == {
            "verbose": True,
            "retries": 3,
            "ratio": 0.5,
            "model": "opus",
        }
        # Types not coerced.
        assert isinstance(flags["verbose"], bool)
        assert isinstance(flags["retries"], int)
        assert isinstance(flags["ratio"], float)


class TestGet:
    def test_returns_immutable_project_config_snapshot(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(
            ProjectRecord(
                name="Alpha",
                path="C:/a",
                linear_api_key="lin",
                default_flags={"x": 1},
            ),
        )

        snap = store.get("Alpha")

        assert isinstance(snap, ProjectConfig)
        assert snap.linear_api_key == "lin"
        # Frozen — mutation raises.
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            snap.path = "hacked"  # type: ignore[misc]

    def test_get_missing_returns_none(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        assert store.get("Ghost") is None


class TestListWithLinearKey:
    def test_filters_by_key_presence(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(
            ProjectRecord(name="HasKey", path="C:/a", linear_api_key="lin"),
        )
        store.create(ProjectRecord(name="NoKey", path="C:/b"))

        assert store.list_with_linear_key() == ["HasKey"]


# ---------------------------------------------------------------------------
# Durability / concurrency
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_tmp_file_left_behind_after_successful_save(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()

        store.create(ProjectRecord(name="Alpha", path="C:/a"))

        tmp = path.with_suffix(path.suffix + ".tmp")
        assert not tmp.exists()
        assert path.exists()


class TestConcurrentMutations:
    def test_parallel_creates_are_serialised_and_persist_all(self, tmp_path):
        """Stress the threading.Lock with N concurrent creates.

        Ensures no torn JSON, no lost writes, and no duplicate keys — even
        under contention. The test deliberately uses a threading pool
        because the store is lock-based (not asyncio).
        """
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()

        def _add(i: int) -> None:
            try:
                store.create(
                    ProjectRecord(name=f"P{i}", path=f"C:/p{i}"),
                )
            except KeyError:
                # Treat duplicates as a failure — they should not happen
                # under a unique-index producer.
                raise

        threads = [threading.Thread(target=_add, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store) == 20
        names = {e["name"] for e in store.list_public()}
        assert names == {f"P{i}" for i in range(20)}
        # Orders remain dense 0..19.
        orders = sorted(cast(int, e["order"]) for e in store.list_public())
        assert orders == list(range(20))


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


class TestSeedFromEnv:
    def test_returns_empty_list_when_env_unset(self):
        assert _seed_from_env() == []

    def test_returns_empty_list_when_env_blank(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", "")
        assert _seed_from_env() == []

    def test_skips_legacy_string_values(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"Legacy": "C:/old"}),
        )
        assert _seed_from_env() == []

    def test_skips_entries_missing_path(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"Bad": {"linear_api_key": "x"}}),
        )
        assert _seed_from_env() == []

    def test_preserves_default_flag_types(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "P": {
                        "path": "C:/p",
                        "default_flags": {"verbose": True, "n": 3},
                    },
                },
            ),
        )

        records = _seed_from_env()

        assert len(records) == 1
        assert records[0].default_flags == {"verbose": True, "n": 3}

    def test_normalizes_backslash_paths_from_env(self, monkeypatch):
        """Legacy users that set ``PROJECTS`` with Windows-native backslashes
        still get POSIX paths seeded into the store on first boot."""
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"Win": {"path": "D:\\ML\\Projects\\x"}}),
        )

        records = _seed_from_env()

        assert len(records) == 1
        assert records[0].path == "D:/ML/Projects/x"

    def test_seed_skips_entry_whose_path_is_whitespace_only(self, monkeypatch):
        """A path consisting only of whitespace must not silently become ``.``;
        the normalizer rejects it and the seed loop skips the entry."""
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"Blank": {"path": "   "}}),
        )

        assert _seed_from_env() == []


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


class TestNormalizeProjectPath:
    def test_windows_backslashes_become_forward_slashes(self):
        assert (
            normalize_project_path("D:\\ML\\Projects\\claude_mobile")
            == "D:/ML/Projects/claude_mobile"
        )

    def test_posix_path_unchanged(self):
        assert normalize_project_path("/home/user/repo") == "/home/user/repo"

    def test_mixed_separators_collapse_to_posix(self):
        assert (
            normalize_project_path("D:/ML\\Projects/claude_mobile")
            == "D:/ML/Projects/claude_mobile"
        )

    def test_strips_surrounding_whitespace(self):
        assert normalize_project_path("  C:/a  ") == "C:/a"

    def test_collapses_doubled_separators(self):
        # ``D:\\\\ML`` in JSON source decodes to ``D:\\ML`` in memory (two
        # backslashes per separator). ``PurePath`` normalizes these to a
        # single separator on its native OS. Assert the idempotent POSIX form.
        normalized = normalize_project_path("D:\\\\ML\\\\x")
        # Regardless of the host OS, the POSIX form must not contain ``\``.
        assert "\\" not in normalized

    def test_is_idempotent(self):
        once = normalize_project_path("D:\\ML\\x")
        twice = normalize_project_path(once)
        assert once == twice

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            normalize_project_path("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            normalize_project_path("   ")


# ---------------------------------------------------------------------------
# Legacy on-disk row migration
# ---------------------------------------------------------------------------


class TestLegacyBackslashRowsNormalizedOnLoad:
    def test_existing_file_with_backslash_paths_is_normalized_on_load(
        self, tmp_path,
    ):
        """Windows users with a pre-existing ``projects.json`` whose rows
        store OS-native backslashes see them migrated to POSIX the first
        time the store is loaded — without any explicit migration step."""
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "Legacy",
                        "path": "D:\\ML\\Projects\\claude_mobile",
                        "order": 0,
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        public = store.list_public()
        assert len(public) == 1
        assert public[0]["path"] == "D:/ML/Projects/claude_mobile"

    def test_malformed_row_with_empty_path_is_dropped(self, tmp_path):
        """A row whose path is an empty string cannot be normalized; the
        loader must skip it rather than propagate the ValueError."""
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {"name": "OK", "path": "C:/ok", "order": 0},
                    {"name": "Bad", "path": "", "order": 1},
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        names = [p["name"] for p in store.list_public()]
        assert names == ["OK"]

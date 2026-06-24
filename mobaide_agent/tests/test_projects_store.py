"""Unit tests for ``mobaide.core.projects_store.ProjectsStore``.

Pure in-process coverage — no FastAPI, no HTTP. Router-level coverage lives
in ``test_rest_projects.py``.
"""
from __future__ import annotations

import json
import threading
from typing import cast

import pytest

from mobaide.core.projects_config import AutoScanFilter, ProjectConfig
from mobaide.core.projects_store import (
    ProjectRecord,
    ProjectsStore,
    ProjectUpdatePatch,
    _seed_from_env,
    normalize_project_path,
    safe_path_exists,
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


class TestLoadSeedJsonExplicit:
    """``ProjectsStore.load(seed_json=...)`` accepts an explicit JSON
    string in addition to (or instead of) ``os.environ['PROJECTS']``.

    This decouples the store from ``os.environ`` so the canonical caller
    (``mobaide.agent.app.create_app``) can pass ``config.projects`` —
    the pydantic-settings-resolved value — directly. Without this path
    `.env`-defined PROJECTS values are silently missed on Windows where
    the supervisor does not pre-load the env file.
    """

    def test_seeds_from_explicit_arg_when_environ_unset(self, tmp_path):
        # PROJECTS env var is unset (autouse fixture _clean_env). The
        # explicit seed_json arg must populate the store anyway.
        path = tmp_path / "projects.json"
        seed = json.dumps(
            {
                "Alpha": {"path": "C:/work/a", "linear_api_key": "lin_abc"},
                "Beta": {"path": "C:/work/b"},
            },
        )

        store = ProjectsStore(str(path))
        store.load(seed_json=seed)

        on_disk = json.loads(path.read_text())
        names = {entry["name"] for entry in on_disk}
        assert names == {"Alpha", "Beta"}
        # Secrets are persisted (file content), not exposed publicly.
        by_name = {entry["name"]: entry for entry in on_disk}
        assert by_name["Alpha"]["linear_api_key"] == "lin_abc"

    def test_explicit_seed_overrides_environ_projects(
        self, tmp_path, monkeypatch,
    ):
        # When both seed_json and PROJECTS env are set, the explicit arg
        # wins — ``_seed_from_env`` only consults ``os.getenv`` when the
        # passed arg is None.
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"FromEnv": {"path": "C:/from-env"}}),
        )
        path = tmp_path / "projects.json"
        explicit = json.dumps({"FromArg": {"path": "C:/from-arg"}})

        store = ProjectsStore(str(path))
        store.load(seed_json=explicit)

        public = store.list_public()
        names = [p["name"] for p in public]
        assert names == ["FromArg"]
        assert "FromEnv" not in names

    def test_explicit_none_falls_back_to_environ(self, tmp_path, monkeypatch):
        # Back-compat path for callers that haven't migrated yet:
        # passing ``seed_json=None`` (or omitting it) still consults the
        # PROJECTS env var.
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"FromEnv": {"path": "C:/from-env"}}),
        )
        path = tmp_path / "projects.json"

        store = ProjectsStore(str(path))
        store.load(seed_json=None)

        names = {p["name"] for p in store.list_public()}
        assert names == {"FromEnv"}

    def test_empty_explicit_seed_creates_empty_store(self, tmp_path):
        # A non-None but empty seed_json (e.g. ``""`` or whitespace)
        # produces an empty store and persists an empty file.
        path = tmp_path / "projects.json"

        store = ProjectsStore(str(path))
        store.load(seed_json="")

        assert path.exists()
        assert json.loads(path.read_text()) == []
        assert store.list_public() == []

    def test_invalid_explicit_seed_raises(self, tmp_path):
        # Malformed JSON in the explicit seed surfaces a RuntimeError —
        # the store does not silently swallow misconfiguration.
        path = tmp_path / "projects.json"

        store = ProjectsStore(str(path))
        with pytest.raises(RuntimeError, match="invalid JSON"):
            store.load(seed_json="{not valid json")

    def test_explicit_seed_ignored_when_file_exists(self, tmp_path):
        # Once the projects.json file exists, the seed is ignored — the
        # file is the source of truth from second boot onward.
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [{"name": "OnDisk", "path": "C:/disk", "order": 0}],
            ),
        )
        seed = json.dumps({"FromArg": {"path": "C:/arg"}})

        store = ProjectsStore(str(path))
        store.load(seed_json=seed)

        names = [p["name"] for p in store.list_public()]
        assert names == ["OnDisk"]
        assert "FromArg" not in names


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

    def test_list_public_tolerates_unreadable_project_path(
        self, tmp_path, monkeypatch,
    ):
        """Regression for the ``GET /api/projects`` 500 reported on a path
        the agent process can't ``stat()`` (e.g. another user's home dir
        with mode 0700).

        ``_record_to_public`` previously called ``Path.exists()`` directly,
        and a single ``PermissionError`` from one record's path probe
        poisoned the whole list response. The fix routes the probes through
        ``safe_path_exists``; this test pins that contract by injecting a
        ``PermissionError`` for the unreadable record and asserting the
        rest of the response stays intact.
        """
        readable_root = tmp_path / "readable"
        readable_root.mkdir()
        (readable_root / ".git").mkdir()
        unreadable_root = tmp_path / "unreadable"

        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(ProjectRecord(name="Readable", path=str(readable_root)))
        store.create(
            ProjectRecord(name="Locked", path=str(unreadable_root)),
        )

        # Simulate an EACCES at the OS layer: any ``Path.exists()`` call on
        # the unreadable root (or its ``.git`` child) raises ``PermissionError``.
        # Other paths must still resolve normally so the readable record's
        # ``is_git`` / ``path_exists`` flags remain truthful.
        from pathlib import Path as _Path

        real_exists = _Path.exists
        unreadable_str = str(unreadable_root)

        def faking_exists(self):  # type: ignore[no-redef]
            if str(self).startswith(unreadable_str):
                raise PermissionError(13, "Permission denied", str(self))
            return real_exists(self)

        monkeypatch.setattr(_Path, "exists", faking_exists)

        public = store.list_public()
        by_name = {e["name"]: e for e in public}

        assert set(by_name) == {"Readable", "Locked"}
        # Locked project: probes returned False, no exception bubbled.
        assert by_name["Locked"]["is_git"] is False
        assert by_name["Locked"]["path_exists"] is False
        # Readable project untouched.
        assert by_name["Readable"]["is_git"] is True
        assert by_name["Readable"]["path_exists"] is True


class TestSafePathExists:
    """Direct coverage for the ``safe_path_exists`` helper.

    This is the load-bearing primitive behind every defensive ``.git`` /
    project-root probe in the agent: ``projects_store._record_to_public``,
    ``agent/git/router._resolve_git_project``, ``core/claude_launcher``'s
    worktree fallback, and ``relay/client``'s git-relay path. The helper
    must (a) report the truth on a normal path, and (b) silently return
    ``False`` on any ``OSError`` subclass — matching the comment in
    ``projects_store.py`` that calls out ``PermissionError``, broken
    symlinks, and bouncing NFS mounts.
    """

    def test_returns_true_for_existing_path(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        assert safe_path_exists(target) is True

    def test_returns_false_for_missing_path(self, tmp_path):
        # ``Path.exists`` already returns False for FileNotFoundError-class
        # outcomes, so this is just the well-behaved baseline.
        assert safe_path_exists(tmp_path / "does_not_exist") is False

    def test_returns_false_when_underlying_exists_raises_permission_error(
        self, tmp_path, monkeypatch,
    ):
        from pathlib import Path as _Path

        target = tmp_path / "locked"

        def boom(self):  # type: ignore[no-redef]
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(_Path, "exists", boom)

        # Must NOT propagate — caller is on a hot path that may not be
        # entitled to stat() the target. EACCES degenerates to "no".
        assert safe_path_exists(target) is False

    def test_returns_false_for_other_oserror_subclasses(
        self, tmp_path, monkeypatch,
    ):
        """Broken symlinks raise ``OSError`` (errno EIO/ELOOP) in some
        kernels; an NFS hiccup surfaces as ``OSError`` too. The docstring
        explicitly enumerates these as "treat as False" — pin that here so
        a future tightening of the except clause to ``PermissionError``
        only doesn't silently break the worktree path.
        """
        from pathlib import Path as _Path

        target = tmp_path / "symlink"

        def boom(self):  # type: ignore[no-redef]
            raise OSError(40, "Symbolic link loop", str(self))

        monkeypatch.setattr(_Path, "exists", boom)

        assert safe_path_exists(target) is False

    def test_does_not_swallow_non_oserror_exceptions(
        self, tmp_path, monkeypatch,
    ):
        """A ``ValueError`` (or any non-OSError) must escape — those are
        bugs, not environment-of-the-day reality, and silencing them
        would hide real defects.
        """
        from pathlib import Path as _Path

        target = tmp_path / "buggy"

        def boom(self):  # type: ignore[no-redef]
            raise ValueError("not an OSError, must propagate")

        monkeypatch.setattr(_Path, "exists", boom)

        with pytest.raises(ValueError, match="must propagate"):
            safe_path_exists(target)


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
            normalize_project_path("D:\\ML\\Projects\\mobaide")
            == "D:/ML/Projects/mobaide"
        )

    def test_posix_path_unchanged(self):
        assert normalize_project_path("/home/user/repo") == "/home/user/repo"

    def test_mixed_separators_collapse_to_posix(self):
        assert (
            normalize_project_path("D:/ML\\Projects/mobaide")
            == "D:/ML/Projects/mobaide"
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
                        "path": "D:\\ML\\Projects\\mobaide",
                        "order": 0,
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        public = store.list_public()
        assert len(public) == 1
        assert public[0]["path"] == "D:/ML/Projects/mobaide"

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


def _filter(**overrides) -> AutoScanFilter:
    base = {
        "state_names": ["Proposals"],
        "label_names": ["AutoPipeline"],
        "active_state": "In Progress",
        "done_state": "Done",
        "removal_label": "AutoPipeline",
    }
    base.update(overrides)
    return AutoScanFilter(**base)


class TestAutoScanFilterDefaultsForLegacyRecords:
    """Legacy records lack the ``auto_scan_filter`` field; loading must
    coerce them to the historical-equivalent default so users do not see
    behavior change after upgrading the agent."""

    def test_legacy_record_gets_default_filter(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "linear_api_key": "lin",
                        "order": 0,
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter == AutoScanFilter()

    def test_partial_filter_field_falls_back_per_field(self, tmp_path):
        # A record that has *some* filter fields but is missing others should
        # populate the absent fields with the per-field default rather than
        # crashing or wholesale-defaulting.
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_filter": {
                            "state_names": ["Backlog"],
                            "active_state": "Doing",
                        },
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter.state_names == ["Backlog"]
        assert snap.auto_scan_filter.label_names == ["AutoPipeline"]
        assert snap.auto_scan_filter.active_state == "Doing"
        assert snap.auto_scan_filter.done_state == "Done"
        assert snap.auto_scan_filter.removal_label == "AutoPipeline"

    def test_malformed_filter_is_silently_replaced_with_default(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        # Wrong type — should not crash, just default.
                        "auto_scan_filter": "not-a-dict",
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter == AutoScanFilter()


class TestAutoScanFilterRoundTrip:
    """A non-default filter persisted via the store must round-trip through
    JSON without losing fields or changing field order."""

    def test_round_trip_preserves_all_fields(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()

        filt = _filter(
            state_names=["Proposals", "Backlog"],
            label_names=["AutoPipeline", "AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )
        store.create(
            ProjectRecord(name="alpha", path="C:/alpha", auto_scan_filter=filt),
        )

        # Reload from disk and verify the snapshot matches.
        store2 = ProjectsStore(str(path))
        store2.load()
        snap = store2.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter == filt

    def test_filter_appears_in_list_public(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        filt = _filter(state_names=["Proposals", "Triage"])
        store.create(
            ProjectRecord(name="alpha", path="C:/alpha", auto_scan_filter=filt),
        )

        rows = store.list_public()
        assert len(rows) == 1
        on_wire = cast("dict", rows[0]).get("auto_scan_filter")
        assert on_wire is not None
        assert isinstance(on_wire, dict)
        assert on_wire["state_names"] == ["Proposals", "Triage"]
        assert on_wire["active_state"] == "In Progress"


class TestUpdateAppliesFilterPatch:
    def test_update_replaces_filter_when_provided(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha",
                auto_scan_filter=_filter(),
            ),
        )

        new_filter = _filter(
            state_names=["Backlog"],
            label_names=["AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )
        snap = store.update(
            "alpha",
            ProjectUpdatePatch(auto_scan_filter=new_filter),
        )
        assert snap.auto_scan_filter == new_filter

    def test_update_keeps_existing_filter_when_patch_is_none(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        starting = _filter(state_names=["Proposals", "Backlog"])
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha", auto_scan_filter=starting,
            ),
        )

        # A patch with auto_scan_filter=None must not overwrite.
        snap = store.update(
            "alpha",
            ProjectUpdatePatch(conda_env="ml"),
        )
        assert snap.auto_scan_filter == starting


class TestFilterFromDictMalformedListElements:
    """``_filter_from_dict`` is the load-time tolerance layer for the
    persisted filter blob. It is exercised indirectly via ``store.load()``,
    but the per-element type-filtering branches are easier to pin with a
    direct fixture: a list-typed ``state_names`` / ``label_names`` whose
    individual entries are not strings (e.g. an int slipped in from a
    hand-edited file) must drop the bogus elements rather than crashing or
    leaking through to the GraphQL filter."""

    def test_non_string_state_names_elements_are_filtered_out(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_filter": {
                            "state_names": ["Proposals", 42, None, "Backlog"],
                            "label_names": ["AutoPipeline"],
                            "active_state": "In Progress",
                            "done_state": "Done",
                            "removal_label": "AutoPipeline",
                        },
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter.state_names == ["Proposals", "Backlog"]

    def test_non_string_label_names_elements_are_filtered_out(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_filter": {
                            "state_names": ["Proposals"],
                            "label_names": ["AutoPipeline", 7, "AI-ready"],
                            "active_state": "In Progress",
                            "done_state": "Done",
                            "removal_label": "AutoPipeline",
                        },
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter.label_names == ["AutoPipeline", "AI-ready"]

    def test_empty_string_active_state_falls_back_to_default(self, tmp_path):
        # Empty-string single-value fields are coerced to the historical
        # default; an empty ``active_state`` would otherwise produce a
        # filter that never matches any workflow state at scan time.
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_filter": {
                            "state_names": ["Proposals"],
                            "label_names": ["AutoPipeline"],
                            "active_state": "",
                            "done_state": "",
                            "removal_label": "",
                        },
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter.active_state == "In Progress"
        assert snap.auto_scan_filter.done_state == "Done"
        assert snap.auto_scan_filter.removal_label == "AutoPipeline"

    def test_empty_string_elements_in_list_are_dropped(self, tmp_path):
        # Mirrors the AutoScanFilterModel field validator. The store loader
        # must drop empty / whitespace-only elements so a hand-edited
        # projects.json cannot land a record that the wire model
        # (ProjectReadModel) would reject on the next GET /api/projects.
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_filter": {
                            "state_names": ["Proposals", "", "Backlog"],
                            "label_names": ["AutoPipeline", "   ", "AI-ready"],
                            "active_state": "In Progress",
                            "done_state": "Done",
                            "removal_label": "AutoPipeline",
                        },
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter.state_names == ["Proposals", "Backlog"]
        assert snap.auto_scan_filter.label_names == ["AutoPipeline", "AI-ready"]

    def test_whitespace_only_singleton_falls_back_to_default(self, tmp_path):
        # ``"   "`` passes the truthiness check on a string but would fail
        # the wire model's _reject_blank_string validator. Loader must
        # treat it identically to empty string.
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_filter": {
                            "state_names": ["Proposals"],
                            "label_names": ["AutoPipeline"],
                            "active_state": "   ",
                            "done_state": "\t\n",
                            "removal_label": "    ",
                        },
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_filter.active_state == "In Progress"
        assert snap.auto_scan_filter.done_state == "Done"
        assert snap.auto_scan_filter.removal_label == "AutoPipeline"


class TestSeedFromEnvParsesAutoScanFilter:
    """The ``PROJECTS`` env var path must also pick up a per-project
    ``auto_scan_filter`` block. Without this, users on first boot would
    silently get the historical defaults regardless of what they put in
    the env — a silent regression for env-driven deployments."""

    def test_seed_carries_filter_from_env(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "P": {
                        "path": "C:/p",
                        "auto_scan_filter": {
                            "state_names": ["Backlog"],
                            "label_names": ["AI-ready"],
                            "active_state": "Doing",
                            "done_state": "Released",
                            "removal_label": "AI-ready",
                        },
                    },
                },
            ),
        )

        records = _seed_from_env()

        assert len(records) == 1
        assert records[0].auto_scan_filter == AutoScanFilter(
            state_names=["Backlog"],
            label_names=["AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )

    def test_seed_uses_default_filter_when_field_absent(self, monkeypatch):
        # An env-defined project with no ``auto_scan_filter`` block must
        # land on the per-field defaults, identical to the legacy-record
        # path on disk.
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"P": {"path": "C:/p"}}),
        )

        records = _seed_from_env()

        assert len(records) == 1
        assert records[0].auto_scan_filter == AutoScanFilter()


# ---------------------------------------------------------------------------
# Per-project auto_scan_prompt (preserve-last-applied)
# ---------------------------------------------------------------------------


class TestAutoScanPromptRoundTrip:
    """The per-project Auto Scan prompt mirrors ``conda_env``: ``None`` means
    "use the engine-wide default", a non-``None`` string is persisted and
    must round-trip through save/load and reach clients via ``list_public``."""

    def test_round_trip_preserves_prompt(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha",
                auto_scan_prompt="Resolve the issue carefully.",
            ),
        )

        # Reload from disk and verify the snapshot carries the prompt.
        store2 = ProjectsStore(str(path))
        store2.load()
        snap = store2.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt == "Resolve the issue carefully."

    def test_record_to_dict_omits_prompt_when_none(self, tmp_path):
        # Like ``conda_env``: the key is written only when non-None so a
        # legacy/default project stays a minimal row on disk.
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(ProjectRecord(name="alpha", path="C:/alpha"))

        on_disk = json.loads(path.read_text())
        assert len(on_disk) == 1
        assert "auto_scan_prompt" not in on_disk[0]

    def test_record_to_dict_writes_prompt_when_present(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha", auto_scan_prompt="do the thing",
            ),
        )

        on_disk = json.loads(path.read_text())
        assert on_disk[0]["auto_scan_prompt"] == "do the thing"

    def test_prompt_appears_in_list_public(self, tmp_path):
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha", auto_scan_prompt="seed prompt",
            ),
        )

        rows = store.list_public()
        assert len(rows) == 1
        assert cast("dict", rows[0])["auto_scan_prompt"] == "seed prompt"

    def test_list_public_prompt_is_none_when_unset(self, tmp_path):
        # The key is always present in the public dict (clients rely on it),
        # but defaults to None when the project never customized the prompt.
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(ProjectRecord(name="alpha", path="C:/alpha"))

        row = cast("dict", store.list_public()[0])
        assert "auto_scan_prompt" in row
        assert row["auto_scan_prompt"] is None


class TestAutoScanPromptLegacyRecords:
    """Legacy on-disk rows predate the field; they must load as ``None`` so
    the engine-wide default applies — no behavior change after upgrade."""

    def test_legacy_record_without_prompt_loads_as_none(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "linear_api_key": "lin",
                        "order": 0,
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt is None

    def test_blank_prompt_on_disk_coerced_to_none(self, tmp_path):
        # A hand-edited / whitespace-only prompt must not become a saved
        # value that suppresses the default; the loader normalizes it to None.
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_prompt": "   ",
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt is None

    def test_non_string_prompt_on_disk_coerced_to_none(self, tmp_path):
        path = tmp_path / "projects.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "alpha",
                        "path": "C:/alpha",
                        "order": 0,
                        "auto_scan_prompt": 42,
                    },
                ],
            ),
        )

        store = ProjectsStore(str(path))
        store.load()

        snap = store.get("alpha")
        assert snap is not None
        assert snap.auto_scan_prompt is None


class TestUpdateAppliesPromptPatch:
    """``ProjectUpdatePatch.auto_scan_prompt`` three-state, identical to
    ``linear_api_key`` / ``conda_env``: ``None`` keeps, ``""`` clears to
    ``None`` (→ default), any other string replaces."""

    def _seed(self, tmp_path):
        store = ProjectsStore(str(tmp_path / "projects.json"))
        store.load()
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha",
                auto_scan_prompt="original prompt",
            ),
        )
        return store

    def test_none_keeps_existing_prompt(self, tmp_path):
        store = self._seed(tmp_path)

        # A patch touching only another field must not disturb the prompt.
        snap = store.update("alpha", ProjectUpdatePatch(conda_env="ml"))

        assert snap.auto_scan_prompt == "original prompt"

    def test_empty_string_clears_prompt_to_none(self, tmp_path):
        store = self._seed(tmp_path)

        snap = store.update("alpha", ProjectUpdatePatch(auto_scan_prompt=""))

        assert snap.auto_scan_prompt is None

    def test_value_replaces_prompt(self, tmp_path):
        store = self._seed(tmp_path)

        snap = store.update(
            "alpha", ProjectUpdatePatch(auto_scan_prompt="new prompt"),
        )

        assert snap.auto_scan_prompt == "new prompt"

    def test_cleared_prompt_persists_as_omitted_on_disk(self, tmp_path):
        # After clearing, the next save must not re-introduce the key — a
        # cleared prompt is indistinguishable from never-set on reload.
        path = tmp_path / "projects.json"
        store = ProjectsStore(str(path))
        store.load()
        store.create(
            ProjectRecord(
                name="alpha", path="C:/alpha", auto_scan_prompt="x",
            ),
        )

        store.update("alpha", ProjectUpdatePatch(auto_scan_prompt=""))

        on_disk = json.loads(path.read_text())
        assert "auto_scan_prompt" not in on_disk[0]


class TestSeedFromEnvParsesAutoScanPrompt:
    """The ``PROJECTS`` env seed must also pick up ``auto_scan_prompt`` so an
    env-driven first boot has parity with the persisted/REST paths."""

    def test_seed_carries_prompt_from_env(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {"P": {"path": "C:/p", "auto_scan_prompt": "env prompt"}},
            ),
        )

        records = _seed_from_env()

        assert len(records) == 1
        assert records[0].auto_scan_prompt == "env prompt"

    def test_seed_prompt_none_when_field_absent(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"P": {"path": "C:/p"}}),
        )

        records = _seed_from_env()

        assert len(records) == 1
        assert records[0].auto_scan_prompt is None

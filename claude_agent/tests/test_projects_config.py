"""Tests for ProjectsConfig and ProjectConfig — pure data structure."""
from __future__ import annotations

import json
from typing import cast

import pytest

from claude_mobile.core.projects_config import ProjectConfig, ProjectsConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PROJECTS", raising=False)


# Dummy reference: the autouse fixture is invoked by pytest via name-based
# lookup, so Pyright's "not accessed" hint is a false positive. Bind it to
# a module-level name to silence the hint without disabling diagnostics.
_ = _clean_env


class TestProjectsConfigFromEnv:
    def test_returns_none_when_projects_env_unset(self):
        assert ProjectsConfig.from_env() is None

    def test_returns_none_when_projects_env_empty_string(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", "")

        assert ProjectsConfig.from_env() is None

    def test_invalid_json_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("PROJECTS", "{not valid json")

        with pytest.raises(RuntimeError, match="invalid JSON"):
            ProjectsConfig.from_env()

    def test_happy_path_parses_valid_json(self, monkeypatch):
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps(
                {
                    "MyProj": {
                        "path": "C:/workspace/my",
                        "linear_api_key": "lin_api_xxx",
                    }
                }
            ),
        )

        cfg = ProjectsConfig.from_env()

        assert cfg is not None
        assert isinstance(cfg, ProjectsConfig)
        proj = cfg.projects["MyProj"]
        assert isinstance(proj, ProjectConfig)
        assert proj.name == "MyProj"
        assert proj.path == "C:/workspace/my"
        assert proj.linear_api_key == "lin_api_xxx"


class TestProjectsConfigFromDictSkipping:
    def test_legacy_string_values_skipped(self):
        result = ProjectsConfig.from_dict({"Legacy": "C:/path/to/legacy"})

        assert result is None

    def test_non_string_non_dict_values_skipped(self):
        result = ProjectsConfig.from_dict({"Weird": 42})

        assert result is None

    def test_list_value_skipped(self):
        result = ProjectsConfig.from_dict({"AsList": [1, 2, 3]})

        assert result is None

    def test_dict_without_path_key_skipped(self):
        result = ProjectsConfig.from_dict(
            {"Bad": {"linear_api_key": "lin_api_xxx"}}
        )

        assert result is None

    def test_dict_with_non_string_path_skipped(self):
        result = ProjectsConfig.from_dict({"Bad": {"path": 12345}})

        assert result is None

    def test_mixed_entries_filters_invalid_keeps_valid(self):
        result = ProjectsConfig.from_dict(
            {
                "Legacy": "C:/old",
                "Bad": {"linear_api_key": "lin_api"},
                "Good": {
                    "path": "C:/workspace/good",
                    "linear_api_key": "lin_api_good",
                },
            }
        )

        assert result is not None
        assert set(result.projects.keys()) == {"Good"}

    def test_returns_none_when_all_entries_filtered_out(self):
        result = ProjectsConfig.from_dict(
            {
                "A": "legacy",
                "B": {"linear_api_key": "no-path-here"},
                "C": 99,
            }
        )

        assert result is None


class TestProjectsConfigFromDictFieldNormalization:
    def test_non_string_linear_api_key_becomes_none(self):
        result = ProjectsConfig.from_dict(
            {"P": {"path": "C:/p", "linear_api_key": 42}}
        )

        assert result is not None
        assert result.projects["P"].linear_api_key is None

    def test_missing_linear_api_key_is_none(self):
        result = ProjectsConfig.from_dict({"P": {"path": "C:/p"}})

        assert result is not None
        assert result.projects["P"].linear_api_key is None

    def test_non_string_conda_env_becomes_none(self):
        result = ProjectsConfig.from_dict(
            {"P": {"path": "C:/p", "conda_env": ["not", "a", "string"]}}
        )

        assert result is not None
        assert result.projects["P"].conda_env is None

    def test_missing_conda_env_is_none(self):
        result = ProjectsConfig.from_dict({"P": {"path": "C:/p"}})

        assert result is not None
        assert result.projects["P"].conda_env is None

    def test_non_dict_default_flags_becomes_empty_dict(self):
        result = ProjectsConfig.from_dict(
            {"P": {"path": "C:/p", "default_flags": "not-a-dict"}}
        )

        assert result is not None
        assert result.projects["P"].default_flags == {}

    def test_missing_default_flags_is_empty_dict(self):
        result = ProjectsConfig.from_dict({"P": {"path": "C:/p"}})

        assert result is not None
        assert result.projects["P"].default_flags == {}

    def test_valid_default_flags_preserved(self):
        result = ProjectsConfig.from_dict(
            {
                "P": {
                    "path": "C:/p",
                    "default_flags": {"verbose": True, "model": "opus"},
                }
            }
        )

        assert result is not None
        assert result.projects["P"].default_flags == {
            "verbose": True,
            "model": "opus",
        }

    def test_valid_conda_env_preserved(self):
        result = ProjectsConfig.from_dict(
            {"P": {"path": "C:/p", "conda_env": "ml"}}
        )

        assert result is not None
        assert result.projects["P"].conda_env == "ml"


class TestProjectsConfigGet:
    def test_get_returns_project_when_present(self):
        cfg = ProjectsConfig.from_dict(
            {"A": {"path": "C:/a"}, "B": {"path": "C:/b"}}
        )

        assert cfg is not None
        proj = cfg.get("A")
        assert proj is not None
        assert proj.name == "A"
        assert proj.path == "C:/a"

    def test_get_returns_none_when_absent(self):
        cfg = ProjectsConfig.from_dict({"A": {"path": "C:/a"}})

        assert cfg is not None
        assert cfg.get("Missing") is None


class TestProjectsConfigContains:
    def test_contains_true_for_known_name(self):
        cfg = ProjectsConfig.from_dict({"A": {"path": "C:/a"}})

        assert cfg is not None
        assert "A" in cfg

    def test_contains_false_for_unknown_name(self):
        cfg = ProjectsConfig.from_dict({"A": {"path": "C:/a"}})

        assert cfg is not None
        assert "B" not in cfg

    def test_contains_false_for_non_string_input(self):
        cfg = ProjectsConfig.from_dict({"A": {"path": "C:/a"}})

        assert cfg is not None
        # Non-string keys should return False, not raise.
        assert (42 in cfg) is False
        assert (None in cfg) is False
        assert ((1, 2) in cfg) is False


class TestProjectsConfigListPublic:
    def test_does_not_leak_linear_api_key(self, tmp_path):
        cfg = ProjectsConfig.from_dict(
            {
                "Secret": {
                    "path": str(tmp_path),
                    "linear_api_key": "lin_api_SHOULD_NOT_LEAK",
                }
            }
        )

        assert cfg is not None
        public = cfg.list_public()

        assert len(public) == 1
        entry = public[0]
        assert "linear_api_key" not in entry
        # Also spot-check: the secret string must not appear in any value.
        for v in entry.values():
            assert "lin_api_SHOULD_NOT_LEAK" not in str(v)

    def test_has_linear_key_true_when_key_present(self, tmp_path):
        cfg = ProjectsConfig.from_dict(
            {
                "P": {
                    "path": str(tmp_path),
                    "linear_api_key": "lin_api_xxx",
                }
            }
        )

        assert cfg is not None
        entry = cfg.list_public()[0]
        assert entry["has_linear_key"] is True

    def test_has_linear_key_false_when_key_absent(self, tmp_path):
        cfg = ProjectsConfig.from_dict({"P": {"path": str(tmp_path)}})

        assert cfg is not None
        entry = cfg.list_public()[0]
        assert entry["has_linear_key"] is False

    def test_is_git_true_when_dot_git_directory_exists(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        cfg = ProjectsConfig.from_dict({"P": {"path": str(tmp_path)}})

        assert cfg is not None
        entry = cfg.list_public()[0]
        assert entry["is_git"] is True

    def test_is_git_false_when_dot_git_missing(self, tmp_path):
        bare = tmp_path / "bare"
        bare.mkdir()
        cfg = ProjectsConfig.from_dict({"P": {"path": str(bare)}})

        assert cfg is not None
        entry = cfg.list_public()[0]
        assert entry["is_git"] is False

    def test_name_and_path_included(self, tmp_path):
        cfg = ProjectsConfig.from_dict({"MyName": {"path": str(tmp_path)}})

        assert cfg is not None
        entry = cfg.list_public()[0]
        assert entry["name"] == "MyName"
        assert entry["path"] == str(tmp_path)

    def test_default_flags_is_a_copy(self, tmp_path):
        cfg = ProjectsConfig.from_dict(
            {
                "P": {
                    "path": str(tmp_path),
                    "default_flags": {"verbose": True},
                }
            }
        )

        assert cfg is not None
        entry = cfg.list_public()[0]
        flags = cast(dict, entry["default_flags"])
        assert flags == {"verbose": True}

        # Mutate the returned dict: source must not be affected.
        flags["mutated"] = "yes"
        proj = cfg.get("P")
        assert proj is not None
        assert "mutated" not in proj.default_flags

    def test_multiple_projects_returns_one_entry_each(self, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        b = tmp_path / "b"
        b.mkdir()
        cfg = ProjectsConfig.from_dict(
            {
                "A": {"path": str(a)},
                "B": {
                    "path": str(b),
                    "linear_api_key": "lin_api_b",
                },
            }
        )

        assert cfg is not None
        public = cfg.list_public()
        by_name = {e["name"]: e for e in public}
        assert set(by_name.keys()) == {"A", "B"}
        assert by_name["A"]["has_linear_key"] is False
        assert by_name["B"]["has_linear_key"] is True

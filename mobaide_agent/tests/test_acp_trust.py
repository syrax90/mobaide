"""Folder trust for chat sessions (``mobaide/acp/trust.py``): where the
Claude Code CLI keeps its trust record, which folders count as trusted, and
how ``grant`` writes the record without losing the rest of the file."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from mobaide.acp.trust import ClaudeFolderTrust, claude_config_path, too_broad_to_record
from tests.conftest import skip_if_not_linux

SESSION_ONLY = "Trust for this folder lasts for this session only."


def _project(tmp_path: Path, *parts: str) -> Path:
    folder = tmp_path.joinpath("work", *parts)
    folder.mkdir(parents=True)
    return folder


def _repo(folder: Path) -> Path:
    (folder / ".git").mkdir()
    return folder


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A home folder below ``tmp_path``, so ``tmp_path`` is a folder above it."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


def _write_config(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _trusted(*folders: Path) -> dict:
    return {
        "projects": {
            os.path.realpath(folder): {"hasTrustDialogAccepted": True} for folder in folders
        }
    }


class TestClaudeConfigPath:
    def test_config_dir_of_the_launch_env_wins_over_the_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "host"))

        path = claude_config_path({"CLAUDE_CONFIG_DIR": str(tmp_path / "launch")})

        assert path == tmp_path / "launch" / ".claude.json"

    def test_launch_env_without_a_config_dir_means_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "host"))

        assert claude_config_path({}) == Path.home() / ".claude.json"

    def test_no_env_reads_the_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "host"))

        assert claude_config_path() == tmp_path / "host" / ".claude.json"

    def test_tilde_is_expanded(self):
        path = claude_config_path({"CLAUDE_CONFIG_DIR": "~/claude-config"})

        assert path == Path.home() / "claude-config" / ".claude.json"


class TestIsTrusted:
    def test_missing_config_file_is_untrusted(self, tmp_path):
        trust = ClaudeFolderTrust(str(_project(tmp_path)), config_path=tmp_path / ".claude.json")

        assert trust.is_trusted() is False

    def test_the_folder_itself(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(project))

        assert ClaudeFolderTrust(str(project), config_path=config).is_trusted() is True

    def test_a_trusted_parent_covers_its_children(self, tmp_path):
        child = _project(tmp_path, "app", "src")
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(tmp_path / "work"))

        assert ClaudeFolderTrust(str(child), config_path=config).is_trusted() is True

    def test_a_trusted_child_does_not_cover_its_parent(self, tmp_path):
        child = _project(tmp_path, "app")
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(child))

        assert ClaudeFolderTrust(str(tmp_path / "work"), config_path=config).is_trusted() is False

    @skip_if_not_linux
    def test_a_symlinked_folder_matches_its_real_path(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(project))

        assert ClaudeFolderTrust(str(link), config_path=config).is_trusted() is True

    @pytest.mark.parametrize(
        "data",
        [
            {"projects": {"FOLDER": {"hasTrustDialogAccepted": False}}},
            {"projects": {"FOLDER": {"hasTrustDialogAccepted": "true"}}},
            {"projects": {"FOLDER": True}},
            {"projects": ["FOLDER"]},
            ["FOLDER"],
        ],
        ids=["declined", "string-true", "entry-not-object", "projects-not-object", "not-object"],
    )
    def test_anything_but_an_explicit_true_is_untrusted(self, tmp_path, data):
        project = _project(tmp_path)
        config = tmp_path / ".claude.json"
        config.write_text(json.dumps(data).replace("FOLDER", os.path.realpath(project)), encoding="utf-8")

        assert ClaudeFolderTrust(str(project), config_path=config).is_trusted() is False

    def test_unreadable_config_is_untrusted(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / ".claude.json"
        config.write_text("{not json", encoding="utf-8")

        assert ClaudeFolderTrust(str(project), config_path=config).is_trusted() is False


class TestGrant:
    def test_creates_a_private_config_file(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "config-dir" / ".claude.json"
        trust = ClaudeFolderTrust(str(project), config_path=config)

        trust.grant()

        assert json.loads(config.read_text(encoding="utf-8")) == _trusted(project)
        assert trust.is_trusted() is True
        if os.name != "nt":
            assert stat.S_IMODE(config.stat().st_mode) == 0o600

    def test_keeps_every_other_key_and_project(self, tmp_path):
        project = _project(tmp_path)
        folder = os.path.realpath(project)
        config = tmp_path / ".claude.json"
        _write_config(
            config,
            {
                "numStartups": 12,
                "oauthAccount": {"emailAddress": "dev@example.com"},
                "projects": {
                    "/other/project": {"hasTrustDialogAccepted": True, "allowedTools": ["Read"]},
                    folder: {"hasTrustDialogAccepted": False, "allowedTools": ["Bash"]},
                },
            },
        )

        ClaudeFolderTrust(str(project), config_path=config).grant()

        assert json.loads(config.read_text(encoding="utf-8")) == {
            "numStartups": 12,
            "oauthAccount": {"emailAddress": "dev@example.com"},
            "projects": {
                "/other/project": {"hasTrustDialogAccepted": True, "allowedTools": ["Read"]},
                folder: {"hasTrustDialogAccepted": True, "allowedTools": ["Bash"]},
            },
        }
        assert sorted(os.listdir(tmp_path)) == [".claude.json", "work"]

    def test_replaces_a_projects_value_that_is_not_an_object(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / ".claude.json"
        _write_config(config, {"theme": "dark", "projects": "broken"})

        ClaudeFolderTrust(str(project), config_path=config).grant()

        assert json.loads(config.read_text(encoding="utf-8")) == {"theme": "dark", **_trusted(project)}

    @skip_if_not_linux
    def test_keeps_the_file_mode(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / ".claude.json"
        _write_config(config, {})
        config.chmod(0o640)

        ClaudeFolderTrust(str(project), config_path=config).grant()

        assert stat.S_IMODE(config.stat().st_mode) == 0o640

    @skip_if_not_linux
    def test_records_the_real_path_of_a_symlinked_folder(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / ".claude.json"

        ClaudeFolderTrust(str(link), config_path=config).grant()

        assert list(json.loads(config.read_text(encoding="utf-8"))["projects"]) == [
            os.path.realpath(project)
        ]

    @pytest.mark.parametrize(
        ("content", "message"),
        [("{not json", None), ("[1, 2]", "is not a JSON object")],
        ids=["invalid-json", "not-object"],
    )
    def test_never_rewrites_a_file_it_cannot_read_whole(self, tmp_path, content, message):
        project = _project(tmp_path)
        config = tmp_path / ".claude.json"
        config.write_text(content, encoding="utf-8")

        with pytest.raises(ValueError, match=message):
            ClaudeFolderTrust(str(project), config_path=config).grant()

        assert config.read_text(encoding="utf-8") == content
        assert sorted(os.listdir(tmp_path)) == [".claude.json", "work"]


class TestRepositoryBoundary:
    def test_a_trusted_parent_does_not_cover_a_repository_inside_it(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(tmp_path / "work"))

        assert ClaudeFolderTrust(str(repo), config_path=config).is_trusted() is False

    def test_a_trusted_parent_does_not_cover_a_folder_deep_in_a_repository(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src"
        src.mkdir()
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(tmp_path / "work"))

        assert ClaudeFolderTrust(str(src), config_path=config).is_trusted() is False

    def test_a_trusted_repository_root_covers_its_subfolders(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src" / "app"
        src.mkdir(parents=True)
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(repo))

        assert ClaudeFolderTrust(str(src), config_path=config).is_trusted() is True

    def test_a_git_file_marks_a_worktree_root_too(self, tmp_path):
        worktree = _project(tmp_path, "linked")
        (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/linked\n", encoding="utf-8")
        config = tmp_path / ".claude.json"
        _write_config(config, _trusted(tmp_path / "work"))

        assert ClaudeFolderTrust(str(worktree), config_path=config).is_trusted() is False


class TestRealPathOnly:
    @skip_if_not_linux
    def test_a_trusted_symlink_spelling_does_not_cover_its_target(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / ".claude.json"
        _write_config(config, {"projects": {str(link): {"hasTrustDialogAccepted": True}}})

        assert ClaudeFolderTrust(str(link), config_path=config).is_trusted() is False

    @skip_if_not_linux
    def test_a_trusted_parent_is_looked_up_through_the_real_path(self, tmp_path):
        real_parent = _project(tmp_path, "real")
        (real_parent / "app").mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_parent, target_is_directory=True)
        config = tmp_path / ".claude.json"
        _write_config(config, {"projects": {str(link): {"hasTrustDialogAccepted": True}}})

        assert ClaudeFolderTrust(str(link / "app"), config_path=config).is_trusted() is False

        _write_config(config, _trusted(real_parent))

        assert ClaudeFolderTrust(str(link / "app"), config_path=config).is_trusted() is True


class TestTooBroadToRecord:
    def test_home_a_folder_above_it_and_a_root_are_too_broad(self, home, tmp_path):
        assert too_broad_to_record(str(home)) is True
        assert too_broad_to_record(str(tmp_path)) is True
        assert too_broad_to_record(tmp_path.anchor) is True

    def test_a_folder_below_home_or_beside_it_is_not(self, home, tmp_path):
        below = home / "proj"
        below.mkdir()

        assert too_broad_to_record(str(below)) is False
        assert too_broad_to_record(str(_project(tmp_path))) is False

    @pytest.mark.parametrize("which", ["home", "above-home", "root"])
    def test_grant_writes_nothing_and_reports_session_only_trust(self, home, tmp_path, which):
        folder = {"home": str(home), "above-home": str(tmp_path), "root": tmp_path.anchor}[which]
        config = tmp_path / "config-dir" / ".claude.json"
        trust = ClaudeFolderTrust(folder, config_path=config)

        assert trust.grant() is False

        assert not config.exists()
        assert not config.parent.exists()
        assert trust.prompt_text.endswith(f" {SESSION_ONLY}")

    def test_grant_keeps_an_existing_config_untouched(self, home, tmp_path):
        config = tmp_path / ".claude.json"
        _write_config(config, {"theme": "dark"})
        before = config.read_text(encoding="utf-8")

        assert ClaudeFolderTrust(str(home), config_path=config).grant() is False

        assert config.read_text(encoding="utf-8") == before

    def test_a_project_folder_is_recorded_and_its_prompt_has_no_session_note(self, home, tmp_path):
        project = home / "proj"
        project.mkdir()
        config = tmp_path / ".claude.json"
        trust = ClaudeFolderTrust(str(project), config_path=config)

        assert SESSION_ONLY not in trust.prompt_text
        assert trust.grant() is True

        assert json.loads(config.read_text(encoding="utf-8")) == _trusted(project)

    @skip_if_not_linux
    def test_a_symlink_to_home_is_too_broad(self, home, tmp_path):
        link = tmp_path / "home-link"
        link.symlink_to(home, target_is_directory=True)
        config = tmp_path / ".claude.json"

        assert ClaudeFolderTrust(str(link), config_path=config).grant() is False
        assert not config.exists()

"""Folder trust for chat sessions (``mobaide/acp/trust.py``): where the
Claude Code and Codex CLIs keep their trust records, which folders count as
trusted, and how ``grant`` writes the record without losing the rest of the
file."""
from __future__ import annotations

import json
import os
import stat
import tomllib
from pathlib import Path

import pytest

from mobaide.acp.trust import (
    ClaudeFolderTrust,
    CodexFolderTrust,
    claude_config_path,
    codex_config_path,
    too_broad_to_record,
)
from tests.conftest import skip_if_not_linux
from tests.git_repo_support import init_repo, requires_git, run_git_sync

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


def _codex_entries(entries: dict[Path, str]) -> str:
    return "".join(
        f"[projects.{json.dumps(os.path.realpath(folder))}]\ntrust_level = {json.dumps(level)}\n\n"
        for folder, level in entries.items()
    )


def _codex_trust(cwd: Path | str, config: Path) -> CodexFolderTrust:
    return CodexFolderTrust(str(cwd), config_path=config)


class TestCodexConfigPath:
    def test_codex_home_of_the_launch_env_wins_over_the_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))

        path = codex_config_path({"CODEX_HOME": str(tmp_path / "launch")})

        assert path == tmp_path / "launch" / "config.toml"

    def test_launch_env_without_codex_home_means_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))

        assert codex_config_path({}) == Path.home() / ".codex" / "config.toml"

    def test_no_env_reads_the_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))

        assert codex_config_path() == tmp_path / "host" / "config.toml"


class TestCodexIsTrusted:
    def test_missing_config_file_is_untrusted(self, tmp_path):
        assert _codex_trust(_project(tmp_path), tmp_path / "config.toml").is_trusted() is False

    def test_the_folder_itself(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({project: "trusted"}), encoding="utf-8")

        assert _codex_trust(project, config).is_trusted() is True

    def test_a_trusted_parent_does_not_cover_its_children(self, tmp_path):
        """Codex looks up the folder and its repository, never other parents."""
        child = _project(tmp_path, "app", "src")
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({tmp_path / "work": "trusted"}), encoding="utf-8")

        assert _codex_trust(child, config).is_trusted() is False

    def test_the_nearest_recorded_folder_decides(self, tmp_path):
        child = _project(tmp_path, "app")
        config = tmp_path / "config.toml"

        config.write_text(
            _codex_entries({tmp_path / "work": "trusted", child: "untrusted"}), encoding="utf-8"
        )
        assert _codex_trust(child, config).is_trusted() is False

        config.write_text(
            _codex_entries({tmp_path / "work": "untrusted", child: "trusted"}), encoding="utf-8"
        )
        assert _codex_trust(child, config).is_trusted() is True

    def test_the_first_recorded_key_decides_over_the_repository_root(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src"
        src.mkdir()
        config = tmp_path / "config.toml"

        config.write_text(_codex_entries({repo: "trusted", src: "untrusted"}), encoding="utf-8")
        assert _codex_trust(src, config).is_trusted() is False

        config.write_text(_codex_entries({repo: "untrusted", src: "trusted"}), encoding="utf-8")
        assert _codex_trust(src, config).is_trusted() is True

    def test_an_entry_without_a_trust_level_stops_the_lookup(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src"
        src.mkdir()
        config = tmp_path / "config.toml"
        config.write_text(
            f"[projects.{json.dumps(os.path.realpath(src))}]\nnote = \"mine\"\n\n"
            + _codex_entries({repo: "trusted"}),
            encoding="utf-8",
        )

        assert _codex_trust(src, config).is_trusted() is False

    @skip_if_not_linux
    def test_the_folder_as_given_counts_after_its_real_path(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / "config.toml"
        link_entry = f'[projects.{json.dumps(str(link))}]\ntrust_level = "trusted"\n\n'

        config.write_text(link_entry, encoding="utf-8")
        assert _codex_trust(link, config).is_trusted() is True

        config.write_text(link_entry + _codex_entries({project: "untrusted"}), encoding="utf-8")
        assert _codex_trust(link, config).is_trusted() is False

    def test_a_trusted_parent_does_not_cover_a_repository_inside_it(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src"
        src.mkdir()
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({tmp_path / "work": "trusted"}), encoding="utf-8")

        assert _codex_trust(repo, config).is_trusted() is False
        assert _codex_trust(src, config).is_trusted() is False

    def test_a_trusted_repository_root_covers_its_subfolders(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src" / "app"
        src.mkdir(parents=True)
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({repo: "trusted"}), encoding="utf-8")

        assert _codex_trust(src, config).is_trusted() is True

    @pytest.mark.parametrize(
        "text",
        ["[projects\nbroken", 'projects = "x"', 'projects = { "FOLDER" = "trusted" }'],
        ids=["invalid-toml", "projects-not-table", "entry-not-table"],
    )
    def test_unusable_config_is_untrusted(self, tmp_path, text):
        project = _project(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text(text.replace("FOLDER", os.path.realpath(project)), encoding="utf-8")

        assert _codex_trust(project, config).is_trusted() is False


class TestCodexGrant:
    def test_creates_the_config_file(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "codex-home" / "config.toml"
        trust = _codex_trust(project, config)

        assert trust.grant() is True

        assert tomllib.loads(config.read_text(encoding="utf-8")) == {
            "projects": {os.path.realpath(project): {"trust_level": "trusted"}}
        }
        assert trust.is_trusted() is True

    def test_a_folder_in_a_repository_records_the_repository_root(self, tmp_path):
        repo = _repo(_project(tmp_path, "cloned"))
        src = repo / "src"
        src.mkdir()
        config = tmp_path / "config.toml"

        assert _codex_trust(src, config).grant() is True

        projects = tomllib.loads(config.read_text(encoding="utf-8"))["projects"]
        assert list(projects) == [os.path.realpath(repo)]
        assert _codex_trust(repo, config).is_trusted() is True

    @pytest.mark.parametrize("trailing_newline", [True, False])
    def test_appends_and_keeps_the_users_text_byte_for_byte(self, tmp_path, trailing_newline):
        project = _project(tmp_path)
        config = tmp_path / "config.toml"
        before = (
            'model = "gpt-5.6-terra"  # my default\n'
            "\n"
            "[mcp_servers.docs]\n"
            'command = "docs-mcp"\n'
            'args = ["--port", "9000"]\n'
            '\n[projects."/elsewhere"]\n'
            'trust_level = "untrusted"'
        ) + ("\n" if trailing_newline else "")
        config.write_text(before, encoding="utf-8")

        assert _codex_trust(project, config).grant() is True

        after = config.read_text(encoding="utf-8")
        assert after.startswith(before)
        assert tomllib.loads(after) == {
            "model": "gpt-5.6-terra",
            "mcp_servers": {"docs": {"command": "docs-mcp", "args": ["--port", "9000"]}},
            "projects": {
                "/elsewhere": {"trust_level": "untrusted"},
                os.path.realpath(project): {"trust_level": "trusted"},
            },
        }

    def test_a_folder_name_that_needs_quoting_round_trips(self, tmp_path):
        project = _project(tmp_path, 'we"ird \\ näme')
        config = tmp_path / "config.toml"
        trust = _codex_trust(project, config)

        assert trust.grant() is True

        assert list(tomllib.loads(config.read_text(encoding="utf-8"))["projects"]) == [
            os.path.realpath(project)
        ]
        assert trust.is_trusted() is True

    @pytest.mark.parametrize("level", ["trusted", "untrusted"])
    def test_a_folder_already_listed_is_left_alone(self, tmp_path, level):
        """Editing the user's own table could break their formatting: the
        trust then lasts for this session only."""
        project = _project(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({project: level}), encoding="utf-8")
        before = config.read_text(encoding="utf-8")

        assert _codex_trust(project, config).grant() is False

        assert config.read_text(encoding="utf-8") == before

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("[projects\nbroken", None),
            ('projects = "x"\n', "projects is not a table"),
            ('projects = { "/a" = { trust_level = "trusted" } }\n', None),
        ],
        ids=["invalid-toml", "projects-not-table", "inline-projects-table"],
    )
    def test_never_rewrites_a_file_it_cannot_extend_safely(self, tmp_path, content, message):
        project = _project(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text(content, encoding="utf-8")

        with pytest.raises(ValueError, match=message):
            _codex_trust(project, config).grant()

        assert config.read_text(encoding="utf-8") == content
        assert sorted(os.listdir(tmp_path)) == ["config.toml", "work"]

    @skip_if_not_linux
    def test_keeps_the_file_mode(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text('model = "x"\n', encoding="utf-8")
        config.chmod(0o640)

        _codex_trust(project, config).grant()

        assert stat.S_IMODE(config.stat().st_mode) == 0o640

    @pytest.mark.parametrize("which", ["home", "above-home", "root"])
    def test_too_broad_writes_nothing_and_reports_session_only_trust(self, home, tmp_path, which):
        folder = {"home": str(home), "above-home": str(tmp_path), "root": tmp_path.anchor}[which]
        config = tmp_path / "codex-home" / "config.toml"
        trust = _codex_trust(folder, config)

        assert trust.grant() is False

        assert not config.parent.exists()
        assert trust.prompt_text.endswith(f" {SESSION_ONLY}")

    def test_a_folder_in_a_repository_at_home_is_too_broad(self, home, tmp_path):
        """A dotfiles repository at home would record trust for home itself."""
        _repo(home)
        project = home / "proj"
        project.mkdir()
        config = tmp_path / "config.toml"
        trust = _codex_trust(project, config)

        assert trust.prompt_text.endswith(f" {SESSION_ONLY}")
        assert trust.grant() is False
        assert not config.exists()

    def test_a_project_below_home_has_no_session_note(self, home, tmp_path):
        project = home / "proj"
        project.mkdir()
        trust = _codex_trust(project, tmp_path / "config.toml")

        assert SESSION_ONLY not in trust.prompt_text
        assert trust.prompt_text.startswith("Codex will use this folder's settings")


def _linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real repository at ``work/main`` and its ``git worktree add`` at ``work/linked``."""
    main = Path(init_repo(str(tmp_path / "work" / "main")))
    linked = tmp_path / "work" / "linked"
    run_git_sync(str(main), "worktree", "add", "-q", "-b", "feature", str(linked))
    return main, linked


@requires_git
class TestCodexLinkedWorktree:
    def test_is_trusted_through_its_main_checkout(self, tmp_path):
        main, linked = _linked_worktree(tmp_path)
        src = linked / "src"
        src.mkdir()
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({main: "trusted"}), encoding="utf-8")

        assert _codex_trust(linked, config).is_trusted() is True
        assert _codex_trust(src, config).is_trusted() is True

    def test_its_own_entry_comes_before_the_main_checkout(self, tmp_path):
        main, linked = _linked_worktree(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({main: "trusted", linked: "untrusted"}), encoding="utf-8")

        assert _codex_trust(linked, config).is_trusted() is False

    def test_a_trusted_worktree_does_not_cover_the_main_checkout(self, tmp_path):
        main, linked = _linked_worktree(tmp_path)
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({linked: "trusted"}), encoding="utf-8")

        assert _codex_trust(main, config).is_trusted() is False

    def test_grant_records_the_main_checkout(self, tmp_path):
        main, linked = _linked_worktree(tmp_path)
        src = linked / "src"
        src.mkdir()
        config = tmp_path / "config.toml"

        assert _codex_trust(src, config).grant() is True

        projects = tomllib.loads(config.read_text(encoding="utf-8"))["projects"]
        assert list(projects) == [os.path.realpath(main)]
        assert _codex_trust(main, config).is_trusted() is True
        assert _codex_trust(linked, config).is_trusted() is True


class TestCodexGitFileLayouts:
    def test_a_worktree_without_a_commondir_file_still_finds_its_main_checkout(self, tmp_path):
        main = _repo(_project(tmp_path, "main"))
        admin = main / ".git" / "worktrees" / "linked"
        admin.mkdir(parents=True)
        linked = _project(tmp_path, "linked")
        (linked / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({main: "trusted"}), encoding="utf-8")

        assert _codex_trust(linked, config).is_trusted() is True

    def test_a_submodule_is_not_trusted_through_its_superproject(self, tmp_path):
        superproject = _repo(_project(tmp_path, "super"))
        (superproject / ".git" / "modules" / "sub").mkdir(parents=True)
        sub = superproject / "sub"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
        config = tmp_path / "config.toml"
        config.write_text(_codex_entries({superproject: "trusted"}), encoding="utf-8")

        assert _codex_trust(sub, config).is_trusted() is False
        assert _codex_trust(sub, config).grant() is True
        assert list(tomllib.loads(config.read_text(encoding="utf-8"))["projects"]) == [
            os.path.realpath(superproject),
            os.path.realpath(sub),
        ]

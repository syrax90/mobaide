"""Folder trust for chat sessions (``mobaide/acp/trust.py``): where the
Claude Code, Codex, Antigravity and GitHub Copilot CLIs keep their trust
records, which folders count as trusted, and how ``grant`` writes the record
without losing the rest of the file."""
from __future__ import annotations

import json
import os
import stat
import tomllib
from pathlib import Path

import pytest

from mobaide.acp.trust import (
    AntigravityFolderTrust,
    ClaudeFolderTrust,
    CodexFolderTrust,
    CopilotFolderTrust,
    PiFolderTrust,
    claude_config_path,
    codex_config_path,
    copilot_config_path,
    pi_agent_dir,
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


class TestCodexIsTrusted:

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


class TestConcurrentGrants:
    """Two chat sessions approving trust for different folders at once each
    read, extend and replace the whole config file; neither record may be
    lost."""

    @staticmethod
    def _slow_writes(monkeypatch) -> None:
        import threading
        import time

        import mobaide.acp.trust as trust_module

        real_write = trust_module.atomic_write
        first = threading.Event()

        def slow_write(path, text, mode=None):
            # The first writer holds its stale copy while the other one runs.
            if not first.is_set():
                first.set()
                time.sleep(0.2)
            real_write(path, text, mode=mode)

        monkeypatch.setattr(trust_module, "atomic_write", slow_write)

    @staticmethod
    def _grant_both(grants) -> list[bool]:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda grant: grant(), grants))

    def test_claude_keeps_both_records(self, tmp_path, monkeypatch):
        self._slow_writes(monkeypatch)
        config = tmp_path / ".claude.json"
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        trusts = [ClaudeFolderTrust(str(f), config_path=config) for f in (a, b)]

        assert self._grant_both([t.grant for t in trusts]) == [True, True]

        assert all(t.is_trusted() for t in trusts)

    def test_codex_keeps_both_records(self, tmp_path, monkeypatch):
        self._slow_writes(monkeypatch)
        config = tmp_path / "config.toml"
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        trusts = [CodexFolderTrust(str(f), config_path=config) for f in (a, b)]

        assert self._grant_both([t.grant for t in trusts]) == [True, True]

        assert all(t.is_trusted() for t in trusts)

    def test_antigravity_keeps_both_records(self, tmp_path, monkeypatch):
        self._slow_writes(monkeypatch)
        settings = tmp_path / "settings.json"
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        trusts = [AntigravityFolderTrust(str(f), settings_path=settings) for f in (a, b)]

        assert self._grant_both([t.grant for t in trusts]) == [True, True]

        assert all(t.is_trusted() for t in trusts)

    def test_copilot_keeps_both_records(self, tmp_path, monkeypatch):
        self._slow_writes(monkeypatch)
        config = tmp_path / "config.json"
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        trusts = [CopilotFolderTrust(str(f), config_path=config) for f in (a, b)]

        assert self._grant_both([t.grant for t in trusts]) == [True, True]

        assert all(t.is_trusted() for t in trusts)

    def test_pi_keeps_both_records(self, tmp_path, monkeypatch):
        self._slow_writes(monkeypatch)
        pi_dir = tmp_path / "pi-agent"
        a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
        trusts = [PiFolderTrust(str(f), agent_dir=pi_dir) for f in (a, b)]

        assert self._grant_both([t.grant for t in trusts]) == [True, True]

        assert json.loads((pi_dir / "trust.json").read_text(encoding="utf-8")) == {
            os.path.realpath(a): True,
            os.path.realpath(b): True,
        }


def _agy_settings(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _agy_trust(cwd: Path | str, settings: Path) -> AntigravityFolderTrust:
    return AntigravityFolderTrust(str(cwd), settings_path=settings)


class TestAntigravityIsTrusted:

    def test_the_listed_folder(self, tmp_path):
        project = _project(tmp_path)
        settings = tmp_path / "settings.json"
        _agy_settings(settings, {"trustedWorkspaces": [os.path.realpath(project)]})

        assert _agy_trust(project, settings).is_trusted() is True

    @pytest.mark.parametrize("listed", ["parent", "repository-root"])
    def test_a_listed_parent_does_not_cover_its_children(self, tmp_path, listed):
        """``agy`` matches the exact folder, unlike Claude Code."""
        child = _project(tmp_path, "app", "src")
        parent = tmp_path / "work" / "app"
        if listed == "repository-root":
            _repo(parent)
        settings = tmp_path / "settings.json"
        _agy_settings(settings, {"trustedWorkspaces": [os.path.realpath(parent)]})

        assert _agy_trust(child, settings).is_trusted() is False

    @skip_if_not_linux
    def test_a_symlinked_folder_matches_its_real_path(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        settings = tmp_path / "settings.json"
        _agy_settings(settings, {"trustedWorkspaces": [os.path.realpath(project)]})

        assert _agy_trust(link, settings).is_trusted() is True

    @skip_if_not_linux
    def test_a_listed_symlink_matches_the_folder_it_points_to(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        settings = tmp_path / "settings.json"
        _agy_settings(settings, {"trustedWorkspaces": [str(link)]})

        assert _agy_trust(project, settings).is_trusted() is True

    @pytest.mark.parametrize(
        "data",
        [
            {"trustedWorkspaces": "FOLDER"},
            {"trustedWorkspaces": {"FOLDER": True}},
            {"trustedWorkspaces": [None, 1, ""]},
            {"otherKey": ["FOLDER"]},
            ["FOLDER"],
        ],
        ids=["string", "object", "no-strings", "other-key", "not-object"],
    )
    def test_anything_but_a_listed_folder_is_untrusted(self, tmp_path, data):
        project = _project(tmp_path)
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(data).replace("FOLDER", os.path.realpath(project)), encoding="utf-8"
        )

        assert _agy_trust(project, settings).is_trusted() is False

    def test_unreadable_settings_are_untrusted(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("{not json", encoding="utf-8")

        assert _agy_trust(_project(tmp_path), settings).is_trusted() is False


class TestAntigravityGrant:
    def test_creates_a_private_settings_file_as_agy_writes_it(self, tmp_path):
        project = _project(tmp_path)
        settings = tmp_path / "agy" / "settings.json"
        trust = _agy_trust(project, settings)

        assert trust.grant() is True

        expected = {"trustedWorkspaces": [os.path.realpath(project)]}
        assert settings.read_text(encoding="utf-8") == json.dumps(expected, indent=2) + "\n"
        assert trust.is_trusted() is True
        if os.name != "nt":
            assert stat.S_IMODE(settings.stat().st_mode) == 0o600

    def test_keeps_the_other_settings_and_the_file_mode(self, tmp_path):
        project = _project(tmp_path, "new")
        settings = tmp_path / "settings.json"
        _agy_settings(
            settings,
            {"theme": "dark", "trustedWorkspaces": ["/srv/old"], "model": "Gemini 3.8 Flash (High)"},
        )
        settings.chmod(0o640)

        assert _agy_trust(project, settings).grant() is True

        assert json.loads(settings.read_text(encoding="utf-8")) == {
            "theme": "dark",
            "trustedWorkspaces": ["/srv/old", os.path.realpath(project)],
            "model": "Gemini 3.8 Flash (High)",
        }
        if os.name != "nt":
            assert stat.S_IMODE(settings.stat().st_mode) == 0o640

    def test_a_folder_already_listed_is_not_written_again(self, tmp_path):
        project = _project(tmp_path)
        settings = tmp_path / "settings.json"
        _agy_settings(settings, {"trustedWorkspaces": [os.path.realpath(project)]})
        # The atomic rewrite replaces the file: a new inode.
        before = settings.stat().st_ino

        assert _agy_trust(project, settings).grant() is True

        assert settings.stat().st_ino == before
        assert json.loads(settings.read_text(encoding="utf-8"))["trustedWorkspaces"] == [
            os.path.realpath(project)
        ]

    @skip_if_not_linux
    def test_a_symlinked_folder_is_recorded_by_its_real_path(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        settings = tmp_path / "settings.json"

        assert _agy_trust(link, settings).grant() is True

        assert json.loads(settings.read_text(encoding="utf-8")) == {
            "trustedWorkspaces": [os.path.realpath(project)]
        }

    @pytest.mark.parametrize(
        "content",
        ["{not json", '["a list"]', '{"trustedWorkspaces": "/srv/x"}'],
        ids=["unreadable", "not-object", "list-not-a-list"],
    )
    def test_a_file_it_cannot_read_back_whole_is_never_rewritten(self, tmp_path, content):
        settings = tmp_path / "settings.json"
        settings.write_text(content, encoding="utf-8")

        with pytest.raises(ValueError):
            _agy_trust(_project(tmp_path), settings).grant()

        assert settings.read_text(encoding="utf-8") == content

    @pytest.mark.parametrize("which", ["home", "above-home", "root"])
    def test_a_folder_too_broad_to_record_writes_nothing(self, home, tmp_path, which):
        folder = {"home": str(home), "above-home": str(tmp_path), "root": tmp_path.anchor}[which]
        settings = tmp_path / "agy" / "settings.json"
        trust = AntigravityFolderTrust(folder, settings_path=settings)

        assert trust.grant() is False

        assert not settings.parent.exists()
        assert trust.prompt_text.endswith(f" {SESSION_ONLY}")


# The comment lines Copilot writes above the JSON object of its ``config.json``.
COPILOT_HEADER = "// User settings belong in settings.json.\n// This file is managed automatically.\n"


def _copilot_config(path: Path, data: object, header: str = COPILOT_HEADER) -> None:
    path.write_text(header + json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _copilot_body(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads("\n".join(line for line in lines if not line.startswith("//")))


def _copilot_trust(cwd: Path | str, config: Path) -> CopilotFolderTrust:
    return CopilotFolderTrust(str(cwd), config_path=config)


class TestCopilotConfigPath:
    def test_copilot_home_of_the_launch_env_wins_over_the_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "host"))

        path = copilot_config_path({"COPILOT_HOME": str(tmp_path / "launch")})

        assert path == tmp_path / "launch" / "config.json"

    def test_without_copilot_home_it_is_in_the_home_folder(self, home):
        assert copilot_config_path({}) == home / ".copilot" / "config.json"


class TestCopilotIsTrusted:
    def test_the_listed_folder_below_the_comment_lines(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [os.path.realpath(project)]})

        assert _copilot_trust(project, config).is_trusted() is True

    def test_a_listed_parent_covers_its_children(self, tmp_path):
        """Copilot trusts the folders inside a listed folder, unlike ``agy``."""
        child = _project(tmp_path, "app", "src")
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [os.path.realpath(tmp_path / "work" / "app")]})

        assert _copilot_trust(child, config).is_trusted() is True

    @pytest.mark.parametrize("listed", ["app/src", "ap"], ids=["child", "name-prefix"])
    def test_a_listed_folder_covers_nothing_outside_it(self, tmp_path, listed):
        project = _project(tmp_path, "app")
        other = tmp_path.joinpath("work", listed)
        other.mkdir(parents=True)
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [os.path.realpath(other)]})

        assert _copilot_trust(project, config).is_trusted() is False

    def test_a_relative_entry_never_matches(self, tmp_path, monkeypatch):
        """A relative entry would mean the folder the agent runs in."""
        project = _project(tmp_path)
        monkeypatch.chdir(project)
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [".", ""]})

        assert _copilot_trust(project, config).is_trusted() is False

    @skip_if_not_linux
    def test_a_symlinked_folder_matches_its_real_path(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [os.path.realpath(project)]})

        assert _copilot_trust(link, config).is_trusted() is True

    @skip_if_not_linux
    def test_a_listed_symlink_matches_the_folder_it_points_to(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [str(link)]})

        assert _copilot_trust(project, config).is_trusted() is True

    @pytest.mark.parametrize(
        "data",
        [
            {"trustedFolders": "FOLDER"},
            {"trustedFolders": {"FOLDER": True}},
            {"trustedFolders": [None, 1, ""]},
            {"otherKey": ["FOLDER"]},
            ["FOLDER"],
        ],
        ids=["string", "object", "no-strings", "other-key", "not-object"],
    )
    def test_anything_but_a_listed_folder_is_untrusted(self, tmp_path, data):
        project = _project(tmp_path)
        config = tmp_path / "config.json"
        config.write_text(
            COPILOT_HEADER + json.dumps(data).replace("FOLDER", os.path.realpath(project)),
            encoding="utf-8",
        )

        assert _copilot_trust(project, config).is_trusted() is False

    def test_an_unreadable_config_is_untrusted(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text(COPILOT_HEADER + "{not json", encoding="utf-8")

        assert _copilot_trust(_project(tmp_path), config).is_trusted() is False


class TestCopilotGrant:
    def test_creates_a_private_config_file_as_copilot_writes_it(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "copilot" / "config.json"
        trust = _copilot_trust(project, config)

        assert trust.grant() is True

        expected = {"trustedFolders": [os.path.realpath(project)]}
        assert config.read_text(encoding="utf-8") == (
            COPILOT_HEADER + json.dumps(expected, indent=2) + "\n"
        )
        assert trust.is_trusted() is True
        if os.name != "nt":
            assert stat.S_IMODE(config.stat().st_mode) == 0o600

    def test_keeps_the_comment_lines_the_other_keys_and_the_file_mode(self, tmp_path):
        project = _project(tmp_path, "new")
        config = tmp_path / "config.json"
        header = "// A newer Copilot wrote this line.\n\n// And this one.\n"
        _copilot_config(
            config,
            {"firstLaunchAt": "2026-11-01", "trustedFolders": ["/srv/old"], "staff": False},
            header=header,
        )
        config.chmod(0o640)

        assert _copilot_trust(project, config).grant() is True

        assert config.read_text(encoding="utf-8").startswith(header + "{")
        assert _copilot_body(config) == {
            "firstLaunchAt": "2026-11-01",
            "trustedFolders": ["/srv/old", os.path.realpath(project)],
            "staff": False,
        }
        if os.name != "nt":
            assert stat.S_IMODE(config.stat().st_mode) == 0o640

    def test_a_config_with_only_the_comment_lines_gets_the_list(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "config.json"
        config.write_text(COPILOT_HEADER, encoding="utf-8")

        assert _copilot_trust(project, config).grant() is True

        assert config.read_text(encoding="utf-8").startswith(COPILOT_HEADER + "{")
        assert _copilot_body(config) == {"trustedFolders": [os.path.realpath(project)]}

    def test_a_folder_already_listed_is_not_written_again(self, tmp_path):
        project = _project(tmp_path)
        config = tmp_path / "config.json"
        _copilot_config(config, {"trustedFolders": [os.path.realpath(project)]})
        # The atomic rewrite replaces the file: a new inode.
        before = config.stat().st_ino

        assert _copilot_trust(project, config).grant() is True

        assert config.stat().st_ino == before
        assert _copilot_body(config)["trustedFolders"] == [os.path.realpath(project)]

    @skip_if_not_linux
    def test_a_symlinked_folder_is_recorded_by_its_real_path(self, tmp_path):
        project = _project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        config = tmp_path / "config.json"

        assert _copilot_trust(link, config).grant() is True

        assert _copilot_body(config) == {"trustedFolders": [os.path.realpath(project)]}

    @pytest.mark.parametrize(
        "body",
        ["{not json", '["a list"]', '{"trustedFolders": "/srv/x"}'],
        ids=["unreadable", "not-object", "list-not-a-list"],
    )
    def test_a_file_it_cannot_read_back_whole_is_never_rewritten(self, tmp_path, body):
        config = tmp_path / "config.json"
        content = COPILOT_HEADER + body
        config.write_text(content, encoding="utf-8")

        with pytest.raises(ValueError):
            _copilot_trust(_project(tmp_path), config).grant()

        assert config.read_text(encoding="utf-8") == content

    @pytest.mark.parametrize("which", ["home", "above-home", "root"])
    def test_a_folder_too_broad_to_record_writes_nothing(self, home, tmp_path, which):
        folder = {"home": str(home), "above-home": str(tmp_path), "root": tmp_path.anchor}[which]
        config = tmp_path / "copilot" / "config.json"
        trust = CopilotFolderTrust(folder, config_path=config)

        assert trust.grant() is False

        assert not config.parent.exists()
        assert trust.prompt_text.endswith(f" {SESSION_ONLY}")


def _pi_project(tmp_path: Path, *parts: str, resource: str = ".pi/settings.json") -> Path:
    """A folder with something Pi would load from it, so Pi asks about it."""
    folder = _project(tmp_path, *parts)
    target = folder.joinpath(*resource.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    return folder


def _pi_dir(tmp_path: Path, trust: object = None, settings: object = None) -> Path:
    folder = tmp_path / "pi-agent"
    folder.mkdir(exist_ok=True)
    if trust is not None:
        _write_config(folder / "trust.json", trust)
    if settings is not None:
        _write_config(folder / "settings.json", settings)
    return folder


def _pi_trust(cwd: Path | str, agent_dir: Path) -> PiFolderTrust:
    return PiFolderTrust(str(cwd), agent_dir=agent_dir)


def _pi_records(agent_dir: Path) -> dict:
    return json.loads((agent_dir / "trust.json").read_text(encoding="utf-8"))


class TestPiAgentDir:
    def test_the_agent_dir_of_the_launch_env_wins_over_the_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "host"))

        assert pi_agent_dir({"PI_CODING_AGENT_DIR": str(tmp_path / "launch")}) == tmp_path / "launch"

    def test_without_an_agent_dir_it_is_in_the_home_folder(self, home):
        assert pi_agent_dir({}) == home / ".pi" / "agent"


class TestPiIsTrusted:
    def test_a_folder_with_nothing_to_load_needs_no_answer(self, tmp_path):
        """Pi asks no trust question for it, so the chat asks none."""
        project = _project(tmp_path)
        (project / ".pi" / "sessions").mkdir(parents=True)
        (project / ".pi" / "notes.md").write_text("mine", encoding="utf-8")

        assert _pi_trust(project, _pi_dir(tmp_path)).is_trusted() is True

    @pytest.mark.parametrize(
        "resource",
        [
            ".pi/settings.json",
            ".pi/mcp.json",
            ".pi/extensions/lint.ts",
            ".pi/skills/review/SKILL.md",
            ".pi/prompts/fix.md",
            ".pi/themes/dark.json",
            ".pi/SYSTEM.md",
            ".pi/APPEND_SYSTEM.md",
            ".agents/skills/review/SKILL.md",
        ],
    )
    def test_a_folder_with_resources_and_no_decision_is_untrusted(self, tmp_path, resource):
        project = _pi_project(tmp_path, resource=resource)

        assert _pi_trust(project, _pi_dir(tmp_path)).is_trusted() is False

    def test_skills_of_a_folder_above_count_too(self, tmp_path):
        _pi_project(tmp_path, "repo", resource=".agents/skills/review/SKILL.md")
        child = tmp_path / "work" / "repo" / "packages" / "app"
        child.mkdir(parents=True)

        assert _pi_trust(child, _pi_dir(tmp_path)).is_trusted() is False

    def test_the_skills_of_the_user_are_no_resources_of_a_project(self, home, tmp_path):
        """``~/.agents/skills`` is above every folder in the home folder."""
        (home / ".agents" / "skills").mkdir(parents=True)
        project = home / "work" / "app"
        project.mkdir(parents=True)

        assert _pi_trust(project, _pi_dir(tmp_path)).is_trusted() is True

    @pytest.mark.parametrize("decision", [True, False], ids=["trusted", "not-trusted"])
    def test_a_saved_decision_is_an_answer(self, tmp_path, decision):
        """A saved "do not trust" too: Pi then starts without the folder's
        resources, and the chat does not ask again."""
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path, trust={os.path.realpath(project): decision})

        assert _pi_trust(project, pi_dir).is_trusted() is True

    def test_the_decision_for_a_parent_covers_its_children(self, tmp_path):
        child = _pi_project(tmp_path, "app", "src")
        pi_dir = _pi_dir(tmp_path, trust={os.path.realpath(tmp_path / "work" / "app"): True})

        assert _pi_trust(child, pi_dir).is_trusted() is True

    @pytest.mark.parametrize("listed", ["app/src", "ap"], ids=["child", "name-prefix"])
    def test_a_decision_covers_nothing_outside_its_folder(self, tmp_path, listed):
        project = _pi_project(tmp_path, "app")
        other = tmp_path.joinpath("work", listed)
        other.mkdir(parents=True)
        pi_dir = _pi_dir(tmp_path, trust={os.path.realpath(other): True})

        assert _pi_trust(project, pi_dir).is_trusted() is False

    @pytest.mark.parametrize(
        "body",
        ['{"FOLDER": "true"}', '{"FOLDER": null}', '{"FOLDER": 1}', '["FOLDER"]', "{not json"],
        ids=["string", "null", "number", "not-object", "unreadable"],
    )
    def test_anything_but_a_saved_true_or_false_is_no_decision(self, tmp_path, body):
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path)
        (pi_dir / "trust.json").write_text(
            body.replace("FOLDER", os.path.realpath(project)), encoding="utf-8"
        )

        assert _pi_trust(project, pi_dir).is_trusted() is False

    @pytest.mark.parametrize("mode", ["always", "never"])
    def test_a_setting_that_decides_for_every_folder_needs_no_answer(self, tmp_path, mode):
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path, settings={"defaultProjectTrust": mode})

        assert _pi_trust(project, pi_dir).is_trusted() is True

    @pytest.mark.parametrize(
        "settings",
        [{"defaultProjectTrust": "ask"}, {"theme": "dark"}, ["always"]],
        ids=["ask", "not-set", "not-object"],
    )
    def test_a_pi_that_asks_leaves_the_folder_untrusted(self, tmp_path, settings):
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path, settings=settings)

        assert _pi_trust(project, pi_dir).is_trusted() is False

    @skip_if_not_linux
    def test_a_symlinked_folder_matches_its_real_path(self, tmp_path):
        project = _pi_project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        pi_dir = _pi_dir(tmp_path, trust={os.path.realpath(project): True})

        assert _pi_trust(link, pi_dir).is_trusted() is True


class TestPiGrant:
    def test_creates_the_trust_file_and_frees_the_lock_of_pi(self, tmp_path):
        project = _pi_project(tmp_path)
        pi_dir = tmp_path / "pi-agent"
        trust = _pi_trust(project, pi_dir)

        assert trust.grant() is True

        assert _pi_records(pi_dir) == {os.path.realpath(project): True}
        assert trust.is_trusted() is True
        # Pi could not write its trust file while the lock folder stays.
        assert not (pi_dir / "trust.json.lock").exists()

    def test_keeps_the_other_decisions_and_the_file_mode(self, tmp_path):
        project = _pi_project(tmp_path, "new")
        pi_dir = _pi_dir(tmp_path, trust={"/srv/old": True, "/srv/never": False})
        (pi_dir / "trust.json").chmod(0o600)

        assert _pi_trust(project, pi_dir).grant() is True

        assert _pi_records(pi_dir) == {
            "/srv/old": True,
            "/srv/never": False,
            os.path.realpath(project): True,
        }
        if os.name != "nt":
            assert stat.S_IMODE((pi_dir / "trust.json").stat().st_mode) == 0o600

    def test_a_saved_do_not_trust_becomes_trust(self, tmp_path):
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path, trust={os.path.realpath(project): False})

        assert _pi_trust(project, pi_dir).grant() is True

        assert _pi_records(pi_dir) == {os.path.realpath(project): True}

    @skip_if_not_linux
    def test_a_symlinked_folder_is_recorded_by_its_real_path(self, tmp_path):
        project = _pi_project(tmp_path, "real")
        link = tmp_path / "link"
        link.symlink_to(project, target_is_directory=True)
        pi_dir = _pi_dir(tmp_path)

        assert _pi_trust(link, pi_dir).grant() is True

        assert _pi_records(pi_dir) == {os.path.realpath(project): True}

    @pytest.mark.parametrize("body", ["{not json", '["a list"]'], ids=["unreadable", "not-object"])
    def test_a_file_it_cannot_read_back_whole_is_never_rewritten(self, tmp_path, body):
        pi_dir = _pi_dir(tmp_path)
        (pi_dir / "trust.json").write_text(body, encoding="utf-8")

        with pytest.raises(ValueError):
            _pi_trust(_pi_project(tmp_path), pi_dir).grant()

        assert (pi_dir / "trust.json").read_text(encoding="utf-8") == body
        assert not (pi_dir / "trust.json.lock").exists()

    @pytest.mark.parametrize("age", [0.0, 60.0], ids=["held-too-long", "left-by-a-dead-pi"])
    def test_a_lock_it_cannot_take_stays_and_the_trust_is_still_saved(
        self, tmp_path, monkeypatch, age
    ):
        """The rewrite is atomic either way, and the lock is not this
        code's to remove."""
        import mobaide.acp.trust as trust_module

        monkeypatch.setattr(trust_module, "_PI_LOCK_WAIT_SECONDS", 0.0)
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path, trust={"/srv/old": True})
        lock = pi_dir / "trust.json.lock"
        lock.mkdir()
        held_since = lock.stat().st_mtime - age
        os.utime(lock, (held_since, held_since))

        assert _pi_trust(project, pi_dir).grant() is True

        assert _pi_records(pi_dir) == {"/srv/old": True, os.path.realpath(project): True}
        assert lock.is_dir()

    @skip_if_not_linux
    @pytest.mark.timeout(20)
    def test_a_lock_path_nobody_can_take_gets_the_same_wait(self, tmp_path, monkeypatch):
        """Here a link to nothing: it is in the way and has no age to read.
        The grant must not try for ever."""
        import mobaide.acp.trust as trust_module

        monkeypatch.setattr(trust_module, "_PI_LOCK_WAIT_SECONDS", 0.0)
        project = _pi_project(tmp_path)
        pi_dir = _pi_dir(tmp_path, trust={"/srv/old": True})
        lock = pi_dir / "trust.json.lock"
        lock.symlink_to(pi_dir / "gone", target_is_directory=True)

        assert _pi_trust(project, pi_dir).grant() is True

        assert _pi_records(pi_dir) == {"/srv/old": True, os.path.realpath(project): True}
        assert lock.is_symlink()

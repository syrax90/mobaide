"""Per-user worktrees folder: the ``MOBAIDE_WORKTREES_DIR`` setting, the
per-project folder under it, and reuse of worktrees in the legacy in-project
folder."""
from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

from mobaide.linear.config import AutoScanConfig
from mobaide.linear.worktree import (
    PROJECT_OWNER_FILE,
    WorktreeCreationError,
    WorktreeManager,
    _claim,
    _safe_dir_name,
)

from .git_repo_support import init_repo, posix_only, requires_git


@pytest.fixture
def home(tmp_path, monkeypatch) -> str:
    """A fake home folder, so no test touches the real one."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    monkeypatch.delenv("MOBAIDE_WORKTREES_DIR", raising=False)
    return str(path)


def _manager(worktrees_dir: str, legacy: str | None = "mobaide_worktree") -> WorktreeManager:
    return WorktreeManager(
        worktrees_dir=worktrees_dir,
        branch_prefix="feature/mobaide",
        main_branch="main",
        legacy_worktree_base=legacy,
    )


def _owner(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


class TestResolvedWorktreesDir:
    def test_default_is_mobaide_worktrees_in_home(self, home, monkeypatch):
        monkeypatch.delenv("MOBAIDE_ENV", raising=False)

        cfg = AutoScanConfig.from_env()

        assert cfg.worktrees_dir is None
        assert cfg.resolved_worktrees_dir() == os.path.join(home, ".mobaide", "worktrees")

    def test_test_mode_default_uses_the_test_state_folder(self, home, monkeypatch):
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        monkeypatch.delenv("MOBAIDE_AGENT_PREFIX", raising=False)
        from mobaide.agent.install_paths import state_prefix

        resolved = AutoScanConfig.from_env().resolved_worktrees_dir()

        assert resolved == str(state_prefix(test_mode=True) / "worktrees")
        assert not resolved.startswith(home)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_env_value_means_default(self, home, monkeypatch, value):
        monkeypatch.delenv("MOBAIDE_ENV", raising=False)
        monkeypatch.setenv("MOBAIDE_WORKTREES_DIR", value)

        cfg = AutoScanConfig.from_env()

        assert cfg.worktrees_dir is None
        assert cfg.resolved_worktrees_dir() == os.path.join(home, ".mobaide", "worktrees")

    @pytest.mark.parametrize(
        ("value", "parts"),
        [
            ("~/wt", ("wt",)),
            (os.path.join("work", "trees"), ("work", "trees")),
            (os.path.join("$WT_SUB", "wt"), ("sub", "wt")),
        ],
    )
    def test_value_is_expanded_and_anchored_at_home(self, home, monkeypatch, value, parts):
        monkeypatch.setenv("WT_SUB", "sub")
        monkeypatch.setenv("MOBAIDE_WORKTREES_DIR", value)

        assert AutoScanConfig.from_env().resolved_worktrees_dir() == os.path.join(home, *parts)

    def test_absolute_value_is_kept(self, home, tmp_path, monkeypatch):
        target = str(tmp_path / "abs" / "wt")
        monkeypatch.setenv("MOBAIDE_WORKTREES_DIR", target)

        assert AutoScanConfig.from_env().resolved_worktrees_dir() == target

    @pytest.mark.parametrize("test_mode", [False, True])
    def test_absolute_legacy_worktree_base_is_kept_on_upgrade(
        self, home, tmp_path, monkeypatch, test_mode,
    ):
        if test_mode:
            monkeypatch.setenv("MOBAIDE_ENV", "test")
        else:
            monkeypatch.delenv("MOBAIDE_ENV", raising=False)
        legacy = str(tmp_path / "moved_worktrees")
        monkeypatch.setenv("WORKTREE_BASE", legacy)

        assert AutoScanConfig.from_env().resolved_worktrees_dir() == legacy


class TestSafeDirName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("my-app", "my-app"),
            ('a<b>c:d"e|f?g*h', "a_b_c_d_e_f_g_h"),
            ("tab\there", "tab_here"),
            ("trailing. . ", "trailing"),
            ("...", "project"),
            ("con", "con_"),
            ("NUL.txt", "NUL.txt_"),
            ("console", "console"),
        ],
    )
    def test_cleans_names(self, name, expected):
        assert _safe_dir_name(name) == expected


class TestProjectWorktreesDir:
    def test_named_after_project_and_claimed(self, tmp_path):
        root = tmp_path / "repos" / "app"
        root.mkdir(parents=True)
        wt = str(tmp_path / "wt")

        folder = _manager(wt).project_worktrees_dir(str(root))

        assert folder == os.path.join(wt, "app")
        with open(os.path.join(folder, PROJECT_OWNER_FILE), encoding="utf-8") as fh:
            assert fh.read().strip() == _owner(str(root))

    def test_same_project_gets_the_same_folder(self, tmp_path):
        root = tmp_path / "app"
        root.mkdir()
        wt = str(tmp_path / "wt")

        first = _manager(wt).project_worktrees_dir(str(root))
        second = _manager(wt).project_worktrees_dir(str(root))

        assert first == second

    def test_same_name_other_repo_gets_hashed_folder(self, tmp_path):
        a = tmp_path / "one" / "app"
        b = tmp_path / "two" / "app"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        wt = str(tmp_path / "wt")
        mgr = _manager(wt)

        folder_a = mgr.project_worktrees_dir(str(a))
        folder_b = mgr.project_worktrees_dir(str(b))

        digest = hashlib.sha256(_owner(str(b)).encode("utf-8")).hexdigest()[:8]
        assert folder_a == os.path.join(wt, "app")
        assert folder_b == os.path.join(wt, f"app-{digest}")
        assert mgr.project_worktrees_dir(str(b)) == folder_b
        assert mgr.project_worktrees_dir(str(a)) == folder_a

    def test_unowned_non_empty_folder_is_not_claimed(self, tmp_path):
        root = tmp_path / "app"
        root.mkdir()
        wt = tmp_path / "wt"
        (wt / "app").mkdir(parents=True)
        (wt / "app" / "stray.txt").write_text("x")

        folder = _manager(str(wt)).project_worktrees_dir(str(root))

        assert folder != str(wt / "app")
        assert not (wt / "app" / PROJECT_OWNER_FILE).exists()

    def test_empty_unowned_folder_is_claimed(self, tmp_path):
        root = tmp_path / "app"
        root.mkdir()
        wt = tmp_path / "wt"
        (wt / "app").mkdir(parents=True)

        folder = _manager(str(wt)).project_worktrees_dir(str(root))

        assert folder == str(wt / "app")

    @pytest.mark.parametrize("hard_links", [True, False])
    def test_claim_keeps_foreign_marker_and_leaves_no_temp_files(
        self, tmp_path, monkeypatch, hard_links,
    ):
        if not hard_links:
            def no_link(src, dst):
                raise OSError("hard links not supported")

            monkeypatch.setattr(os, "link", no_link)
        wt = tmp_path / "wt"
        folder = str(wt / "app")

        assert _claim(folder, "first-owner") is True
        assert _claim(folder, "second-owner") is False

        marker = wt / "app" / PROJECT_OWNER_FILE
        assert marker.read_text().strip() == "first-owner"
        assert sorted(p.name for p in wt.iterdir()) == ["app"]
        assert sorted(p.name for p in (wt / "app").iterdir()) == [PROJECT_OWNER_FILE]

    @posix_only
    def test_symlinked_root_shares_the_folder(self, tmp_path):
        root = tmp_path / "app"
        root.mkdir()
        link = tmp_path / "link"
        link.symlink_to(root)
        wt = str(tmp_path / "wt")
        mgr = _manager(wt)

        assert mgr.project_worktrees_dir(str(link)) == mgr.project_worktrees_dir(str(root))


@requires_git
class TestCreateWorktreeLocation:
    @pytest.fixture
    def repo(self, tmp_path):
        return init_repo(str(tmp_path / "project"))

    async def test_new_worktree_goes_to_per_user_folder(self, repo, tmp_path):
        wt = str(tmp_path / "wt")

        result = await _manager(wt).create_worktree("topic", repo, branch_name="topic")

        assert result.is_new is True
        assert result.path == os.path.join(wt, "project", "topic")
        assert not os.path.exists(os.path.join(repo, "mobaide_worktree"))
        head = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=result.path, capture_output=True, text=True, check=True,
        )
        assert head.stdout.strip() == "topic"

    async def test_legacy_in_project_worktree_is_reused(self, repo, tmp_path):
        legacy_path = os.path.join(repo, "mobaide_worktree", "topic")
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "topic", legacy_path],
            cwd=repo, capture_output=True, check=True,
        )
        wt = str(tmp_path / "wt")

        result = await _manager(wt).create_worktree("topic", repo, branch_name="topic")

        assert result.is_new is False
        assert result.path == legacy_path
        assert result.branch == "topic"
        assert not os.path.exists(os.path.join(wt, "project", "topic"))

    async def test_absolute_legacy_base_is_reused(self, repo, tmp_path):
        abs_base = str(tmp_path / "abs_legacy")
        legacy_path = os.path.join(abs_base, "topic")
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "topic", legacy_path],
            cwd=repo, capture_output=True, check=True,
        )
        wt = str(tmp_path / "wt")

        result = await _manager(wt, legacy=abs_base).create_worktree(
            "topic", repo, branch_name="topic",
        )

        assert result.is_new is False
        assert result.path == legacy_path
        assert not os.path.exists(os.path.join(wt, "project", "topic"))

    async def test_dash_branch_rejected_before_legacy_reuse(self, repo, tmp_path):
        os.makedirs(os.path.join(repo, "mobaide_worktree", "x"))

        with pytest.raises(WorktreeCreationError, match="must not start with '-'"):
            await _manager(str(tmp_path / "wt")).create_worktree(
                "x", repo, branch_name="-x",
            )

    async def test_slug_equal_to_project_folder_does_not_reuse_container(
        self, tmp_path,
    ):
        wts = str(tmp_path / "wts")
        repo = init_repo(str(tmp_path / "app"))
        manager = _manager(wts, legacy=wts)
        await manager.create_worktree("feat", repo, branch_name="feat")

        result = await manager.create_worktree("app", repo, branch_name="app")

        assert result.is_new is True
        assert result.path == os.path.join(wts, "app", "app")

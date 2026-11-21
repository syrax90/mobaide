"""Tests for ``mobaide.linear.worktree._run_git`` and the bounded fetch.

``_run_git`` goes through ``mobaide.core.git_exec.run_capped`` so a git
command that stalls (a fetch waiting on the network or a sign-in prompt) is
killed instead of holding the repo lock and the session create forever. These
tests run real git against temp repos; only the command meant to stall is
swapped for a timed-out result, so nothing sleeps.
"""
from __future__ import annotations

import os
import subprocess

import pytest

import mobaide.linear.worktree as _wt
from mobaide.core.git_exec import CmdResult, reset_caches
from mobaide.linear.worktree import WorktreeCreationError, WorktreeManager, _run_git

from .git_repo_support import (
    clone_repo,
    commit_all,
    init_repo,
    requires_git,
    run_git_sync,
    seed_remote,
    write_file,
)


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    reset_caches()
    monkeypatch.setattr(_wt, "_fetch_timed_out_at", {})
    yield
    reset_caches()


class _StallingGit:
    """Runs every git command for real except ``stalled``, which answers as
    if it hit its timeout. Records the timeout each command was given."""

    def __init__(self, stalled: str) -> None:
        self.stalled = stalled
        self.timeouts: dict[str, float] = {}
        self.calls: list[str] = []
        self._real = _wt.run_capped

    def __call__(self, argv, **kwargs) -> CmdResult:
        command = argv[1]
        self.timeouts[command] = kwargs["timeout"]
        self.calls.append(command)
        if command == self.stalled:
            return CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True)
        return self._real(argv, **kwargs)


class _KilledWorktreeAdd:
    """Runs every git command for real, but reports the first ``worktree add``
    whose argv matches ``with_b`` as timed out after it ran: the state a kill
    part way through leaves behind (directory and branch may exist)."""

    def __init__(self, *, with_b: bool, locked: bool = False) -> None:
        self.with_b = with_b
        self.locked = locked
        self.killed = False
        self._real = _wt.run_capped

    def __call__(self, argv, **kwargs) -> CmdResult:
        result = self._real(argv, **kwargs)
        is_add = argv[1:3] == ["worktree", "add"]
        if is_add and not self.killed and ("-b" in argv) == self.with_b:
            self.killed = True
            if self.locked and result.rc == 0:
                # git holds this lock while it checks the worktree out.
                wt_dir = argv[argv.index("--no-track") + 1]
                self._real(
                    [argv[0], "worktree", "lock", "--reason", "initializing", wt_dir],
                    **kwargs,
                )
            return CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True)
        return result


def _branch_exists(repo: str, branch: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo, capture_output=True,
    ).returncode == 0


def _rev(repo: str, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _worktree_paths(repo: str) -> list[str]:
    porcelain = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    return [line[len("worktree "):] for line in porcelain.splitlines() if line.startswith("worktree ")]


def _manager(tmp_path) -> WorktreeManager:
    return WorktreeManager(
        worktrees_dir=str(tmp_path / "worktrees"),
        branch_prefix="feature/mobaide",
        main_branch="main",
    )


@requires_git
class TestRunGit:
    async def test_runs_in_cwd_even_when_the_agent_inherits_git_dir(
        self, tmp_path, monkeypatch,
    ):
        # A hook or a parent git process can leave GIT_DIR in the agent's
        # environment; passed on, every worktree command would act on that repo.
        repo = init_repo(str(tmp_path / "project"))
        other = init_repo(str(tmp_path / "other"))
        monkeypatch.setenv("GIT_DIR", os.path.join(other, ".git"))

        rc, stdout, stderr = await _run_git("rev-parse", "--show-toplevel", cwd=repo)

        assert (rc, stderr) == (0, "")
        assert os.path.samefile(stdout.strip(), repo)

    async def test_a_failure_returns_git_rc_and_decoded_stderr(self, tmp_path):
        rc, stdout, stderr = await _run_git("rev-parse", "--verify", "nope", cwd=str(tmp_path))

        assert rc == 128
        assert stdout == ""
        assert isinstance(stderr, str) and "not a git repository" in stderr

    async def test_a_missing_cwd_runs_no_git(self, tmp_path, monkeypatch):
        stalling = _StallingGit("status")
        monkeypatch.setattr(_wt, "run_capped", stalling)

        result = await _run_git("status", cwd=str(tmp_path / "gone"))

        assert result == (1, "", f"Directory does not exist: {tmp_path / 'gone'}")
        assert stalling.timeouts == {}


@requires_git
class TestStalledGitDuringCreate:
    async def test_a_stalled_fetch_branches_from_the_last_fetched_state(
        self, tmp_path, monkeypatch,
    ):
        remote = seed_remote(str(tmp_path))
        local = clone_repo(remote, str(tmp_path / "local"))
        cached = _rev(local, "origin/main")
        seed = str(tmp_path / "seed")
        write_file(seed, "new.txt", b"newer\n")
        commit_all(seed, "newer on the remote")
        run_git_sync(seed, "push", "-q", "origin", "main")
        stalling = _StallingGit("fetch")
        monkeypatch.setattr(_wt, "run_capped", stalling)

        result = await _manager(tmp_path).create_worktree("gh-1", local, branch_name="gh-1")

        assert result.is_new is True
        assert _rev(result.path, "HEAD") == cached
        assert stalling.timeouts["fetch"] == _wt._FETCH_TIMEOUT
        assert stalling.timeouts["worktree"] == _wt._GIT_TIMEOUT

    @pytest.mark.parametrize("locked", [False, True], ids=["unlocked", "locked-initializing"])
    async def test_a_killed_new_branch_add_is_undone_and_not_retried(
        self, tmp_path, monkeypatch, locked,
    ):
        repo = init_repo(str(tmp_path / "project"))
        killer = _KilledWorktreeAdd(with_b=True, locked=locked)
        monkeypatch.setattr(_wt, "run_capped", killer)
        manager = _manager(tmp_path)

        with pytest.raises(WorktreeCreationError) as excinfo:
            await manager.create_worktree("gh-2", repo, branch_name="gh-2")

        assert excinfo.value.stderr == "git worktree timed out after 60 s"
        assert not os.path.exists(excinfo.value.path)
        assert len(_worktree_paths(repo)) == 1
        assert not _branch_exists(repo, "gh-2")
        monkeypatch.setattr(_wt, "run_capped", killer._real)
        retry = await manager.create_worktree("gh-2", repo, branch_name="gh-2")
        assert retry.is_new is True
        assert retry.path == excinfo.value.path

    async def test_a_killed_existing_branch_add_is_undone_and_keeps_the_branch(
        self, tmp_path, monkeypatch,
    ):
        repo = init_repo(str(tmp_path / "project"))
        run_git_sync(repo, "branch", "gh-3")
        killer = _KilledWorktreeAdd(with_b=False)
        monkeypatch.setattr(_wt, "run_capped", killer)
        manager = _manager(tmp_path)

        with pytest.raises(WorktreeCreationError) as excinfo:
            await manager.create_worktree("gh-3", repo, branch_name="gh-3")

        assert excinfo.value.stderr == "git worktree timed out after 60 s"
        assert not os.path.exists(excinfo.value.path)
        assert _branch_exists(repo, "gh-3")
        monkeypatch.setattr(_wt, "run_capped", killer._real)
        retry = await manager.create_worktree("gh-3", repo, branch_name="gh-3")
        assert retry.is_new is True

    async def test_a_killed_new_branch_add_keeps_a_branch_that_existed_before(
        self, tmp_path, monkeypatch,
    ):
        repo = init_repo(str(tmp_path / "project"))
        run_git_sync(repo, "branch", "gh-4")
        before = _rev(repo, "refs/heads/gh-4")
        monkeypatch.setattr(_wt, "run_capped", _KilledWorktreeAdd(with_b=True))

        with pytest.raises(WorktreeCreationError) as excinfo:
            await _manager(tmp_path).create_worktree("gh-4", repo, branch_name="gh-4")

        assert excinfo.value.stderr == "git worktree timed out after 60 s"
        assert not os.path.exists(excinfo.value.path)
        assert _rev(repo, "refs/heads/gh-4") == before


@requires_git
class TestFetchBackoff:
    async def test_a_timed_out_fetch_is_skipped_until_the_backoff_ends(
        self, tmp_path, monkeypatch,
    ):
        remote = seed_remote(str(tmp_path))
        local = clone_repo(remote, str(tmp_path / "local"))
        stalling = _StallingGit("fetch")
        monkeypatch.setattr(_wt, "run_capped", stalling)
        manager = _manager(tmp_path)

        await manager.create_worktree("gh-5", local, branch_name="gh-5")
        assert stalling.calls.count("fetch") == 1

        second = await manager.create_worktree("gh-6", local, branch_name="gh-6")
        assert second.is_new is True
        assert stalling.calls.count("fetch") == 1

        for key in _wt._fetch_timed_out_at:
            _wt._fetch_timed_out_at[key] -= _wt._FETCH_BACKOFF + 1
        stalling.stalled = "none"
        await manager.create_worktree("gh-7", local, branch_name="gh-7")
        assert stalling.calls.count("fetch") == 2
        assert _wt._fetch_timed_out_at == {}

    async def test_the_backoff_is_per_repo(self, tmp_path, monkeypatch):
        remote = seed_remote(str(tmp_path))
        first = clone_repo(remote, str(tmp_path / "first"))
        second = clone_repo(remote, str(tmp_path / "second"))
        stalling = _StallingGit("fetch")
        monkeypatch.setattr(_wt, "run_capped", stalling)

        await _manager(tmp_path).create_worktree("gh-8", first, branch_name="gh-8")
        await _manager(tmp_path / "b").create_worktree("gh-9", second, branch_name="gh-9")

        assert stalling.calls.count("fetch") == 2

"""Real on-disk repositories for the session git status/diff tests.

Shared by ``test_git_changes_service.py``, ``test_git_changes_diff.py`` and
``test_git_session_router.py``. Setup goes through a synchronous ``git``
helper isolated from the developer's global config (same recipe as
``test_git_router.py``); the code under test never sees these helpers.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from mobaide.core.git_exec import CmdResult

GIT_BIN = shutil.which("git")
requires_git = pytest.mark.skipif(GIT_BIN is None, reason="git binary not on PATH")
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks and file modes need POSIX"
)


def run_git_sync(
    repo: str, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    env.setdefault("GIT_CONFIG_GLOBAL", os.path.devnull)
    env.setdefault("GIT_CONFIG_SYSTEM", os.path.devnull)
    return subprocess.run(
        [GIT_BIN, *args],
        cwd=repo,
        check=check,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def write_file(repo: str, rel: str, data: bytes) -> str:
    full = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    return full


def commit_all(repo: str, message: str) -> None:
    run_git_sync(repo, "add", "-A")
    run_git_sync(repo, "commit", "-m", message)


def init_repo(path: str, *, initial_commit: bool = True) -> str:
    os.makedirs(path, exist_ok=True)
    run_git_sync(path, "init", "-b", "main")
    run_git_sync(path, "config", "core.autocrlf", "false")
    run_git_sync(path, "config", "user.email", "test@example.com")
    run_git_sync(path, "config", "user.name", "Test")
    if initial_commit:
        write_file(path, "README.md", b"hello\n")
        write_file(path, "keep.txt", b"one\ntwo\nthree\n")
        commit_all(path, "initial")
    return path


def init_bare_remote(path: str) -> str:
    """An empty bare repository on ``main`` for the fetch/pull/push jobs to
    talk to over the local transport."""
    os.makedirs(path, exist_ok=True)
    run_git_sync(path, "init", "-q", "--bare", "-b", "main")
    return path


def seed_remote(tmp_root: str) -> str:
    """A bare remote at ``<tmp_root>/remote.git`` whose ``main`` carries the
    initial commit ``init_repo`` makes."""
    bare = init_bare_remote(os.path.join(tmp_root, "remote.git"))
    seed = init_repo(os.path.join(tmp_root, "seed"))
    run_git_sync(seed, "remote", "add", "origin", bare)
    run_git_sync(seed, "push", "-q", "-u", "origin", "main")
    return bare


def clone_repo(remote: str, path: str) -> str:
    """A clone of ``remote`` (``main`` tracking ``origin/main``) with the
    identity ``init_repo`` uses."""
    parent = os.path.dirname(path) or os.curdir
    os.makedirs(parent, exist_ok=True)
    run_git_sync(parent, "clone", "-q", remote, path)
    run_git_sync(path, "config", "core.autocrlf", "false")
    run_git_sync(path, "config", "user.email", "test@example.com")
    run_git_sync(path, "config", "user.name", "Test")
    return path


def make_conflict_branches(repo: str) -> None:
    """``main`` and ``other`` each rewrite line 2 of ``keep.txt`` so any
    merge / rebase / cherry-pick between them stops with a conflict."""
    run_git_sync(repo, "checkout", "-b", "other")
    write_file(repo, "keep.txt", b"one\nOTHER\nthree\n")
    commit_all(repo, "other side")
    run_git_sync(repo, "checkout", "main")
    write_file(repo, "keep.txt", b"one\nMAIN\nthree\n")
    commit_all(repo, "main side")


def finish_merge_as_ours(repo: str) -> None:
    """``other`` merged into ``main`` with the conflict on ``keep.txt`` resolved
    as "ours" and committed: the index keeps the merge's resolve-undo record,
    and its stage 2 is still ``HEAD``'s blob of ``keep.txt``."""
    make_conflict_branches(repo)
    run_git_sync(repo, "merge", "other", check=False)
    run_git_sync(repo, "checkout", "--ours", "--", "keep.txt")
    run_git_sync(repo, "add", "keep.txt")
    run_git_sync(repo, "commit", "-q", "--no-edit")


def start_failing_am_after_a_merge_as_ours(repo: str, patch_dir: str) -> None:
    """:func:`finish_merge_as_ours`, then ``git am`` (no ``-3``) of ``other``'s
    commit, which no longer applies: the session stays open in
    ``rebase-apply`` without ever rewriting the index, so the merge's
    resolve-undo record is still there."""
    finish_merge_as_ours(repo)
    run_git_sync(repo, "format-patch", "-q", "-1", "-o", patch_dir, "other")
    (patch,) = os.listdir(patch_dir)
    run_git_sync(repo, "am", os.path.join(patch_dir, patch), check=False)


def stop_rebase_on_exec_after_a_pick_as_ours(repo: str) -> None:
    """``other`` replayed onto ``main`` with a failing ``exec`` after the pick:
    the pick's conflict on ``keep.txt`` is resolved as "ours" and committed by
    ``--continue``, then the rebase stops on the ``exec``. ``REBASE_HEAD`` is
    gone, and the pick's resolve-undo record is still in the index."""
    make_conflict_branches(repo)
    run_git_sync(repo, "checkout", "-q", "other")
    write_file(repo, "extra.txt", b"extra\n")
    run_git_sync(repo, "add", "extra.txt")
    run_git_sync(repo, "commit", "-q", "--amend", "--no-edit")
    run_git_sync(repo, "rebase", "--no-autostash", "-x", "false", "main", check=False)
    run_git_sync(repo, "checkout", "--ours", "--", "keep.txt")
    run_git_sync(repo, "add", "keep.txt")
    run_git_sync(repo, "-c", "core.editor=true", "rebase", "--continue", check=False)


def make_modify_delete_branches(repo: str, *, ours_deletes: bool) -> None:
    """``main`` and ``other`` disagree on ``keep.txt``: one side deletes it,
    the other rewrites line 2, so merging ``other`` into ``main`` stops with a
    ``DU`` (``ours_deletes``) or ``UD`` conflict instead of a ``UU`` one."""
    run_git_sync(repo, "checkout", "-b", "other")
    if ours_deletes:
        write_file(repo, "keep.txt", b"one\nOTHER\nthree\n")
    else:
        os.remove(os.path.join(repo, "keep.txt"))
    commit_all(repo, "other side")
    run_git_sync(repo, "checkout", "main")
    if ours_deletes:
        os.remove(os.path.join(repo, "keep.txt"))
    else:
        write_file(repo, "keep.txt", b"one\nMAIN\nthree\n")
    commit_all(repo, "main side")


def add_submodule(repo: str, tmp_root: str, name: str = "sub") -> str:
    """Stage a submodule at ``<repo>/<name>`` sourced from a sibling repo."""
    source = init_repo(os.path.join(tmp_root, f"{name}-src"))
    run_git_sync(
        repo, "-c", "protocol.file.allow=always", "submodule", "add", source, name
    )
    return source


def make_submodule_conflict(repo: str, tmp_root: str, name: str = "sub") -> None:
    """``main`` and ``other`` each move the submodule at ``<name>`` to a commit
    the other side lacks, and ``other`` is merged into ``main``: the merge
    stops with gitlink entries at stages 1-3 of ``<name>``. The submodule's
    worktree stays on ``main``'s commit, so ``git add <name>`` resolves the
    conflict as "ours"."""
    add_submodule(repo, tmp_root, name)
    commit_all(repo, "adds the submodule")
    sub = os.path.join(repo, name)
    run_git_sync(repo, "checkout", "-q", "-b", "other")
    run_git_sync(sub, "checkout", "-q", "-b", "other-side")
    write_file(sub, "keep.txt", b"one\nOTHER\nthree\n")
    commit_all(sub, "other side")
    commit_all(repo, "other moves the submodule")
    run_git_sync(repo, "checkout", "-q", "main")
    run_git_sync(sub, "checkout", "-q", "-b", "main-side", "main")
    write_file(sub, "keep.txt", b"one\nMAIN\nthree\n")
    commit_all(sub, "main side")
    commit_all(repo, "main moves the submodule")
    run_git_sync(repo, "merge", "other", check=False)


def norm_path(path: str) -> str:
    """The form ``resolve_repo`` reports: realpath, forward slashes on Windows only."""
    resolved = os.path.realpath(path)
    if sys.platform == "win32":
        resolved = resolved.replace("\\", "/")
    return resolved


def stub_git(monkeypatch, result: CmdResult, version=(2, 50, 0)) -> None:
    """Replace the service's runner seams with canned answers (no git needed)."""
    from mobaide.agent.git import changes_service

    async def fake_version():
        return version

    async def fake_run(args, **_kwargs):
        return result

    monkeypatch.setattr(changes_service, "git_version", fake_version)
    monkeypatch.setattr(changes_service, "run_git", fake_run)


@pytest.fixture
def repo(tmp_path) -> str:
    """A committed repo on ``main`` with ``README.md`` and a 3-line ``keep.txt``."""
    if GIT_BIN is None:
        pytest.skip("git binary not on PATH")
    return init_repo(str(tmp_path / "repo"))

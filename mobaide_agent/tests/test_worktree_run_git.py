"""Tests for ``mobaide.linear.worktree._run_git``.

The Windows pairing fix installs ``WindowsSelectorEventLoopPolicy``
at ``main()`` top, which makes ``asyncio.create_subprocess_exec`` raise
``NotImplementedError`` on Windows (the Selector loop's subprocess
support is Proactor-only). ``_run_git`` was therefore migrated from
``asyncio.create_subprocess_exec`` to
``asyncio.to_thread(subprocess.run, ...)`` so every Linear / auto-scan
git operation keeps working on Windows under either event-loop policy.

These tests pin the new contract:

* ``subprocess.run`` is the underlying call site (not
  ``asyncio.create_subprocess_exec``).
* ``["git", *args]`` is passed as ``argv``, with ``cwd`` threaded
  through and ``capture_output=True`` set.
* The coroutine still returns the ``(returncode, stdout_str,
  stderr_str)`` tuple shape every caller in ``WorktreeManager`` expects.

We do NOT test the legacy ``create_subprocess_exec`` path — it is gone,
and tests pinned to it would obstruct a future loop migration. The
existing ``test_worktree.py`` already covers the ``WorktreeManager``
methods that call ``_run_git`` (they mock ``_run_git`` itself); this
file covers ``_run_git``'s own implementation.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

import mobaide.linear.worktree as _wt
from mobaide.linear.worktree import _run_git


class _SubprocessRunRecorder:
    """Records every ``subprocess.run`` call and returns a canned
    ``CompletedProcess``-shaped object.

    Using ``SimpleNamespace`` with the three attributes ``_run_git``
    reads (``returncode``, ``stdout``, ``stderr``) is sufficient — we
    do not need to construct a real ``CompletedProcess`` instance, and
    avoiding it keeps the test independent of Python's stdlib changes
    around that dataclass.
    """

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args, **kwargs) -> SimpleNamespace:
        self.calls.append({"args": args, "kwargs": kwargs})
        return SimpleNamespace(
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class TestRunGitSubprocessContract:
    """Pin the ``asyncio.to_thread(subprocess.run, ...)`` call shape.

    The Windows event-loop policy installed by ``main()`` makes
    ``asyncio.create_subprocess_exec`` raise ``NotImplementedError``.
    The migration to ``subprocess.run`` keeps git operations working
    on every loop policy. If a future refactor reverts to
    ``create_subprocess_exec`` the test suite must catch it before
    Linear / auto-scan break on Windows — these assertions are the
    canary.
    """

    async def test_subprocess_run_invoked_with_git_argv_and_kwargs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        # ``cwd`` must point at an existing directory so the production
        # ``os.path.isdir`` guard does not short-circuit before
        # ``subprocess.run`` is ever reached. ``tmp_path`` is the
        # pytest-managed temp directory; it always exists for the test
        # duration.
        cwd = str(tmp_path)
        recorder = _SubprocessRunRecorder(
            returncode=0,
            stdout=b"git version 2.45.1\n",
            stderr=b"",
        )
        # Patch ``subprocess.run`` on the ``mobaide.linear.worktree``
        # module's bound ``subprocess`` reference rather than the
        # global stdlib symbol — this is the import the production
        # ``asyncio.to_thread(subprocess.run, ...)`` resolves through,
        # and patching it at the module level keeps the production code
        # path identical (``to_thread`` is invoked, the thread executor
        # is exercised, the only substituted leaf is the syscall
        # itself).
        monkeypatch.setattr(_wt.subprocess, "run", recorder)

        rc, stdout, stderr = await _run_git("--version", cwd=cwd)

        # ``subprocess.run`` was reached — proves the production code is
        # not falling back to ``create_subprocess_exec``.
        assert len(recorder.calls) == 1, (
            f"expected exactly one subprocess.run call; got "
            f"{len(recorder.calls)}: {recorder.calls!r}"
        )

        call = recorder.calls[0]
        # The first positional MUST be ``["git", *args]``. A regression
        # that strips the literal ``"git"`` prefix or forwards
        # ``*args`` raw (which would not run git at all) is exactly
        # what this assertion catches.
        assert call["args"][0] == ["git", "--version"], (
            f"subprocess.run must be called with ['git', *args] as "
            f"argv (so it actually invokes git, not the first arg as "
            f"a binary); got {call['args']!r}"
        )

        # ``cwd`` MUST be threaded through — every WorktreeManager
        # caller relies on git running in the project root, not in the
        # agent's CWD.
        assert call["kwargs"].get("cwd") == cwd, (
            f"subprocess.run must receive cwd={cwd!r} via kwargs so "
            f"git runs in the project root; got "
            f"cwd={call['kwargs'].get('cwd')!r}"
        )

        # ``capture_output=True`` is what gives us bytes back on stdout
        # / stderr (otherwise they'd inherit the parent stdio and the
        # caller's tuple would carry empty strings even on success).
        assert call["kwargs"].get("capture_output") is True, (
            f"subprocess.run must be called with capture_output=True so "
            f"stdout/stderr come back as bytes for the caller; got "
            f"capture_output={call['kwargs'].get('capture_output')!r}"
        )

        # Return-shape contract — caller code in ``WorktreeManager``
        # unpacks the tuple as ``(rc, stdout, stderr)``.
        assert rc == 0, f"expected rc=0 on a successful run; got {rc!r}"
        assert stdout == "git version 2.45.1\n", (
            f"stdout must be the decoded bytes from subprocess.run; got "
            f"{stdout!r}"
        )
        assert stderr == "", (
            f"stderr must be the decoded empty bytes from subprocess.run; "
            f"got {stderr!r}"
        )

    async def test_returns_tuple_with_decoded_stdout_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        # The contract is ``(int, str, str)`` — ``WorktreeManager`` does
        # ``rc, stdout, stderr = await _run_git(...)`` and then runs
        # ``.strip()`` on the strings. If ``_run_git`` ever leaked raw
        # bytes that would crash with ``AttributeError: 'bytes' object
        # has no attribute 'strip'`` at the worktree.add error path —
        # which is exactly the production code path the original
        # Windows hang masks. Pin the decode here.
        cwd = str(tmp_path)
        recorder = _SubprocessRunRecorder(
            returncode=128,
            stdout=b"some stdout chunk",
            stderr=b"fatal: not a git repository\n",
        )
        monkeypatch.setattr(_wt.subprocess, "run", recorder)

        rc, stdout, stderr = await _run_git(
            "rev-parse", "--show-toplevel", cwd=cwd,
        )

        assert isinstance(rc, int)
        assert isinstance(stdout, str), (
            f"stdout must be a decoded str (not bytes); got "
            f"{type(stdout).__name__}"
        )
        assert isinstance(stderr, str), (
            f"stderr must be a decoded str (not bytes); got "
            f"{type(stderr).__name__}"
        )
        assert rc == 128
        assert "some stdout chunk" in stdout
        assert "fatal: not a git repository" in stderr

        # ``["git", "rev-parse", "--show-toplevel"]`` is the exact argv
        # the production code threads through — a regression that
        # drops or reorders args would fail this assertion.
        assert recorder.calls[0]["args"][0] == [
            "git", "rev-parse", "--show-toplevel",
        ]

    async def test_returns_error_tuple_when_cwd_does_not_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        # The production guard at the top of ``_run_git`` short-circuits
        # to ``(1, "", "Directory does not exist: {cwd}")`` BEFORE
        # ``subprocess.run`` runs. Pin both halves of the contract:
        # the rc=1 tuple AND the no-syscall behaviour — otherwise a
        # spurious ``git`` call against a path that should never have
        # been opened could fire (e.g. against the user's home dir if
        # the call shape were changed).
        nonexistent = str(tmp_path / "definitely-not-here")
        # Sanity: the path must NOT exist for this test to mean
        # anything. tmp_path is fresh per test, so this is safe.
        assert not (tmp_path / "definitely-not-here").exists()

        recorder = _SubprocessRunRecorder()
        monkeypatch.setattr(_wt.subprocess, "run", recorder)

        rc, stdout, stderr = await _run_git(
            "status", "--porcelain", cwd=nonexistent,
        )

        assert rc == 1, (
            f"missing-cwd guard must return rc=1; got {rc!r}"
        )
        assert stdout == "", f"stdout must be empty on the guard path; got {stdout!r}"
        assert "Directory does not exist" in stderr, (
            f"stderr must surface the missing-cwd reason so callers can "
            f"log it; got {stderr!r}"
        )
        # The guard MUST prevent the subprocess from running — calling
        # subprocess.run with a nonexistent cwd would crash with
        # FileNotFoundError, which is what the guard exists to avoid.
        assert recorder.calls == [], (
            f"subprocess.run must NOT be invoked when cwd does not "
            f"exist; got {len(recorder.calls)} call(s): "
            f"{recorder.calls!r}"
        )


class TestRunGitDoesNotUseCreateSubprocessExec:
    """Negative regression guard for the subprocess.run migration.

    The original Proactor-on-Windows recv hang prompted the migration
    to ``WindowsSelectorEventLoopPolicy``, which then required
    ``_run_git`` to leave ``asyncio.create_subprocess_exec`` behind.
    Pin that the symbol is no longer reached so a future revert can't
    silently re-introduce the ``NotImplementedError`` on Windows.
    """

    async def test_create_subprocess_exec_is_not_invoked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        cwd = str(tmp_path)
        called = {"n": 0}

        async def _explode(*_args, **_kwargs):
            # If production calls this, the count will go up and the
            # final assertion will fail. We do NOT raise from here
            # because the production code path also has ``except`` /
            # ``finally`` blocks that could swallow a generic exception
            # and produce a misleading test failure.
            called["n"] += 1

            class _NeverAwaited:
                pass

            return _NeverAwaited()

        # Hook the asyncio symbol — even though the production code no
        # longer imports it via ``from asyncio import
        # create_subprocess_exec``, we cover the most likely revert
        # shape (``asyncio.create_subprocess_exec`` via the bound
        # ``asyncio`` import in ``mobaide.linear.worktree``).
        monkeypatch.setattr(
            _wt.asyncio, "create_subprocess_exec", _explode, raising=False,
        )

        recorder = _SubprocessRunRecorder(returncode=0)
        monkeypatch.setattr(_wt.subprocess, "run", recorder)

        await _run_git("--version", cwd=cwd)

        assert called["n"] == 0, (
            "asyncio.create_subprocess_exec must NOT be reached — it "
            "raises NotImplementedError under "
            "WindowsSelectorEventLoopPolicy, which is the whole "
            "reason the migration to subprocess.run + "
            "asyncio.to_thread exists"
        )
        # And subprocess.run MUST have fired, otherwise the test could
        # pass on a no-op refactor that bypassed both paths.
        assert len(recorder.calls) == 1

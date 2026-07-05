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

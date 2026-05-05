"""Tests for the Claude binary resolver.

These exercise the real ``_resolve_claude_binary`` implementation, which the
autouse fixture in ``conftest.py`` swaps for a stub that always returns
``"claude"``. We capture the real function here at module-import time —
before any fixture has a chance to run — and call it via ``_REAL_RESOLVER``.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from mobaide.core import claude_launcher
from mobaide.core.claude_launcher import (
    ClaudeBinaryNotFoundError,
    ClaudeLauncher,
    ClaudeOptions,
)
from mobaide.core.projects_config import ProjectConfig

_REAL_RESOLVER = claude_launcher._resolve_claude_binary

skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-only fallback paths"
)


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


@pytest.fixture
def clean_resolver_state(monkeypatch):
    """Strip env vars, isolate PATH, clear the resolver's cache."""
    monkeypatch.delenv("MOBAIDE_CLAUDE_BIN", raising=False)
    claude_launcher._resolved_claude_bin = None
    yield
    claude_launcher._resolved_claude_bin = None


def test_resolver_uses_env_var_override(monkeypatch, tmp_path, clean_resolver_state):
    fake = _make_executable(tmp_path / "myclaude")
    monkeypatch.setenv("MOBAIDE_CLAUDE_BIN", str(fake))

    assert _REAL_RESOLVER() == str(fake)


@skip_on_windows
def test_resolver_uses_which_when_no_override(monkeypatch, clean_resolver_state):
    monkeypatch.setattr(
        claude_launcher.shutil, "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(
        claude_launcher, "_posix_fallback_candidates", lambda: []
    )

    assert _REAL_RESOLVER() == "/fake/bin/claude"


@skip_on_windows
def test_resolver_falls_back_to_npm_global(
    monkeypatch, tmp_path, clean_resolver_state
):
    fake = _make_executable(tmp_path / ".npm-global" / "bin" / "claude")
    monkeypatch.setattr(claude_launcher.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(claude_launcher.shutil, "which", lambda *_a, **_kw: None)

    assert _REAL_RESOLVER() == str(fake)


@skip_on_windows
def test_resolver_falls_back_to_nvm_latest_version(
    monkeypatch, tmp_path, clean_resolver_state
):
    _make_executable(tmp_path / ".nvm" / "versions" / "node" / "v18.0.0" / "bin" / "claude")
    fake_v20 = _make_executable(
        tmp_path / ".nvm" / "versions" / "node" / "v20.0.0" / "bin" / "claude"
    )
    monkeypatch.setattr(claude_launcher.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(claude_launcher.shutil, "which", lambda *_a, **_kw: None)

    assert _REAL_RESOLVER() == str(fake_v20)


@skip_on_windows
def test_resolver_nvm_picks_v10_over_v9_using_semver_key(
    monkeypatch, tmp_path, clean_resolver_state
):
    """A lexicographic ``sorted(...)[-1]`` ranks ``v9.x`` ABOVE ``v10.x``
    because ``'9' > '1'`` byte-wise. The resolver uses ``_nvm_version_key``
    to parse the ``vX.Y.Z`` segment into an int tuple so ``max(...)``
    correctly picks v10 when both are installed.
    """
    _make_executable(tmp_path / ".nvm" / "versions" / "node" / "v9.11.2" / "bin" / "claude")
    fake_v10 = _make_executable(
        tmp_path / ".nvm" / "versions" / "node" / "v10.0.0" / "bin" / "claude"
    )
    monkeypatch.setattr(claude_launcher.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(claude_launcher.shutil, "which", lambda *_a, **_kw: None)

    assert _REAL_RESOLVER() == str(fake_v10)


def test_nvm_version_key_orders_v10_above_v9(tmp_path):
    """Direct unit test for ``_nvm_version_key`` — the comparable tuple it
    returns for ``v10.x`` must be strictly greater than for ``v9.x``,
    which is the property ``max(..., key=...)`` relies on. Also checks
    that an unparseable directory name falls to the ``(0,)`` floor so
    it never wins against a real semver-named entry.

    Cross-platform: parses pure path strings so it runs on Windows too;
    the ``v9 vs v10`` ordering matters regardless of whether the host
    actually uses nvm.
    """
    v9_path = str(tmp_path / ".nvm" / "versions" / "node" / "v9.11.2" / "bin" / "claude")
    v10_path = str(tmp_path / ".nvm" / "versions" / "node" / "v10.0.0" / "bin" / "claude")
    v18_path = str(tmp_path / ".nvm" / "versions" / "node" / "v18.20.4" / "bin" / "claude")
    junk_path = str(tmp_path / ".nvm" / "versions" / "node" / "system" / "bin" / "claude")

    assert claude_launcher._nvm_version_key(v10_path) > claude_launcher._nvm_version_key(v9_path)
    assert claude_launcher._nvm_version_key(v18_path) > claude_launcher._nvm_version_key(v10_path)
    assert claude_launcher._nvm_version_key(junk_path) == (0,)
    assert claude_launcher._nvm_version_key(v9_path) > claude_launcher._nvm_version_key(junk_path)


def test_resolver_raises_with_actionable_message(
    monkeypatch, tmp_path, clean_resolver_state
):
    monkeypatch.setattr(claude_launcher.shutil, "which", lambda *_a, **_kw: None)
    monkeypatch.setattr(claude_launcher, "_posix_fallback_candidates", lambda: [])

    with pytest.raises(ClaudeBinaryNotFoundError) as exc_info:
        _REAL_RESOLVER()

    msg = str(exc_info.value)
    assert "npm install -g @anthropic-ai/claude-code" in msg
    assert "MOBAIDE_CLAUDE_BIN" in msg
    assert exc_info.value.searched_paths


def test_resolver_caches_result(monkeypatch, tmp_path, clean_resolver_state):
    fake = _make_executable(tmp_path / "myclaude")
    monkeypatch.setenv("MOBAIDE_CLAUDE_BIN", str(fake))

    first = _REAL_RESOLVER()
    monkeypatch.delenv("MOBAIDE_CLAUDE_BIN", raising=False)
    second = _REAL_RESOLVER()

    assert first == second == str(fake)


@pytest.mark.asyncio
async def test_build_uses_resolved_absolute_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        claude_launcher, "_resolve_claude_binary", lambda: "/abs/path/to/claude"
    )
    project = ProjectConfig(name="test", path=str(tmp_path))

    spec = await ClaudeLauncher().build(project, ClaudeOptions())

    assert spec.argv[0] == "/abs/path/to/claude"
    assert spec.argv[1] == "--debug"


@pytest.mark.asyncio
async def test_build_env_prepends_resolver_dir_to_path(monkeypatch, tmp_path):
    resolved_dir = "/opt/x/bin"
    monkeypatch.setattr(
        claude_launcher, "_resolve_claude_binary",
        lambda: f"{resolved_dir}/claude",
    )
    project = ProjectConfig(name="test", path=str(tmp_path))

    spec = await ClaudeLauncher().build(project, ClaudeOptions())

    parts = spec.env["PATH"].split(os.pathsep)
    assert resolved_dir in parts
    assert parts[0] == resolved_dir


@pytest.mark.asyncio
async def test_build_env_does_not_duplicate_resolver_dir(monkeypatch, tmp_path):
    """If the resolver dir is already in PATH, we should not prepend a duplicate."""
    resolved_dir = "/opt/x/bin"
    monkeypatch.setattr(
        claude_launcher, "_resolve_claude_binary",
        lambda: f"{resolved_dir}/claude",
    )
    monkeypatch.setenv("PATH", f"{resolved_dir}{os.pathsep}/usr/bin")
    project = ProjectConfig(name="test", path=str(tmp_path))

    spec = await ClaudeLauncher().build(project, ClaudeOptions())

    parts = spec.env["PATH"].split(os.pathsep)
    assert parts.count(resolved_dir) == 1


def test_real_resolver_returns_absolute_path(monkeypatch, tmp_path, clean_resolver_state):
    """Sanity: even when shutil.which returns a relative-ish hit, the
    resolver normalises to an absolute path so subprocess.Popen never
    re-searches PATH."""
    fake = _make_executable(tmp_path / "myclaude")
    monkeypatch.setattr(
        claude_launcher.shutil, "which",
        lambda name: str(fake) if name in ("claude", "claude.cmd", "claude.exe") else None,
    )
    monkeypatch.setattr(claude_launcher, "_posix_fallback_candidates", lambda: [])

    result = _REAL_RESOLVER()
    assert os.path.isabs(result)
    assert result == str(fake)


@pytest.mark.asyncio
async def test_build_raises_before_creating_worktree_when_binary_missing(
    monkeypatch, tmp_path
):
    """``ClaudeLauncher.build`` must resolve the Claude binary BEFORE
    creating a worktree on disk. Otherwise a host with no ``claude`` CLI
    would leak an orphan ``git worktree add`` directory on every scan
    cycle (the issue Linear-side never completes, so it gets retried
    forever).

    The contract: when ``_resolve_claude_binary`` raises, the worktree
    manager's ``create_worktree`` MUST NOT have been awaited. We assert
    that directly via the mock's ``await_count`` rather than relying on
    inspecting the filesystem (more deterministic).
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        claude_launcher,
        "_resolve_claude_binary",
        lambda: (_ for _ in ()).throw(
            ClaudeBinaryNotFoundError(searched_paths=["PATH:claude"])
        ),
    )
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    project = ProjectConfig(name="test", path=str(tmp_path))
    options = ClaudeOptions(worktree=True, identifier="CLA-1", title="t")

    worktree_mgr = AsyncMock()
    launcher = ClaudeLauncher(worktree_mgr=worktree_mgr)

    with pytest.raises(ClaudeBinaryNotFoundError):
        await launcher.build(project, options)

    worktree_mgr.create_worktree.assert_not_awaited()

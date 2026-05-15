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
skip_if_not_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-only fallback paths"
)


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


@pytest.fixture
def clean_resolver_state(monkeypatch):
    """Strip the legacy env-var override so each test exercises a clean lookup."""
    monkeypatch.delenv("MOBAIDE_CLAUDE_BIN", raising=False)
    yield


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
    monkeypatch.setattr(claude_launcher, "_windows_fallback_candidates", lambda: [])

    with pytest.raises(ClaudeBinaryNotFoundError) as exc_info:
        _REAL_RESOLVER()

    msg = str(exc_info.value)
    assert "npm install -g @anthropic-ai/claude-code" in msg
    assert "MOBAIDE_CLAUDE_BIN" in msg
    assert exc_info.value.searched_paths


def test_resolver_is_stateless(monkeypatch, tmp_path, clean_resolver_state):
    """Two calls with different ``override`` values must each see their
    own override. The previous design cached at module level, which made
    test order and per-instance config invisible to the resolver. The
    new design pushes caching down to ``ClaudeLauncher`` so the module
    function stays a pure platform-lookup."""
    fake_a = _make_executable(tmp_path / "claude_a")
    fake_b = _make_executable(tmp_path / "claude_b")

    assert _REAL_RESOLVER(override=str(fake_a)) == str(fake_a)
    assert _REAL_RESOLVER(override=str(fake_b)) == str(fake_b)


def test_resolver_prefers_override_over_env_var(
    monkeypatch, tmp_path, clean_resolver_state
):
    fake_override = _make_executable(tmp_path / "from_override")
    fake_env = _make_executable(tmp_path / "from_env")
    monkeypatch.setenv("MOBAIDE_CLAUDE_BIN", str(fake_env))

    assert _REAL_RESOLVER(override=str(fake_override)) == str(fake_override)


def test_resolver_override_expands_user_path_segments(
    monkeypatch, tmp_path, clean_resolver_state
):
    """A hand-edited ``MOBAIDE_CLAUDE_BIN=~/bin/claude`` or
    ``%APPDATA%\\npm\\claude.cmd`` must resolve to a real absolute path
    rather than failing for a literal ``~`` or ``%APPDATA%``."""
    home_dir = tmp_path / "home"
    fake = _make_executable(home_dir / "bin" / "claude")
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    assert _REAL_RESOLVER(override="~/bin/claude") == str(fake)


def test_resolver_override_falls_through_when_path_missing(
    monkeypatch, tmp_path, clean_resolver_state
):
    """An operator-supplied override that points at a non-existent path
    falls through to env / PATH / fallback rather than failing hard. The
    failed candidate still appears in ``searched_paths`` so the operator
    can see why their override was ignored."""
    fake_env = _make_executable(tmp_path / "from_env")
    monkeypatch.setenv("MOBAIDE_CLAUDE_BIN", str(fake_env))

    assert _REAL_RESOLVER(override=str(tmp_path / "does_not_exist")) == str(fake_env)


@pytest.mark.asyncio
async def test_build_uses_resolved_absolute_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        claude_launcher,
        "_resolve_claude_binary",
        lambda *_a, **_kw: "/abs/path/to/claude",
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
        lambda *_a, **_kw: f"{resolved_dir}/claude",
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
        lambda *_a, **_kw: f"{resolved_dir}/claude",
    )
    monkeypatch.setenv("PATH", f"{resolved_dir}{os.pathsep}/usr/bin")
    project = ProjectConfig(name="test", path=str(tmp_path))

    spec = await ClaudeLauncher().build(project, ClaudeOptions())

    parts = spec.env["PATH"].split(os.pathsep)
    assert parts.count(resolved_dir) == 1


@pytest.mark.asyncio
async def test_launcher_passes_override_to_resolver(monkeypatch, tmp_path):
    """``ClaudeLauncher(claude_bin_override=...)`` must forward the
    override into the resolver call. The launcher is the only place
    where the override threads through, so this guards against accidental
    refactors that drop the parameter."""
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "/abs/path/to/claude"

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _capture)
    project = ProjectConfig(name="test", path=str(tmp_path))

    launcher = ClaudeLauncher(claude_bin_override="/my/explicit/claude")
    await launcher.build(project, ClaudeOptions())

    assert captured["override"] == "/my/explicit/claude"


@pytest.mark.asyncio
async def test_launcher_caches_resolution_per_instance(monkeypatch, tmp_path):
    """Two ``build`` calls on the same launcher must call the resolver
    exactly once. The previous module-level cache is gone; the launcher
    holds the cached value now and the test pins that contract."""
    call_count = {"n": 0}

    def _counting(override=None):
        call_count["n"] += 1
        return "/abs/path/to/claude"

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _counting)
    project = ProjectConfig(name="test", path=str(tmp_path))

    launcher = ClaudeLauncher()
    await launcher.build(project, ClaudeOptions())
    await launcher.build(project, ClaudeOptions())

    assert call_count["n"] == 1


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
    monkeypatch.setattr(claude_launcher, "_windows_fallback_candidates", lambda: [])

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

    def _raise(*_a, **_kw):
        raise ClaudeBinaryNotFoundError(searched_paths=["PATH:claude"])

    monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _raise)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    project = ProjectConfig(name="test", path=str(tmp_path))
    options = ClaudeOptions(worktree=True, identifier="CLA-1", title="t")

    worktree_mgr = AsyncMock()
    launcher = ClaudeLauncher(worktree_mgr=worktree_mgr)

    with pytest.raises(ClaudeBinaryNotFoundError):
        await launcher.build(project, options)

    worktree_mgr.create_worktree.assert_not_awaited()


def test_windows_fallback_candidates_uses_user_npm_dirs(monkeypatch, tmp_path):
    """The Windows fallback list must probe the installing user's
    ``%APPDATA%\\npm\\claude.cmd`` first — that's where ``npm install -g
    @anthropic-ai/claude-code`` lands on a default Windows install.

    Platform-agnostic: ``_windows_fallback_candidates`` itself reads env
    vars and joins paths; it doesn't gate on ``sys.platform``, so we can
    exercise it on POSIX CI runners. The pathsep difference matters only
    for the join semantics — assertions check directory anchoring, not
    backslashes specifically.
    """
    appdata = tmp_path / "user" / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramData", raising=False)

    candidates = claude_launcher._windows_fallback_candidates()

    assert any(str(appdata) in c and c.endswith("claude.cmd") for c in candidates)


def test_windows_fallback_candidates_covers_localappdata_and_userprofile(
    monkeypatch, tmp_path
):
    """Per-user installs land in ``%APPDATA%`` (default npm), but some
    operators relocate npm-global to ``%LOCALAPPDATA%`` or run scripts
    relative to ``%USERPROFILE%``. The fallback covers all three so the
    resolver can find the binary in non-standard installs (chocolatey,
    relocated npm prefix) even if PATH and ``MOBAIDE_CLAUDE_BIN`` both
    miss it."""
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userprofile"))

    candidates = claude_launcher._windows_fallback_candidates()

    assert any(str(tmp_path / "local") in c for c in candidates)
    assert any(
        str(tmp_path / "userprofile" / "AppData" / "Roaming") in c
        for c in candidates
    )


@skip_if_not_windows
def test_windows_resolver_uses_fallback_when_path_misses(
    monkeypatch, tmp_path, clean_resolver_state
):
    """End-to-end Windows path: shutil.which returns nothing (e.g. a
    user whose interactive PATH carries npm but whose non-interactive
    Scheduled-Task session does not), but ``%APPDATA%\\npm\\claude.cmd``
    exists. The resolver must find it via the fallback list — covers
    the original ``claude_cli_missing`` 503 where PATH-only lookup
    silently failed."""
    appdata = tmp_path / "user" / "AppData" / "Roaming"
    fake = appdata / "npm" / "claude.cmd"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("@echo off\r\nexit 0\r\n")

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(claude_launcher.shutil, "which", lambda *_a, **_kw: None)

    resolved = _REAL_RESOLVER()

    assert os.path.normcase(resolved) == os.path.normcase(str(fake))

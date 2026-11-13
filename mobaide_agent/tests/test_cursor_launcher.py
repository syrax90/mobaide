from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mobaide.core import cursor_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.cursor_launcher import (
    CursorBinaryNotFoundError,
    CursorLauncher,
    CursorOptions,
)
from mobaide.core.projects_config import ProjectConfig

# Captured before the autouse ``_pin_cursor_binary`` fixture swaps it, so the
# resolver tests below exercise the real implementation.
_REAL_CURSOR_RESOLVER = cursor_launcher._resolve_cursor_binary


def _make_project(path: str) -> ProjectConfig:
    return ProjectConfig(name="test", path=path)


@pytest.mark.asyncio
async def test_argv_default_is_bare_cursor_agent(tmp_path: Path):
    """Default options add no flag: Cursor then asks before each command."""
    spec = await CursorLauncher().build(_make_project(str(tmp_path)), CursorOptions())
    assert spec.argv == ["cursor-agent"]
    assert spec.cwd == str(tmp_path)
    assert spec.created_files == []


@pytest.mark.asyncio
async def test_argv_force_is_one_flag(tmp_path: Path):
    spec = await CursorLauncher().build(
        _make_project(str(tmp_path)), CursorOptions(force=True),
    )
    assert spec.argv == ["cursor-agent", "--force"]


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        cursor_launcher, "_resolve_cursor_binary",
        lambda *_a, **_k: os.path.join("/opt", "cursor", "bin", "cursor-agent"),
    )
    spec = await CursorLauncher().build(_make_project(str(tmp_path)), CursorOptions())
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "cursor", "bin")


def test_build_env_works_without_the_binary(tmp_path: Path, monkeypatch):
    """A chat session builds its env before Cursor is looked up, and reports
    a missing Cursor in its own way."""
    def _raise(*_a, **_kw):
        raise CursorBinaryNotFoundError(searched_paths=["PATH:cursor-agent"])

    monkeypatch.setattr(cursor_launcher, "_resolve_cursor_binary", _raise)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = CursorLauncher().build_env(AdHocLaunch(cwd=str(tmp_path)))

    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONIOENCODING"] == "utf-8"


class _RecordingWorktreeMgr:
    """Minimal WorktreeManager double mirroring the real signature; returns a
    real (existing) worktree dir so ``check_cwd_exists`` passes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_worktree(self, slug, project_root, *, branch_name=None, base_branch=None):
        from mobaide.linear.worktree import WorktreeResult

        self.calls.append(
            {"slug": slug, "branch_name": branch_name, "base_branch": base_branch}
        )
        wt = Path(project_root) / "wt" / slug
        wt.mkdir(parents=True, exist_ok=True)
        return WorktreeResult(path=str(wt), is_new=True, branch=branch_name or slug)


@pytest.mark.asyncio
async def test_cwd_worktree_on_git_project_resolves_to_worktree(tmp_path: Path):
    """worktree=True on a git project starts Cursor in the new worktree, made
    from the user's branch name and base branch."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await CursorLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)),
        CursorOptions(worktree=True, branch_name="feature/x", base_branch="origin/dev"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert mgr.calls[0]["base_branch"] == "origin/dev"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_cursor_prefix(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await CursorLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), CursorOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("cursor-session-")


@pytest.mark.asyncio
async def test_cwd_adhoc_worktree_is_ignored(tmp_path: Path):
    """An ad-hoc launch has no project, so worktree=True is ignored."""
    mgr = _RecordingWorktreeMgr()
    spec = await CursorLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        AdHocLaunch(cwd=str(tmp_path)), CursorOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise CursorBinaryNotFoundError(searched_paths=["PATH:cursor-agent"])

    monkeypatch.setattr(cursor_launcher, "_resolve_cursor_binary", _raise)
    with pytest.raises(CursorBinaryNotFoundError) as exc_info:
        await CursorLauncher().build(_make_project(str(tmp_path)), CursorOptions())
    # The REST and relay layers build ``cursor_cli_missing`` from this name.
    assert exc_info.value.cli_name == "cursor"


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "cursor-agent"

    monkeypatch.setattr(cursor_launcher, "_resolve_cursor_binary", _capture)
    await CursorLauncher(cursor_bin_override="/my/cursor-agent").build(
        _make_project(str(tmp_path)), CursorOptions(),
    )
    assert captured["override"] == "/my/cursor-agent"


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path):
    fake = _make_executable(tmp_path / "mycursor")
    monkeypatch.setenv("MOBAIDE_CURSOR_BIN", str(fake))
    assert _REAL_CURSOR_RESOLVER() == str(fake)


def test_real_resolver_looks_up_the_full_name_only(monkeypatch, tmp_path):
    """The installer also links the short name ``agent``; another program can
    own that name, so only ``cursor-agent`` on PATH counts."""
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "agent")
    monkeypatch.delenv("MOBAIDE_CURSOR_BIN", raising=False)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(cursor_launcher, "_posix_fallback_candidates", lambda: [])
    monkeypatch.setattr(cursor_launcher, "_windows_fallback_candidates", lambda: [])

    with pytest.raises(CursorBinaryNotFoundError):
        _REAL_CURSOR_RESOLVER()

    full = _make_executable(bin_dir / "cursor-agent")
    if os.name != "nt":
        assert _REAL_CURSOR_RESOLVER() == str(full)


def test_real_resolver_reports_every_place_it_looked(monkeypatch, tmp_path):
    """No Cursor anywhere: the error names the env value, PATH and each
    fallback, so the user can see which lookup to fix."""
    missing = tmp_path / "nowhere" / "cursor-agent"
    fallback = str(tmp_path / "local" / "cursor-agent")
    monkeypatch.setenv("MOBAIDE_CURSOR_BIN", str(missing))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(cursor_launcher, "_posix_fallback_candidates", lambda: [fallback])
    monkeypatch.setattr(cursor_launcher, "_windows_fallback_candidates", lambda: [fallback])

    with pytest.raises(CursorBinaryNotFoundError) as exc_info:
        _REAL_CURSOR_RESOLVER()

    searched = exc_info.value.searched_paths
    assert searched[0] == f"env:{missing}"
    assert "PATH:cursor-agent" in searched
    assert searched[-1] == f"fallback:{fallback}"


def test_posix_fallbacks_start_with_the_install_script_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    candidates = cursor_launcher._posix_fallback_candidates()

    assert candidates[0] == str(tmp_path / ".local" / "bin" / "cursor-agent")


def test_windows_fallbacks_are_the_install_script_launchers(monkeypatch):
    root = os.path.join("C:", "Users", "u", "AppData", "Local")
    monkeypatch.setenv("LOCALAPPDATA", root)

    assert cursor_launcher._windows_fallback_candidates() == [
        os.path.join(root, "cursor-agent", "cursor-agent.exe"),
        os.path.join(root, "cursor-agent", "cursor-agent.cmd"),
    ]


def test_windows_fallbacks_need_local_app_data(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert cursor_launcher._windows_fallback_candidates() == []

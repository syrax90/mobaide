from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from mobaide.core import pi_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.pi_launcher import PiBinaryNotFoundError, PiLauncher, PiOptions
from mobaide.core.projects_config import ProjectConfig

# Captured before the autouse ``_pin_pi_binary`` fixture swaps it, so the
# resolver tests below exercise the real implementation.
_REAL_PI_RESOLVER = pi_launcher._resolve_pi_binary


def _make_project(path: str) -> ProjectConfig:
    return ProjectConfig(name="test", path=path)


@pytest.mark.asyncio
async def test_argv_is_bare_pi_whatever_the_options(tmp_path: Path):
    """Pi has no approval mode and no launch flag: the model and the thinking
    level are picked inside the session."""
    spec = await PiLauncher().build(
        _make_project(str(tmp_path)), PiOptions(branch_name="ignored-without-worktree"),
    )
    assert spec.argv == ["pi"]
    assert spec.cwd == str(tmp_path)
    assert spec.created_files == []


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        pi_launcher, "_resolve_pi_binary",
        lambda *_a, **_k: os.path.join("/opt", "pi", "bin", "pi"),
    )
    spec = await PiLauncher().build(_make_project(str(tmp_path)), PiOptions())
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "pi", "bin")


def test_build_env_works_without_the_binary(tmp_path: Path, monkeypatch):
    """A chat session builds its env before Pi is looked up, and reports a
    missing Pi in its own way."""
    def _raise(*_a, **_kw):
        raise PiBinaryNotFoundError(searched_paths=["PATH:pi"])

    monkeypatch.setattr(pi_launcher, "_resolve_pi_binary", _raise)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = PiLauncher().build_env(AdHocLaunch(cwd=str(tmp_path)))

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
    """worktree=True on a git project starts Pi in the new worktree, made from
    the user's branch name and base branch."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await PiLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)),
        PiOptions(worktree=True, branch_name="feature/x", base_branch="origin/dev"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert mgr.calls[0]["base_branch"] == "origin/dev"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_pi_prefix(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await PiLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), PiOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("pi-session-")


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise PiBinaryNotFoundError(searched_paths=["PATH:pi"])

    monkeypatch.setattr(pi_launcher, "_resolve_pi_binary", _raise)
    with pytest.raises(PiBinaryNotFoundError) as exc_info:
        await PiLauncher().build(_make_project(str(tmp_path)), PiOptions())
    # The REST and relay layers build ``pi_cli_missing`` from this name.
    assert exc_info.value.cli_name == "pi"


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "pi"

    monkeypatch.setattr(pi_launcher, "_resolve_pi_binary", _capture)
    await PiLauncher(pi_bin_override="/my/pi").build(
        _make_project(str(tmp_path)), PiOptions(),
    )
    assert captured["override"] == "/my/pi"


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def _no_pi_on_this_host(monkeypatch, tmp_path: Path) -> Path:
    """A home folder with no Pi, an empty PATH and no npm folders."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("MOBAIDE_PI_BIN", raising=False)
    monkeypatch.setattr(pi_launcher, "posix_npm_fallbacks", lambda _name: [])
    monkeypatch.setattr(pi_launcher, "windows_npm_fallbacks", lambda _name: [])
    return home


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path):
    fake = _make_executable(tmp_path / "mypi")
    monkeypatch.setenv("MOBAIDE_PI_BIN", str(fake))
    assert _REAL_PI_RESOLVER() == str(fake)


def test_real_resolver_finds_pi_in_the_folder_of_its_own_installer(monkeypatch, tmp_path):
    """Pi's installer links its launcher into a folder on PATH, which a
    service that started before the install does not have."""
    home = _no_pi_on_this_host(monkeypatch, tmp_path)
    name = "pi.cmd" if sys.platform == "win32" else "pi"
    managed = _make_executable(home / ".pi" / "agent" / "bin" / name)

    assert _REAL_PI_RESOLVER() == str(managed)


def test_real_resolver_reports_every_place_it_looked(monkeypatch, tmp_path):
    """No Pi anywhere: the error names the env value, PATH and each fallback,
    so the user can see which lookup to fix."""
    home = _no_pi_on_this_host(monkeypatch, tmp_path)
    missing = tmp_path / "nowhere" / "pi"
    fallback = str(tmp_path / "npm" / "pi")
    monkeypatch.setenv("MOBAIDE_PI_BIN", str(missing))
    monkeypatch.setattr(pi_launcher, "posix_npm_fallbacks", lambda _name: [fallback])
    monkeypatch.setattr(pi_launcher, "windows_npm_fallbacks", lambda _name: [fallback])

    with pytest.raises(PiBinaryNotFoundError) as exc_info:
        _REAL_PI_RESOLVER()

    searched = exc_info.value.searched_paths
    name = "pi.cmd" if sys.platform == "win32" else "pi"
    assert searched[0] == f"env:{missing}"
    assert "PATH:pi" in searched
    assert searched[-2:] == [
        f"fallback:{home / '.pi' / 'agent' / 'bin' / name}",
        f"fallback:{fallback}",
    ]

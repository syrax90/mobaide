from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mobaide.core import copilot_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.copilot_launcher import (
    CopilotBinaryNotFoundError,
    CopilotLauncher,
    CopilotOptions,
)
from mobaide.core.projects_config import ProjectConfig

# Captured before the autouse ``_pin_copilot_binary`` fixture swaps it, so the
# resolver tests below exercise the real implementation.
_REAL_COPILOT_RESOLVER = copilot_launcher._resolve_copilot_binary


def _make_project(path: str) -> ProjectConfig:
    return ProjectConfig(name="test", path=path)


@pytest.mark.asyncio
async def test_argv_default_is_bare_copilot(tmp_path: Path):
    """Default options add no flag: Copilot then asks before each tool call."""
    spec = await CopilotLauncher().build(_make_project(str(tmp_path)), CopilotOptions())
    assert spec.argv == ["copilot"]
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_argv_allow_all_is_one_flag(tmp_path: Path):
    spec = await CopilotLauncher().build(
        _make_project(str(tmp_path)), CopilotOptions(allow_all=True),
    )
    assert spec.argv == ["copilot", "--allow-all"]


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        copilot_launcher, "_resolve_copilot_binary",
        lambda *_a, **_k: os.path.join("/opt", "gh", "bin", "copilot"),
    )
    spec = await CopilotLauncher().build(_make_project(str(tmp_path)), CopilotOptions())
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "gh", "bin")


def test_build_env_works_without_the_binary(tmp_path: Path, monkeypatch):
    """A chat session builds its env before Copilot is looked up, and reports
    a missing Copilot in its own way."""
    def _raise(*_a, **_kw):
        raise CopilotBinaryNotFoundError(searched_paths=["PATH:copilot"])

    monkeypatch.setattr(copilot_launcher, "_resolve_copilot_binary", _raise)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = CopilotLauncher().build_env(AdHocLaunch(cwd=str(tmp_path)))

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
    """worktree=True on a git project starts Copilot in the new worktree, made
    from the user's branch name and base branch."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await CopilotLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)),
        CopilotOptions(worktree=True, branch_name="feature/x", base_branch="origin/dev"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert mgr.calls[0]["base_branch"] == "origin/dev"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_copilot_prefix(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await CopilotLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), CopilotOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("copilot-session-")


@pytest.mark.asyncio
async def test_cwd_adhoc_worktree_is_ignored(tmp_path: Path):
    """An ad-hoc launch has no project, so worktree=True is ignored."""
    mgr = _RecordingWorktreeMgr()
    spec = await CopilotLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        AdHocLaunch(cwd=str(tmp_path)), CopilotOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise CopilotBinaryNotFoundError(searched_paths=["PATH:copilot"])

    monkeypatch.setattr(copilot_launcher, "_resolve_copilot_binary", _raise)
    with pytest.raises(CopilotBinaryNotFoundError) as exc_info:
        await CopilotLauncher().build(_make_project(str(tmp_path)), CopilotOptions())
    # The REST and relay layers build ``copilot_cli_missing`` from this name.
    assert exc_info.value.cli_name == "copilot"


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "copilot"

    monkeypatch.setattr(copilot_launcher, "_resolve_copilot_binary", _capture)
    await CopilotLauncher(copilot_bin_override="/my/copilot").build(
        _make_project(str(tmp_path)), CopilotOptions(),
    )
    assert captured["override"] == "/my/copilot"


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path):
    fake = _make_executable(tmp_path / "mycopilot")
    monkeypatch.setenv("MOBAIDE_COPILOT_BIN", str(fake))
    assert _REAL_COPILOT_RESOLVER() == str(fake)


def test_real_resolver_reports_every_place_it_looked(monkeypatch, tmp_path):
    """No Copilot anywhere: the error names the env value, PATH and each
    fallback, so the user can see which lookup to fix."""
    missing = tmp_path / "nowhere" / "copilot"
    fallback = str(tmp_path / "npm" / "copilot")
    monkeypatch.setenv("MOBAIDE_COPILOT_BIN", str(missing))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(copilot_launcher, "posix_npm_fallbacks", lambda _name: [fallback])
    monkeypatch.setattr(copilot_launcher, "windows_npm_fallbacks", lambda _name: [fallback])
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(CopilotBinaryNotFoundError) as exc_info:
        _REAL_COPILOT_RESOLVER()

    searched = exc_info.value.searched_paths
    assert searched[0] == f"env:{missing}"
    assert "PATH:copilot" in searched
    assert searched[-1] == f"fallback:{fallback}"

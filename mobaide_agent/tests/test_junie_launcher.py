from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mobaide.core import junie_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.junie_launcher import (
    JunieBinaryNotFoundError,
    JunieLauncher,
    JunieOptions,
)
from mobaide.core.workspaces_config import WorkspaceConfig

# Captured before the autouse ``_pin_junie_binary`` fixture swaps it, so the
# resolver tests below exercise the real implementation.
_REAL_JUNIE_RESOLVER = junie_launcher._resolve_junie_binary


def _make_workspace(path: str) -> WorkspaceConfig:
    return WorkspaceConfig(name="test", path=path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "argv"),
    [
        (JunieOptions(), ["junie"]),
        (JunieOptions(plan_mode=True), ["junie", "--plan"]),
        (JunieOptions(brave=True), ["junie", "--brave"]),
        (JunieOptions(plan_mode=True, brave=True), ["junie", "--plan", "--brave"]),
    ],
    ids=["default", "plan", "brave", "plan-and-brave"],
)
async def test_argv_has_a_flag_per_chosen_option(tmp_path: Path, options, argv):
    """Without ``--brave`` Junie asks before it runs a command."""
    spec = await JunieLauncher().build(_make_workspace(str(tmp_path)), options)

    assert spec.argv == argv
    assert spec.cwd == str(tmp_path)
    assert spec.created_files == []


def test_build_env_works_without_the_binary(tmp_path: Path, monkeypatch):
    """A chat session builds its env before Junie is looked up, and reports
    a missing Junie in its own way."""
    def _raise(*_a, **_kw):
        raise JunieBinaryNotFoundError(searched_paths=["PATH:junie"])

    monkeypatch.setattr(junie_launcher, "_resolve_junie_binary", _raise)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = JunieLauncher().build_env(AdHocLaunch(cwd=str(tmp_path)))

    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONIOENCODING"] == "utf-8"


class _RecordingWorktreeMgr:
    """Minimal WorktreeManager double mirroring the real signature; returns a
    real (existing) worktree dir so ``check_cwd_exists`` passes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_worktree(self, slug, workspace_root, *, branch_name=None, base_branch=None):
        from mobaide.linear.worktree import WorktreeResult

        self.calls.append(
            {"slug": slug, "branch_name": branch_name, "base_branch": base_branch}
        )
        wt = Path(workspace_root) / "wt" / slug
        wt.mkdir(parents=True, exist_ok=True)
        return WorktreeResult(path=str(wt), is_new=True, branch=branch_name or slug)


@pytest.mark.asyncio
async def test_cwd_worktree_on_git_workspace_resolves_to_worktree(tmp_path: Path):
    """worktree=True on a git workspace starts Junie in the new worktree, made
    from the user's branch name and base branch."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await JunieLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_workspace(str(tmp_path)),
        JunieOptions(worktree=True, branch_name="feature/x", base_branch="origin/dev"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert mgr.calls[0]["base_branch"] == "origin/dev"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise JunieBinaryNotFoundError(searched_paths=["PATH:junie"])

    monkeypatch.setattr(junie_launcher, "_resolve_junie_binary", _raise)
    with pytest.raises(JunieBinaryNotFoundError) as exc_info:
        await JunieLauncher().build(_make_workspace(str(tmp_path)), JunieOptions())
    # The REST and relay layers build ``junie_cli_missing`` from this name.
    assert exc_info.value.cli_name == "junie"


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "junie"

    monkeypatch.setattr(junie_launcher, "_resolve_junie_binary", _capture)
    await JunieLauncher(junie_bin_override="/my/junie").build(
        _make_workspace(str(tmp_path)), JunieOptions(),
    )
    assert captured["override"] == "/my/junie"


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path):
    fake = _make_executable(tmp_path / "myjunie")
    monkeypatch.setenv("MOBAIDE_JUNIE_BIN", str(fake))
    assert _REAL_JUNIE_RESOLVER() == str(fake)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fallbacks")
def test_real_resolver_finds_the_install_script_launcher_off_path(monkeypatch, tmp_path):
    """The agent's service PATH often lacks ``~/.local/bin``, where the
    install script puts ``junie``."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("MOBAIDE_JUNIE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    installed = _make_executable(tmp_path / ".local" / "bin" / "junie")

    assert _REAL_JUNIE_RESOLVER() == str(installed)


def test_windows_fallbacks_are_the_install_script_launchers(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = os.path.join(str(tmp_path), ".local", "bin")

    assert junie_launcher._windows_fallback_candidates() == [
        os.path.join(root, "junie.bat"),
        os.path.join(root, "junie.cmd"),
        os.path.join(root, "junie.exe"),
    ]


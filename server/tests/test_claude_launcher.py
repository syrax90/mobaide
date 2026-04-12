from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_mobile.core.claude_launcher import ClaudeLauncher, ClaudeOptions
from claude_mobile.core.projects_config import ProjectConfig


def _make_project(path: str, *, key: str | None = None, conda: str | None = None) -> ProjectConfig:
    return ProjectConfig(
        name="test", path=path, linear_api_key=key, conda_env=conda
    )


@pytest.mark.asyncio
async def test_argv_minimal_adds_bypass(tmp_path: Path):
    """Default options now force bypass mode — the mobile client cannot mediate
    approval dialogs, so sessions always start with permissions skipped."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)), ClaudeOptions()
    )
    assert spec.argv == ["claude", "--debug", "--dangerously-skip-permissions"]


@pytest.mark.asyncio
async def test_argv_plan_mode_with_default_skip_permissions(tmp_path: Path):
    """When plan_mode is on and skip_permissions is True (the default),
    both --permission-mode plan and --allow-dangerously-skip-permissions are passed."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(plan_mode=True),
    )
    assert spec.argv == [
        "claude", "--debug",
        "--permission-mode", "plan",
        "--allow-dangerously-skip-permissions",
    ]


@pytest.mark.asyncio
async def test_argv_plan_mode_without_skip_permissions(tmp_path: Path):
    """Plan mode with skip_permissions=False: only --permission-mode plan."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(plan_mode=True, skip_permissions=False),
    )
    assert spec.argv == ["claude", "--debug", "--permission-mode", "plan"]


@pytest.mark.asyncio
async def test_argv_no_plan_no_skip(tmp_path: Path):
    """Neither plan_mode nor skip_permissions: bare claude --debug."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(plan_mode=False, skip_permissions=False),
    )
    assert spec.argv == ["claude", "--debug"]


@pytest.mark.asyncio
async def test_argv_no_skip_with_initial_prompt(tmp_path: Path):
    """skip_permissions=False with an initial_prompt: no permission flags,
    but the ``--`` separator and prompt must still appear."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(skip_permissions=False, initial_prompt="run it"),
    )
    assert spec.argv == ["claude", "--debug", "--", "run it"]


@pytest.mark.asyncio
async def test_argv_all_flags(tmp_path: Path):
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(
            plan_mode=True,
            no_memory=True,
            initial_prompt="hello world",
        ),
    )
    assert spec.argv == [
        "claude",
        "--debug",
        "--permission-mode",
        "plan",
        "--allow-dangerously-skip-permissions",
        "--",
        "hello world",
    ]
    assert spec.env["CLAUDE_NO_MEMORY"] == "1"


@pytest.mark.asyncio
async def test_env_linear_key_injected(tmp_path: Path):
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path), key="lin_test_123"), ClaudeOptions()
    )
    assert spec.env["LINEAR_API_KEY"] == "lin_test_123"
    assert spec.env["PYTHONIOENCODING"] == "utf-8"


@pytest.mark.asyncio
async def test_env_no_linear_key_omitted(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)), ClaudeOptions()
    )
    assert "LINEAR_API_KEY" not in spec.env


@pytest.mark.asyncio
async def test_mcp_json_written_when_key_present(tmp_path: Path):
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path), key="lin_xyz"), ClaudeOptions()
    )
    mcp = tmp_path / ".mcp.json"
    assert mcp.exists()
    doc = json.loads(mcp.read_text())
    assert doc["mcpServers"]["linear"]["headers"]["Authorization"] == "Bearer lin_xyz"
    assert str(mcp) in spec.created_files

    settings = tmp_path / ".claude" / "settings.local.json"
    assert settings.exists()
    sdoc = json.loads(settings.read_text())
    assert sdoc["enabledPlugins"]["linear@claude-plugins-official"] is False


@pytest.mark.asyncio
async def test_mcp_json_skipped_without_key(tmp_path: Path):
    launcher = ClaudeLauncher()
    await launcher.build(_make_project(str(tmp_path)), ClaudeOptions())
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


@pytest.mark.asyncio
async def test_settings_local_not_overwritten(tmp_path: Path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    existing = settings_dir / "settings.local.json"
    existing.write_text('{"keep": "me"}')
    launcher = ClaudeLauncher()
    await launcher.build(
        _make_project(str(tmp_path), key="lin_xyz"), ClaudeOptions()
    )
    assert json.loads(existing.read_text()) == {"keep": "me"}


@pytest.mark.asyncio
async def test_cwd_resolution_no_worktree(tmp_path: Path):
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=False),
    )
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_resolution_with_worktree(tmp_path: Path):
    class _FakeWorktreeMgr:
        async def create_worktree(self, slug, project_root):
            from claude_mobile.linear.worktree import WorktreeResult

            wt = Path(project_root) / "wt" / slug
            wt.mkdir(parents=True, exist_ok=True)
            return WorktreeResult(path=str(wt), is_new=True, branch=slug)

    # CLA-20: launcher now skips worktree creation for non-git projects.
    (tmp_path / ".git").mkdir()
    launcher = ClaudeLauncher(worktree_mgr=_FakeWorktreeMgr())  # type: ignore[arg-type]
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=True, identifier="CLA-99", title="My Task"),
    )
    assert "cla-99-my-task" in spec.cwd

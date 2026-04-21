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
    approval dialogs, so sessions always start with permissions skipped. Max
    effort is also on by default."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)), ClaudeOptions()
    )
    assert spec.argv == [
        "claude", "--debug",
        "--dangerously-skip-permissions",
        "--effort", "max",
    ]


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
        "--effort", "max",
    ]


@pytest.mark.asyncio
async def test_argv_plan_mode_without_skip_permissions(tmp_path: Path):
    """Plan mode with skip_permissions=False: only --permission-mode plan."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(plan_mode=True, skip_permissions=False),
    )
    assert spec.argv == [
        "claude", "--debug",
        "--permission-mode", "plan",
        "--effort", "max",
    ]


@pytest.mark.asyncio
async def test_argv_no_plan_no_skip(tmp_path: Path):
    """Neither plan_mode nor skip_permissions: bare claude --debug plus
    the default --effort max."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(plan_mode=False, skip_permissions=False),
    )
    assert spec.argv == ["claude", "--debug", "--effort", "max"]


@pytest.mark.asyncio
async def test_argv_no_skip_with_initial_prompt(tmp_path: Path):
    """skip_permissions=False with an initial_prompt: no permission flags,
    but the ``--`` separator and prompt must still appear after --effort max."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(skip_permissions=False, initial_prompt="run it"),
    )
    assert spec.argv == [
        "claude", "--debug",
        "--effort", "max",
        "--", "run it",
    ]


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
        "--effort",
        "max",
        "--",
        "hello world",
    ]
    assert spec.env["CLAUDE_NO_MEMORY"] == "1"


@pytest.mark.asyncio
async def test_argv_max_effort_disabled_omits_flag(tmp_path: Path):
    """When max_effort=False, the --effort flag is not emitted at all, so the
    CLI falls back to its own default (xhigh for Opus 4.7, high elsewhere)."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(max_effort=False),
    )
    assert "--effort" not in spec.argv
    assert "max" not in spec.argv


@pytest.mark.asyncio
async def test_argv_max_effort_placed_before_prompt_separator(tmp_path: Path):
    """Regression guard: --effort max MUST appear before the ``--`` separator,
    otherwise ``max`` would be interpreted as part of the prompt passthrough."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(max_effort=True, initial_prompt="go"),
    )
    effort_idx = spec.argv.index("--effort")
    separator_idx = spec.argv.index("--")
    assert effort_idx < separator_idx
    assert spec.argv[effort_idx + 1] == "max"


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


# ---------------------------------------------------------------------------
# Pre-spawn cwd existence check (reconcile-project-path-format)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_cwd_missing_directory_raises_file_not_found(
    tmp_path: Path,
):
    """The launcher must fail fast with a clean ``FileNotFoundError`` when
    the project path does not exist — so the session router can surface a
    readable 400 to the mobile client instead of letting ``pywinpty`` emit
    a ``repr``-escaped path that the client renders with 4 backslashes."""
    missing = tmp_path / "does" / "not" / "exist"
    launcher = ClaudeLauncher()

    with pytest.raises(FileNotFoundError) as exc_info:
        await launcher.build(
            _make_project(str(missing)),
            ClaudeOptions(worktree=False),
        )

    # The ``filename`` attribute is the plain string the router wants to
    # interpolate via f-string — not ``str(exc)``, which embeds ``repr``.
    assert exc_info.value.filename == str(missing)
    assert exc_info.value.errno == 2


@pytest.mark.asyncio
async def test_resolve_cwd_existing_directory_returns_path(tmp_path: Path):
    """Regression: a real directory must pass the new existence gate."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=False),
    )
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_resolve_cwd_worktree_fallback_also_checks_existence(
    tmp_path: Path,
):
    """When worktree is requested for a non-git project the launcher falls
    back to ``project.path`` — the existence check must still apply on that
    branch so the failure is reported at the same clean point."""
    missing = tmp_path / "ghost"
    launcher = ClaudeLauncher()

    with pytest.raises(FileNotFoundError):
        await launcher.build(
            _make_project(str(missing)),
            ClaudeOptions(worktree=True),
        )

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mobaide.core.claude_launcher import AdHocLaunch, ClaudeLauncher, ClaudeOptions
from mobaide.core.projects_config import ProjectConfig


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
async def test_env_prompt_caching_1h_always_set(tmp_path: Path):
    """Every spawn MUST carry ENABLE_PROMPT_CACHING_1H=1 so the claude CLI
    opts into the 1-hour prompt-cache TTL (v2.1.108+)."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(_make_project(str(tmp_path)), ClaudeOptions())
    assert spec.env["ENABLE_PROMPT_CACHING_1H"] == "1"


@pytest.mark.asyncio
async def test_env_prompt_caching_1h_set_with_all_options(tmp_path: Path):
    """The preset survives alongside every other optional env key — Linear
    key, no_memory, conda env — so no caller path loses it."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(
        _make_project(str(tmp_path), key="lin_abc", conda="some_env"),
        ClaudeOptions(no_memory=True),
    )
    assert spec.env["ENABLE_PROMPT_CACHING_1H"] == "1"
    assert spec.env["CLAUDE_NO_MEMORY"] == "1"
    assert spec.env["LINEAR_API_KEY"] == "lin_abc"


@pytest.mark.asyncio
async def test_env_prompt_caching_1h_overrides_outer_environment(
    tmp_path: Path, monkeypatch,
):
    """The preset is authoritative: even if the operator has
    ENABLE_PROMPT_CACHING_1H=0 in their shell, the spawn env must still be
    forced to "1"."""
    monkeypatch.setenv("ENABLE_PROMPT_CACHING_1H", "0")
    launcher = ClaudeLauncher()
    spec = await launcher.build(_make_project(str(tmp_path)), ClaudeOptions())
    assert spec.env["ENABLE_PROMPT_CACHING_1H"] == "1"


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


class _FakeWorktreeMgr:
    """Test double capturing calls to ``create_worktree``.

    Mirrors the real signature (``slug``, ``project_root``, ``branch_name=``)
    so tests can assert what the launcher handed off.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_worktree(
        self, slug, project_root, *, branch_name=None,
    ):
        from mobaide.linear.worktree import WorktreeResult

        effective_branch = branch_name if branch_name is not None else slug
        self.calls.append(
            {"slug": slug, "project_root": project_root, "branch_name": branch_name}
        )
        wt = Path(project_root) / "wt" / slug
        wt.mkdir(parents=True, exist_ok=True)
        return WorktreeResult(
            path=str(wt), is_new=True, branch=effective_branch
        )


@pytest.mark.asyncio
async def test_cwd_resolution_with_worktree(tmp_path: Path):
    # CLA-20: launcher now skips worktree creation for non-git projects.
    (tmp_path / ".git").mkdir()
    launcher = ClaudeLauncher(worktree_mgr=_FakeWorktreeMgr())  # type: ignore[arg-type]
    spec = await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=True, identifier="CLA-99", title="My Task"),
    )
    assert "cla-99-my-task" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_resolution_autoscan_uses_slug_no_explicit_branch(tmp_path: Path):
    """Auto Scan (``identifier`` set) calls ``create_worktree`` without the
    ``branch_name`` kwarg so the WorktreeManager applies its prefix convention
    (``feature/claude/<slug>``)."""
    (tmp_path / ".git").mkdir()
    mgr = _FakeWorktreeMgr()
    launcher = ClaudeLauncher(worktree_mgr=mgr)  # type: ignore[arg-type]
    await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=True, identifier="CLA-99", title="My Task"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] is None
    assert mgr.calls[0]["slug"] == "cla-99-my-task"


@pytest.mark.asyncio
async def test_cwd_resolution_standard_uses_branch_name_verbatim(tmp_path: Path):
    """Standard mode: the user's entered ``branch_name`` is passed verbatim
    as the branch — no prefix applied, no slug wrapping. The worktree directory
    name is derived from the branch by slugifying it (so ``feature/my-fix``
    becomes dir ``feature-my-fix``)."""
    (tmp_path / ".git").mkdir()
    mgr = _FakeWorktreeMgr()
    launcher = ClaudeLauncher(worktree_mgr=mgr)  # type: ignore[arg-type]
    await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=True, branch_name="feature/my-fix"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/my-fix"
    assert mgr.calls[0]["slug"] == "feature-my-fix"


@pytest.mark.asyncio
async def test_cwd_resolution_standard_empty_branch_auto_generates(tmp_path: Path):
    """With ``worktree=True`` and no identifier/branch_name, the launcher must
    still create a worktree — auto-generating a unique branch name so two
    consecutive Standard sessions never collide on the same directory."""
    (tmp_path / ".git").mkdir()
    mgr = _FakeWorktreeMgr()
    launcher = ClaudeLauncher(worktree_mgr=mgr)  # type: ignore[arg-type]
    await launcher.build(
        _make_project(str(tmp_path)),
        ClaudeOptions(worktree=True),
    )
    assert len(mgr.calls) == 1
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("claude-session-")


@pytest.mark.asyncio
async def test_cwd_resolution_worktree_true_without_mgr_raises(tmp_path: Path):
    """Dropping the silent fallback: when ``worktree=True`` but no
    ``WorktreeManager`` is wired, fail loud rather than landing the user
    back in project root."""
    (tmp_path / ".git").mkdir()
    launcher = ClaudeLauncher()

    with pytest.raises(RuntimeError, match="WorktreeManager"):
        await launcher.build(
            _make_project(str(tmp_path)),
            ClaudeOptions(worktree=True, branch_name="x"),
        )


@pytest.mark.asyncio
async def test_cwd_resolution_worktree_propagates_creation_error(tmp_path: Path):
    """When ``create_worktree`` raises ``WorktreeCreationError``, the launcher
    propagates it — the router maps it to HTTP 400 so the user sees the git
    stderr."""
    from mobaide.linear.worktree import WorktreeCreationError

    class _FailingMgr:
        async def create_worktree(self, slug, project_root, *, branch_name=None):
            raise WorktreeCreationError(
                branch=branch_name or slug, path="/x", stderr="boom"
            )

    (tmp_path / ".git").mkdir()
    launcher = ClaudeLauncher(worktree_mgr=_FailingMgr())  # type: ignore[arg-type]
    with pytest.raises(WorktreeCreationError):
        await launcher.build(
            _make_project(str(tmp_path)),
            ClaudeOptions(worktree=True, branch_name="my-branch"),
        )


@pytest.mark.asyncio
async def test_mcp_json_written_into_worktree_dir(tmp_path: Path):
    """Regression for Bug 6: when a worktree is created, ``.mcp.json`` must
    land inside the worktree directory (the session's actual CWD), not in the
    project root."""
    (tmp_path / ".git").mkdir()
    mgr = _FakeWorktreeMgr()
    launcher = ClaudeLauncher(worktree_mgr=mgr)  # type: ignore[arg-type]
    spec = await launcher.build(
        _make_project(str(tmp_path), key="lin_k"),
        ClaudeOptions(worktree=True, branch_name="feat/x"),
    )
    mcp = Path(spec.cwd) / ".mcp.json"
    assert mcp.exists()
    project_root_mcp = tmp_path / ".mcp.json"
    assert not project_root_mcp.exists(), (
        ".mcp.json must not leak to the project root"
    )


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


@pytest.mark.asyncio
async def test_adhoc_launch_resolves_cwd_to_target_directory(tmp_path: Path):
    """The launcher accepts an :class:`AdHocLaunch` target and resolves
    ``spec.cwd`` to its ``cwd`` directly — no project lookup, no
    worktree dance, no slug derivation."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(AdHocLaunch(cwd=str(tmp_path)), ClaudeOptions())
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_adhoc_launch_missing_cwd_raises_file_not_found(tmp_path: Path):
    """Ad-hoc cwd must exist on disk — same failure shape as the project
    path branch so the router/relay error mapping stays uniform."""
    missing = tmp_path / "no" / "such" / "dir"
    launcher = ClaudeLauncher()

    with pytest.raises(FileNotFoundError) as exc_info:
        await launcher.build(AdHocLaunch(cwd=str(missing)), ClaudeOptions())

    assert exc_info.value.filename == str(missing)
    assert exc_info.value.errno == 2


@pytest.mark.asyncio
async def test_adhoc_launch_ignores_worktree_flag(tmp_path: Path):
    """A stale client may still send ``worktree=True`` for a custom path.
    Ad-hoc launches must silently downgrade — never call the
    :class:`WorktreeManager`, never raise — and just spawn in the target
    cwd. The downgrade is logged for auditability."""
    # Even with a ``.git`` directory present, ad-hoc must NOT consult the
    # worktree manager because there is no stored project / branch_prefix
    # to use.
    (tmp_path / ".git").mkdir()
    mgr = _FakeWorktreeMgr()
    launcher = ClaudeLauncher(worktree_mgr=mgr)  # type: ignore[arg-type]

    spec = await launcher.build(
        AdHocLaunch(cwd=str(tmp_path)),
        ClaudeOptions(worktree=True, branch_name="feature/x"),
    )

    assert spec.cwd == str(tmp_path)
    assert mgr.calls == [], (
        "WorktreeManager must not be invoked for AdHocLaunch even when "
        "worktree=True is requested"
    )


@pytest.mark.asyncio
async def test_adhoc_launch_env_omits_linear_api_key(
    tmp_path: Path, monkeypatch,
):
    """Ad-hoc sessions have no stored project, so no ``LINEAR_API_KEY``
    can be injected — even if the outer shell happens to export one,
    the launcher must not surface it (that would leak the operator's
    key into an arbitrary user-supplied folder context)."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    launcher = ClaudeLauncher()
    spec = await launcher.build(AdHocLaunch(cwd=str(tmp_path)), ClaudeOptions())
    assert "LINEAR_API_KEY" not in spec.env


@pytest.mark.asyncio
async def test_adhoc_launch_env_carries_session_preset(tmp_path: Path):
    """The 1-hour prompt-cache preset and PYTHONIOENCODING must still be
    applied — the ad-hoc branch is not allowed to skip them, only the
    project-specific overlays (Linear key, conda env)."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(AdHocLaunch(cwd=str(tmp_path)), ClaudeOptions())
    assert spec.env["ENABLE_PROMPT_CACHING_1H"] == "1"
    assert spec.env["PYTHONIOENCODING"] == "utf-8"


@pytest.mark.asyncio
async def test_adhoc_launch_writes_no_project_files(tmp_path: Path):
    """Polluting an arbitrary user-supplied directory with ``.mcp.json`` /
    ``.claude/settings.local.json`` would be a surprise — the ad-hoc
    branch of ``_write_project_files`` must short-circuit and write
    nothing. ``spec.created_files`` is empty for the same reason."""
    launcher = ClaudeLauncher()
    spec = await launcher.build(AdHocLaunch(cwd=str(tmp_path)), ClaudeOptions())

    assert spec.created_files == []
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()


@pytest.mark.asyncio
async def test_adhoc_launch_argv_matches_project_launch(tmp_path: Path):
    """argv is built independently of the target — switching from a
    :class:`ProjectConfig` to an :class:`AdHocLaunch` must produce the
    same flags for the same :class:`ClaudeOptions`. Regression guard:
    if a future refactor sneaks a project-specific flag into
    ``_build_argv``, this test catches it."""
    launcher = ClaudeLauncher()
    opts = ClaudeOptions(plan_mode=True, initial_prompt="hello")

    project_spec = await launcher.build(_make_project(str(tmp_path)), opts)
    adhoc_spec = await launcher.build(AdHocLaunch(cwd=str(tmp_path)), opts)

    assert project_spec.argv == adhoc_spec.argv


def test_adhoc_launch_target_is_frozen():
    """:class:`AdHocLaunch` is a ``frozen=True`` dataclass — accidental
    mutation must fail loudly. Pinning this so a future code change that
    drops ``frozen=True`` is caught at test time."""
    from dataclasses import FrozenInstanceError

    target = AdHocLaunch(cwd="/tmp")
    with pytest.raises(FrozenInstanceError):
        target.cwd = "/other"  # type: ignore[misc]

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from mobaide.core import codex_auth, codex_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.codex_launcher import (
    CodexAuthRequiredError,
    CodexBinaryNotFoundError,
    CodexLauncher,
    CodexOptions,
)
from mobaide.core.projects_config import ProjectConfig

# Captured before the autouse ``_pin_codex_binary`` fixture swaps it, so the
# resolver tests below exercise the real implementation.
_REAL_CODEX_RESOLVER = codex_launcher._resolve_codex_binary


@pytest.fixture(autouse=True)
def _codex_authed_by_default(monkeypatch):
    """Default every launcher test to an authenticated Codex.

    Most tests here exercise argv/env/cwd, not auth, so the CLA-71 ``build()``
    auth guard would otherwise trip them in a clean CI env (no
    ``~/.codex/auth.json``, no ``OPENAI_API_KEY``) — they'd raise
    ``CodexAuthRequiredError`` before reaching their assertions, and pass only on
    a host that happens to be Codex-authed. The auth-specific tests override this
    (the negative ones via ``_unauthenticated_codex_env``; the positive ones set
    their own env in the test body), so this default never masks them.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-default")


def _make_project(path: str, *, conda: str | None = None) -> ProjectConfig:
    return ProjectConfig(name="test", path=path, conda_env=conda)


# ---------------------------------------------------------------------------
# argv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_argv_default_is_sandboxed_non_interactive_preset(tmp_path: Path):
    """Default options reproduce the v1 preset: sandboxed, no approval prompts
    (the mobile client cannot mediate them yet), high reasoning effort."""
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.argv == [
        "codex",
        "--sandbox", "workspace-write",
        "--ask-for-approval", "never",
        "-c", "model_reasoning_effort=high",
    ]


@pytest.mark.asyncio
async def test_argv_model_flag_uses_long_form(tmp_path: Path):
    spec = await CodexLauncher().build(
        _make_project(str(tmp_path)), CodexOptions(model="gpt-5.4-codex"),
    )
    assert spec.argv[:3] == ["codex", "--model", "gpt-5.4-codex"]


@pytest.mark.asyncio
async def test_argv_full_auto_omits_sandbox_and_approval(tmp_path: Path):
    """full_auto maps to --dangerously-bypass-approvals-and-sandbox, which is
    mutually exclusive with the sandbox/approval flags — those must be dropped."""
    spec = await CodexLauncher().build(
        _make_project(str(tmp_path)), CodexOptions(full_auto=True),
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in spec.argv
    assert "--sandbox" not in spec.argv
    assert "--ask-for-approval" not in spec.argv


@pytest.mark.asyncio
async def test_argv_custom_sandbox_and_approval(tmp_path: Path):
    spec = await CodexLauncher().build(
        _make_project(str(tmp_path)),
        CodexOptions(sandbox="read-only", approval="on-request"),
    )
    assert spec.argv[spec.argv.index("--sandbox") + 1] == "read-only"
    assert spec.argv[spec.argv.index("--ask-for-approval") + 1] == "on-request"


@pytest.mark.asyncio
async def test_argv_initial_prompt_is_trailing_positional(tmp_path: Path):
    """Codex takes the prompt as a trailing positional — no ``--`` separator
    (unlike Claude)."""
    spec = await CodexLauncher().build(
        _make_project(str(tmp_path)), CodexOptions(initial_prompt="do the thing"),
    )
    assert spec.argv[-1] == "do the thing"
    assert "--" not in spec.argv


@pytest.mark.asyncio
async def test_argv_no_reasoning_effort_omits_config_override(tmp_path: Path):
    spec = await CodexLauncher().build(
        _make_project(str(tmp_path)), CodexOptions(reasoning_effort=None),
    )
    assert "-c" not in spec.argv
    assert not any("model_reasoning_effort" in a for a in spec.argv)


@pytest.mark.asyncio
async def test_argv_independent_of_target(tmp_path: Path):
    """Switching ProjectConfig -> AdHocLaunch must not change the flags."""
    opts = CodexOptions(model="m", initial_prompt="go")
    project = await CodexLauncher().build(_make_project(str(tmp_path)), opts)
    adhoc = await CodexLauncher().build(AdHocLaunch(cwd=str(tmp_path)), opts)
    assert project.argv == adhoc.argv


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_sets_encoding_and_term(tmp_path: Path):
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.env["PYTHONIOENCODING"] == "utf-8"
    assert spec.env.get("TERM")


@pytest.mark.asyncio
async def test_env_adds_no_claude_specific_vars(tmp_path: Path, monkeypatch):
    """Codex copies the inherited env but ADDS none of Claude's extras: no
    forced 1h-cache preset, no attention-hook vars, and — even for a project
    carrying a Linear key — no injected ``LINEAR_API_KEY``. The parent env is
    cleared first so the assertion isolates what the launcher itself injects."""
    for key in (
        "ENABLE_PROMPT_CACHING_1H", "MOBAIDE_SESSION_ID",
        "MOBAIDE_HOOK_URL", "MOBAIDE_HOOK_TOKEN", "LINEAR_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    proj = ProjectConfig(
        name="t", path=str(tmp_path), linear_api_key="lin_secret",
    )
    spec = await CodexLauncher().build(proj, CodexOptions())
    assert "ENABLE_PROMPT_CACHING_1H" not in spec.env
    assert "MOBAIDE_SESSION_ID" not in spec.env
    assert "LINEAR_API_KEY" not in spec.env


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        codex_launcher, "_resolve_codex_binary",
        lambda *_a, **_k: os.path.join("/opt", "cx", "bin", "codex"),
    )
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "cx", "bin")


# ---------------------------------------------------------------------------
# cwd / project files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cwd_project_root_no_worktree(tmp_path: Path):
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_adhoc_target(tmp_path: Path):
    spec = await CodexLauncher().build(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_missing_raises_clean_file_not_found(tmp_path: Path):
    missing = tmp_path / "does" / "not" / "exist"
    with pytest.raises(FileNotFoundError) as exc_info:
        await CodexLauncher().build(_make_project(str(missing)), CodexOptions())
    assert exc_info.value.filename == str(missing)
    assert exc_info.value.errno == 2


@pytest.mark.asyncio
async def test_writes_no_project_files(tmp_path: Path):
    """Codex needs no .mcp.json / .claude settings — created_files is empty."""
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.created_files == []
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()


class _RecordingWorktreeMgr:
    """Minimal WorktreeManager double mirroring the real signature; returns a
    real (existing) worktree dir so ``check_cwd_exists`` passes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_worktree(self, slug, project_root, *, branch_name=None):
        from mobaide.linear.worktree import WorktreeResult

        self.calls.append({"slug": slug, "branch_name": branch_name})
        wt = Path(project_root) / "wt" / slug
        wt.mkdir(parents=True, exist_ok=True)
        return WorktreeResult(
            path=str(wt), is_new=True, branch=branch_name or slug,
        )


@pytest.mark.asyncio
async def test_cwd_worktree_on_git_project_resolves_to_worktree(tmp_path: Path):
    """worktree=True on a git project resolves cwd to the created worktree (not
    the project root) and hands the user's branch name to the manager verbatim."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await CodexLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)),
        CodexOptions(worktree=True, branch_name="feature/x"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_codex_prefix(tmp_path: Path):
    """With no branch_name the launcher auto-generates one under the
    ``codex-session-`` prefix (distinct from Claude/Terminal prefixes)."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await CodexLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), CodexOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("codex-session-")


@pytest.mark.asyncio
async def test_cwd_worktree_non_git_falls_back_to_root(tmp_path: Path):
    """A stale worktree=True for a non-git project is downgraded to the project
    root and the WorktreeManager is never touched."""
    mgr = _RecordingWorktreeMgr()
    spec = await CodexLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), CodexOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_cwd_worktree_without_mgr_raises(tmp_path: Path):
    """worktree=True on a git project with no WorktreeManager wired fails loud
    rather than silently landing in the project root (mirrors Claude)."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(RuntimeError, match="WorktreeManager"):
        await CodexLauncher().build(
            _make_project(str(tmp_path)),
            CodexOptions(worktree=True, branch_name="x"),
        )


@pytest.mark.asyncio
async def test_cwd_adhoc_worktree_is_ignored(tmp_path: Path):
    """Ad-hoc launches have no git/project context, so worktree=True is ignored
    and the bare cwd is used."""
    spec = await CodexLauncher().build(
        AdHocLaunch(cwd=str(tmp_path)), CodexOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)


# ---------------------------------------------------------------------------
# binary resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise CodexBinaryNotFoundError(searched_paths=["PATH:codex"])

    monkeypatch.setattr(codex_launcher, "_resolve_codex_binary", _raise)
    with pytest.raises(CodexBinaryNotFoundError):
        await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "codex"

    monkeypatch.setattr(codex_launcher, "_resolve_codex_binary", _capture)
    await CodexLauncher(codex_bin_override="/my/codex").build(
        _make_project(str(tmp_path)), CodexOptions(),
    )
    assert captured["override"] == "/my/codex"


def test_codex_options_is_frozen():
    from dataclasses import FrozenInstanceError

    opts = CodexOptions()
    with pytest.raises(FrozenInstanceError):
        opts.model = "x"  # type: ignore[misc]


def test_binary_not_found_message_is_actionable():
    err = CodexBinaryNotFoundError(searched_paths=["PATH:codex", "fallback:/x"])
    msg = str(err)
    assert "npm install -g @openai/codex" in msg
    assert "MOBAIDE_CODEX_BIN" in msg
    assert err.cli_name == "codex"
    assert err.searched_paths == ["PATH:codex", "fallback:/x"]


# ---------------------------------------------------------------------------
# real resolver (override / env / not-found)
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_codex_env(monkeypatch):
    monkeypatch.delenv("MOBAIDE_CODEX_BIN", raising=False)
    yield


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path, _clean_codex_env):
    fake = _make_executable(tmp_path / "mycodex")
    monkeypatch.setenv("MOBAIDE_CODEX_BIN", str(fake))
    assert _REAL_CODEX_RESOLVER() == str(fake)


def test_real_resolver_prefers_override_over_env(monkeypatch, tmp_path, _clean_codex_env):
    fake_override = _make_executable(tmp_path / "from_override")
    fake_env = _make_executable(tmp_path / "from_env")
    monkeypatch.setenv("MOBAIDE_CODEX_BIN", str(fake_env))
    assert _REAL_CODEX_RESOLVER(override=str(fake_override)) == str(fake_override)


def test_real_resolver_raises_with_actionable_message(
    monkeypatch, tmp_path, _clean_codex_env,
):
    from mobaide.core import cli_launcher

    monkeypatch.setattr(codex_launcher, "_posix_fallback_candidates", lambda: [])
    monkeypatch.setattr(codex_launcher, "_windows_fallback_candidates", lambda: [])
    monkeypatch.setattr(cli_launcher.shutil, "which", lambda *_a, **_kw: None)

    with pytest.raises(CodexBinaryNotFoundError) as exc_info:
        _REAL_CODEX_RESOLVER()

    msg = str(exc_info.value)
    assert "@openai/codex" in msg
    assert "MOBAIDE_CODEX_BIN" in msg
    assert exc_info.value.searched_paths


# ---------------------------------------------------------------------------
# auth guard (CLA-71)
# ---------------------------------------------------------------------------


@pytest.fixture
def _unauthenticated_codex_env(monkeypatch, tmp_path):
    """Empty CODEX_HOME (no auth.json), no env key, and a ``codex login status``
    that reports logged-out — so ``codex_auth_present`` returns False
    deterministically, independent of the test host's real codex credentials."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        codex_auth.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 1, b"", b""),
    )
    yield


@pytest.mark.asyncio
async def test_build_raises_auth_required_when_unauthenticated(
    tmp_path: Path, _unauthenticated_codex_env,
):
    """``build()`` must raise ``CodexAuthRequiredError`` BEFORE any cwd/worktree
    work when neither an env key nor an auth.json is reachable — so the spawn
    path never launches an unauthenticated codex into its login TUI."""
    with pytest.raises(CodexAuthRequiredError):
        await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())


@pytest.mark.asyncio
async def test_build_auth_guard_runs_before_worktree_creation(
    tmp_path: Path, _unauthenticated_codex_env,
):
    """The guard fires before worktree resolution: a git project requesting a
    worktree with no WorktreeManager would otherwise raise the loud
    misconfiguration ``RuntimeError`` — proving auth is checked first."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(CodexAuthRequiredError):
        await CodexLauncher().build(
            _make_project(str(tmp_path)),
            CodexOptions(worktree=True, branch_name="x"),
        )


@pytest.mark.asyncio
async def test_build_succeeds_when_env_api_key_present(
    tmp_path: Path, monkeypatch,
):
    """An ``OPENAI_API_KEY`` in the inherited env passes the guard and the build
    completes normally (no auth.json required)."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.cwd == str(tmp_path)
    assert spec.argv[0] == "codex"


@pytest.mark.asyncio
async def test_build_falls_back_to_auth_json_when_status_unsupported(
    tmp_path: Path, monkeypatch,
):
    """On an older codex without ``login status`` (clap exit 2), detection is
    indeterminate and falls back to auth.json presence, so a valid file-authed
    host still builds."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        codex_auth.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, 2, b"", b"error: unrecognized subcommand 'status'",
        ),
    )
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_build_raises_when_auth_json_stale_and_status_logged_out(
    tmp_path: Path, monkeypatch,
):
    """A present-but-stale ``auth.json`` no longer short-circuits: when ``codex
    login status`` reports signed out, ``build()`` must raise
    ``CodexAuthRequiredError`` instead of launching Codex into interactive
    login — the round-4 review fix."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        codex_auth.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 1, b"", b"Not logged in"),
    )
    with pytest.raises(CodexAuthRequiredError):
        await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())


@pytest.mark.asyncio
async def test_build_succeeds_when_login_status_reports_signed_in(
    tmp_path: Path, monkeypatch,
):
    """No env key and no auth.json, but ``codex login status`` exits 0 — the
    credential lives in the OS keyring the file check can't see. The guard must
    pass and build complete, rather than wrongly demanding an API-key login."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        codex_auth.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, b"", b""),
    )
    spec = await CodexLauncher().build(_make_project(str(tmp_path)), CodexOptions())
    assert spec.cwd == str(tmp_path)
    assert spec.argv[0] == "codex"


def test_auth_required_error_discriminator_and_hint():
    err = CodexAuthRequiredError()
    assert err.cli_name == "codex"
    msg = str(err)
    assert "not authenticated" in msg.lower()
    assert "codex login" in msg.lower()


# ---------------------------------------------------------------------------
# list_models (memoized ``codex debug models --json`` enumeration)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ('["gpt-5.5", "gpt-5.4"]', ["gpt-5.5", "gpt-5.4"]),
        ('{"models": ["gpt-5.5"]}', ["gpt-5.5"]),
        (
            '[{"id": "a"}, {"name": "b"}, {"model": "c"}, {"slug": "d"}]',
            ["a", "b", "c", "d"],
        ),
        # ``id`` outranks the other keys when several are present.
        ('[{"name": "display", "id": "canonical"}]', ["canonical"]),
        # Unusable entries are dropped, salvaging whatever parses.
        ('["ok", 42, {"id": "  "}, {}]', ["ok"]),
        ('[" padded "]', ["padded"]),
    ],
)
def test_parse_codex_models_accepts_plausible_shapes(output, expected):
    """``debug models`` has no output-stability contract (openai/codex#8871),
    so the parser must accept every plausible JSON shape."""
    assert codex_launcher._parse_codex_models(output) == expected


@pytest.mark.parametrize(
    "output",
    [
        "not json at all",
        '"a bare string"',
        "42",
        '{"other": ["gpt-5.5"]}',
        "[]",
        "[42, {}, null]",
        "",
    ],
)
def test_parse_codex_models_rejects_unusable_output(output):
    assert codex_launcher._parse_codex_models(output) is None


@pytest.mark.asyncio
async def test_list_models_parses_json_and_memoizes(monkeypatch):
    """A successful enumeration memoizes the parsed list and never re-invokes
    the runner on a warm cache — ``cached_models`` is the non-spawning
    capability view."""
    calls = {"n": 0}

    def _runner(_binary):
        calls["n"] += 1
        return '["gpt-x", "gpt-y"]'

    monkeypatch.setattr(codex_launcher, "_run_codex_models_json", _runner)
    launcher = CodexLauncher()
    assert launcher.cached_models is None

    first = await launcher.list_models()
    second = await launcher.list_models()

    assert first == ["gpt-x", "gpt-y"]
    assert second == ["gpt-x", "gpt-y"]
    assert launcher.cached_models == ("gpt-x", "gpt-y")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_list_models_failed_run_memoizes_fallback(monkeypatch):
    """A failed run (non-zero exit → runner returns None) memoizes the curated
    fallback — unlike Antigravity, Codex capabilities must always carry a model
    list, so the fallback is served without retrying the runner."""
    calls = {"n": 0}

    def _runner(_binary):
        calls["n"] += 1
        return None

    monkeypatch.setattr(codex_launcher, "_run_codex_models_json", _runner)
    launcher = CodexLauncher()

    first = await launcher.list_models()
    second = await launcher.list_models()

    assert first == list(codex_launcher._CODEX_FALLBACK_MODELS)
    assert second == first
    assert launcher.cached_models == codex_launcher._CODEX_FALLBACK_MODELS
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_list_models_unparseable_output_serves_fallback(monkeypatch):
    monkeypatch.setattr(
        codex_launcher, "_run_codex_models_json", lambda _b: "not json"
    )
    launcher = CodexLauncher()
    assert await launcher.list_models() == list(codex_launcher._CODEX_FALLBACK_MODELS)


@pytest.mark.asyncio
async def test_list_models_runner_exception_serves_fallback(monkeypatch):
    def _raise(_binary):
        raise OSError("exec failed")

    monkeypatch.setattr(codex_launcher, "_run_codex_models_json", _raise)
    launcher = CodexLauncher()
    assert await launcher.list_models() == list(codex_launcher._CODEX_FALLBACK_MODELS)


@pytest.mark.asyncio
async def test_list_models_missing_binary_serves_fallback_without_running(monkeypatch):
    def _raise(*_a, **_kw):
        raise CodexBinaryNotFoundError(searched_paths=["PATH:codex"])

    def _boom(_binary):
        raise AssertionError("runner must not be invoked when the binary is missing")

    monkeypatch.setattr(codex_launcher, "_resolve_codex_binary", _raise)
    monkeypatch.setattr(codex_launcher, "_run_codex_models_json", _boom)
    launcher = CodexLauncher()

    assert await launcher.list_models() == list(codex_launcher._CODEX_FALLBACK_MODELS)
    assert launcher.cached_models == codex_launcher._CODEX_FALLBACK_MODELS

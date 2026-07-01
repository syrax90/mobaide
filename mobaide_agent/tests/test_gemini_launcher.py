from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mobaide.core import gemini_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.gemini_launcher import (
    GeminiAuthRequiredError,
    GeminiBinaryNotFoundError,
    GeminiLauncher,
    GeminiOptions,
)
from mobaide.core.projects_config import ProjectConfig

# Captured before the autouse ``_pin_gemini_binary`` fixture swaps it, so the
# resolver tests below exercise the real implementation.
_REAL_GEMINI_RESOLVER = gemini_launcher._resolve_gemini_binary


@pytest.fixture(autouse=True)
def _gemini_authed_by_default(monkeypatch):
    """Default every launcher test to an authenticated Gemini.

    Most tests here exercise argv/env/cwd, not auth, so the ``build()`` auth
    guard would otherwise trip them in a clean CI env (no ``GEMINI_API_KEY``, no
    ``~/.gemini/oauth_creds.json``) — they'd raise ``GeminiAuthRequiredError``
    before reaching their assertions. The auth-specific tests override this (the
    negative ones via ``_unauthenticated_gemini_env``; the positive ones set
    their own credential in the test body), so this default never masks them.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-default-key")


def _make_project(path: str, *, conda: str | None = None) -> ProjectConfig:
    return ProjectConfig(name="test", path=path, conda_env=conda)


# argv


@pytest.mark.asyncio
async def test_argv_default_emits_approval_mode_default(tmp_path: Path):
    """Default options launch with Gemini's own defaults: no ``--model`` (the CLI
    picks ``auto``) and an explicit ``--approval-mode default``."""
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert spec.argv == ["gemini", "--approval-mode", "default"]


@pytest.mark.asyncio
async def test_argv_model_flag_uses_long_form(tmp_path: Path):
    spec = await GeminiLauncher().build(
        _make_project(str(tmp_path)), GeminiOptions(model="flash"),
    )
    assert spec.argv[:3] == ["gemini", "--model", "flash"]


@pytest.mark.asyncio
async def test_argv_approval_mode_is_emitted(tmp_path: Path):
    """``--approval-mode`` replaces Gemini's deprecated ``--yolo``; the chosen
    mode is passed through verbatim."""
    spec = await GeminiLauncher().build(
        _make_project(str(tmp_path)), GeminiOptions(approval_mode="yolo"),
    )
    assert spec.argv[spec.argv.index("--approval-mode") + 1] == "yolo"
    assert "--yolo" not in spec.argv


@pytest.mark.asyncio
async def test_argv_sandbox_flag_added_when_true(tmp_path: Path):
    spec = await GeminiLauncher().build(
        _make_project(str(tmp_path)), GeminiOptions(sandbox=True),
    )
    assert "--sandbox" in spec.argv


@pytest.mark.asyncio
async def test_argv_sandbox_omitted_when_false(tmp_path: Path):
    spec = await GeminiLauncher().build(
        _make_project(str(tmp_path)), GeminiOptions(sandbox=False),
    )
    assert "--sandbox" not in spec.argv


@pytest.mark.asyncio
async def test_argv_include_directories_repeats_flag(tmp_path: Path):
    """Each extra workspace dir is passed as its own ``--include-directories``
    flag so a path containing a comma can't corrupt the list."""
    spec = await GeminiLauncher().build(
        _make_project(str(tmp_path)),
        GeminiOptions(include_directories=("/a", "/b")),
    )
    assert spec.argv.count("--include-directories") == 2
    idxs = [i for i, a in enumerate(spec.argv) if a == "--include-directories"]
    assert [spec.argv[i + 1] for i in idxs] == ["/a", "/b"]


@pytest.mark.asyncio
async def test_argv_no_include_directories_omits_flag(tmp_path: Path):
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert "--include-directories" not in spec.argv


@pytest.mark.asyncio
async def test_argv_independent_of_target(tmp_path: Path):
    """Switching ProjectConfig -> AdHocLaunch must not change the flags."""
    opts = GeminiOptions(model="pro", approval_mode="plan", sandbox=True)
    project = await GeminiLauncher().build(_make_project(str(tmp_path)), opts)
    adhoc = await GeminiLauncher().build(AdHocLaunch(cwd=str(tmp_path)), opts)
    assert project.argv == adhoc.argv


# env


@pytest.mark.asyncio
async def test_env_sets_encoding_and_term(tmp_path: Path):
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert spec.env["PYTHONIOENCODING"] == "utf-8"
    assert spec.env.get("TERM")


@pytest.mark.asyncio
async def test_env_adds_no_claude_specific_vars(tmp_path: Path, monkeypatch):
    """Gemini copies the inherited env but ADDS none of Claude's extras and — even
    for a project carrying a Linear key — no injected ``LINEAR_API_KEY``."""
    for key in (
        "ENABLE_PROMPT_CACHING_1H", "MOBAIDE_SESSION_ID",
        "MOBAIDE_HOOK_URL", "MOBAIDE_HOOK_TOKEN", "LINEAR_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    proj = ProjectConfig(name="t", path=str(tmp_path), linear_api_key="lin_secret")
    spec = await GeminiLauncher().build(proj, GeminiOptions())
    assert "ENABLE_PROMPT_CACHING_1H" not in spec.env
    assert "MOBAIDE_SESSION_ID" not in spec.env
    assert "LINEAR_API_KEY" not in spec.env


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        gemini_launcher, "_resolve_gemini_binary",
        lambda *_a, **_k: os.path.join("/opt", "gm", "bin", "gemini"),
    )
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "gm", "bin")


# cwd / project files


@pytest.mark.asyncio
async def test_cwd_project_root_no_worktree(tmp_path: Path):
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_adhoc_target(tmp_path: Path):
    spec = await GeminiLauncher().build(AdHocLaunch(cwd=str(tmp_path)), GeminiOptions())
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_missing_raises_clean_file_not_found(tmp_path: Path):
    missing = tmp_path / "does" / "not" / "exist"
    with pytest.raises(FileNotFoundError) as exc_info:
        await GeminiLauncher().build(_make_project(str(missing)), GeminiOptions())
    assert exc_info.value.filename == str(missing)
    assert exc_info.value.errno == 2


@pytest.mark.asyncio
async def test_writes_no_project_files(tmp_path: Path):
    """Gemini needs no .mcp.json / .claude settings — created_files is empty."""
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
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
        return WorktreeResult(path=str(wt), is_new=True, branch=branch_name or slug)


@pytest.mark.asyncio
async def test_cwd_worktree_on_git_project_resolves_to_worktree(tmp_path: Path):
    """worktree=True on a git project resolves cwd to the created worktree (not
    the project root) and hands the user's branch name to the manager verbatim."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await GeminiLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)),
        GeminiOptions(worktree=True, branch_name="feature/x"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_gemini_prefix(tmp_path: Path):
    """With no branch_name the launcher auto-generates one under the
    ``gemini-session-`` prefix (distinct from Claude/Codex/Terminal prefixes)."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await GeminiLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), GeminiOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("gemini-session-")


@pytest.mark.asyncio
async def test_cwd_worktree_non_git_falls_back_to_root(tmp_path: Path):
    """A stale worktree=True for a non-git project is downgraded to the project
    root and the WorktreeManager is never touched."""
    mgr = _RecordingWorktreeMgr()
    spec = await GeminiLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), GeminiOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_cwd_worktree_without_mgr_raises(tmp_path: Path):
    """worktree=True on a git project with no WorktreeManager wired fails loud
    rather than silently landing in the project root (mirrors Claude/Codex)."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(RuntimeError, match="WorktreeManager"):
        await GeminiLauncher().build(
            _make_project(str(tmp_path)),
            GeminiOptions(worktree=True, branch_name="x"),
        )


@pytest.mark.asyncio
async def test_cwd_adhoc_worktree_is_ignored(tmp_path: Path):
    """Ad-hoc launches have no git/project context, so worktree=True is ignored
    and the bare cwd is used."""
    spec = await GeminiLauncher().build(
        AdHocLaunch(cwd=str(tmp_path)), GeminiOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)


# binary resolution


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise GeminiBinaryNotFoundError(searched_paths=["PATH:gemini"])

    monkeypatch.setattr(gemini_launcher, "_resolve_gemini_binary", _raise)
    with pytest.raises(GeminiBinaryNotFoundError):
        await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "gemini"

    monkeypatch.setattr(gemini_launcher, "_resolve_gemini_binary", _capture)
    await GeminiLauncher(gemini_bin_override="/my/gemini").build(
        _make_project(str(tmp_path)), GeminiOptions(),
    )
    assert captured["override"] == "/my/gemini"


def test_gemini_options_is_frozen():
    from dataclasses import FrozenInstanceError

    opts = GeminiOptions()
    with pytest.raises(FrozenInstanceError):
        opts.model = "x"  # type: ignore[misc]


def test_binary_not_found_message_is_actionable():
    err = GeminiBinaryNotFoundError(searched_paths=["PATH:gemini", "fallback:/x"])
    msg = str(err)
    assert "npm install -g @google/gemini-cli" in msg
    assert "MOBAIDE_GEMINI_BIN" in msg
    assert err.cli_name == "gemini"
    assert err.searched_paths == ["PATH:gemini", "fallback:/x"]


# real resolver (override / env / not-found)


@pytest.fixture
def _clean_gemini_env(monkeypatch):
    monkeypatch.delenv("MOBAIDE_GEMINI_BIN", raising=False)
    yield


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path, _clean_gemini_env):
    fake = _make_executable(tmp_path / "mygemini")
    monkeypatch.setenv("MOBAIDE_GEMINI_BIN", str(fake))
    assert _REAL_GEMINI_RESOLVER() == str(fake)


def test_real_resolver_prefers_override_over_env(monkeypatch, tmp_path, _clean_gemini_env):
    fake_override = _make_executable(tmp_path / "from_override")
    fake_env = _make_executable(tmp_path / "from_env")
    monkeypatch.setenv("MOBAIDE_GEMINI_BIN", str(fake_env))
    assert _REAL_GEMINI_RESOLVER(override=str(fake_override)) == str(fake_override)


def test_real_resolver_raises_with_actionable_message(
    monkeypatch, tmp_path, _clean_gemini_env,
):
    from mobaide.core import cli_launcher

    monkeypatch.setattr(gemini_launcher, "_posix_fallback_candidates", lambda: [])
    monkeypatch.setattr(gemini_launcher, "_windows_fallback_candidates", lambda: [])
    monkeypatch.setattr(cli_launcher.shutil, "which", lambda *_a, **_kw: None)

    with pytest.raises(GeminiBinaryNotFoundError) as exc_info:
        _REAL_GEMINI_RESOLVER()

    msg = str(exc_info.value)
    assert "@google/gemini-cli" in msg
    assert "MOBAIDE_GEMINI_BIN" in msg
    assert exc_info.value.searched_paths


# auth guard (detection-only — env + filesystem, no subprocess)


@pytest.fixture
def _unauthenticated_gemini_env(monkeypatch, tmp_path):
    """Empty GEMINI_CLI_HOME (no oauth_creds.json / settings.json / .env) and no
    credential env vars, so ``gemini_auth_present`` returns False deterministically,
    independent of the test host's real gemini credentials. Pointing the home root
    at ``tmp_path`` — the same dir the build target uses as its cwd — also bounds
    the ``.env`` upward search so it never climbs into the real home tree."""
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.mark.asyncio
async def test_build_raises_auth_required_when_unauthenticated(
    tmp_path: Path, _unauthenticated_gemini_env,
):
    """``build()`` must raise ``GeminiAuthRequiredError`` BEFORE any cwd/worktree
    work when no credential is reachable — so the spawn path never launches an
    unauthenticated gemini into its "Sign in with Google" TUI."""
    with pytest.raises(GeminiAuthRequiredError):
        await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())


@pytest.mark.asyncio
async def test_build_auth_guard_runs_before_worktree_creation(
    tmp_path: Path, _unauthenticated_gemini_env,
):
    """The guard fires before worktree resolution: a git project requesting a
    worktree with no WorktreeManager would otherwise raise the loud
    misconfiguration ``RuntimeError`` — proving auth is checked first."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(GeminiAuthRequiredError):
        await GeminiLauncher().build(
            _make_project(str(tmp_path)),
            GeminiOptions(worktree=True, branch_name="x"),
        )


@pytest.mark.asyncio
async def test_build_succeeds_when_env_api_key_present(tmp_path: Path, monkeypatch):
    """A ``GEMINI_API_KEY`` in the inherited env passes the guard and the build
    completes normally."""
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test-present")
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert spec.cwd == str(tmp_path)
    assert spec.argv[0] == "gemini"


@pytest.mark.asyncio
async def test_build_succeeds_when_cached_oauth_creds_present(
    tmp_path: Path, monkeypatch,
):
    """No env key, but a cached ``~/.gemini/oauth_creds.json`` (Sign-in-with-Google)
    passes the guard — the filesystem branch of detection."""
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "gem_home"
    config = root / ".gemini"
    config.mkdir(parents=True)
    (config / "oauth_creds.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(root))
    spec = await GeminiLauncher().build(_make_project(str(tmp_path)), GeminiOptions())
    assert spec.cwd == str(tmp_path)


def test_auth_required_error_discriminator_and_hint():
    err = GeminiAuthRequiredError()
    assert err.cli_name == "gemini"
    msg = str(err)
    assert "not authenticated" in msg.lower()
    assert "gemini_api_key" in msg.lower()

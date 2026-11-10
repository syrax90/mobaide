from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mobaide.core import antigravity_launcher
from mobaide.core.antigravity_launcher import (
    AntigravityBinaryNotFoundError,
    AntigravityLauncher,
    AntigravityOptions,
)
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.projects_config import ProjectConfig

# Captured before the autouse ``_pin_antigravity_binary`` fixture swaps it, so
# the resolver tests below exercise the real implementation.
_REAL_AGY_RESOLVER = antigravity_launcher._resolve_agy_binary


def _make_project(path: str, *, conda: str | None = None) -> ProjectConfig:
    return ProjectConfig(name="test", path=path, conda_env=conda)


# argv


@pytest.mark.asyncio
async def test_argv_default_is_bare_agy(tmp_path: Path):
    """Default options launch a bare ``agy``: no ``--model`` (the CLI picks its
    own default) and — unlike Gemini before it — no approval-mode flag exists."""
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert spec.argv == ["agy"]


@pytest.mark.asyncio
async def test_argv_model_display_name_is_single_element(tmp_path: Path):
    """``agy models`` prints full display names with spaces and parens; the
    launcher must forward one verbatim as a SINGLE argv element (safe because
    the spawn execs argv directly, never via a shell)."""
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)),
        AntigravityOptions(model="Gemini 3.1 Pro (Low)"),
    )
    assert spec.argv == ["agy", "--model", "Gemini 3.1 Pro (Low)"]


@pytest.mark.asyncio
async def test_argv_model_saved_as_an_agy_models_line_passes_only_the_name(tmp_path: Path):
    """A choice saved from an older agent may hold a whole ``agy models``
    line (model id, tab, display name), which ``--model`` rejects."""
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)),
        AntigravityOptions(model="gemini-3.8-flash-high\tGemini 3.8 Flash (High)"),
    )
    assert spec.argv == ["agy", "--model", "Gemini 3.8 Flash (High)"]


@pytest.mark.asyncio
async def test_argv_skip_permissions_flag_added_when_true(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(skip_permissions=True),
    )
    assert "--dangerously-skip-permissions" in spec.argv


@pytest.mark.asyncio
async def test_argv_skip_permissions_omitted_when_false(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(skip_permissions=False),
    )
    assert "--dangerously-skip-permissions" not in spec.argv


@pytest.mark.asyncio
async def test_argv_sandbox_flag_added_when_true(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(sandbox=True),
    )
    assert "--sandbox" in spec.argv


@pytest.mark.asyncio
async def test_argv_sandbox_omitted_when_false(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(sandbox=False),
    )
    assert "--sandbox" not in spec.argv


@pytest.mark.asyncio
async def test_argv_add_dirs_repeats_flag(tmp_path: Path):
    """Each extra workspace dir is passed as its own ``--add-dir`` flag so a
    path containing a comma can't corrupt the list."""
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)),
        AntigravityOptions(add_dirs=("/a", "/b")),
    )
    assert spec.argv.count("--add-dir") == 2
    idxs = [i for i, a in enumerate(spec.argv) if a == "--add-dir"]
    assert [spec.argv[i + 1] for i in idxs] == ["/a", "/b"]


@pytest.mark.asyncio
async def test_argv_no_add_dirs_omits_flag(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert "--add-dir" not in spec.argv


@pytest.mark.asyncio
async def test_argv_independent_of_target(tmp_path: Path):
    """Switching ProjectConfig -> AdHocLaunch must not change the flags."""
    opts = AntigravityOptions(model="Gemini 3.1 Pro (Low)", sandbox=True)
    project = await AntigravityLauncher().build(_make_project(str(tmp_path)), opts)
    adhoc = await AntigravityLauncher().build(AdHocLaunch(cwd=str(tmp_path)), opts)
    assert project.argv == adhoc.argv


# env


@pytest.mark.asyncio
async def test_env_sets_encoding_and_term(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert spec.env["PYTHONIOENCODING"] == "utf-8"
    assert spec.env.get("TERM")


@pytest.mark.asyncio
async def test_env_adds_no_claude_specific_vars(tmp_path: Path, monkeypatch):
    """Antigravity copies the inherited env but ADDS none of Claude's extras and
    — even for a project carrying a Linear key — no injected ``LINEAR_API_KEY``."""
    for key in (
        "ENABLE_PROMPT_CACHING_1H", "MOBAIDE_SESSION_ID",
        "MOBAIDE_HOOK_URL", "MOBAIDE_HOOK_TOKEN", "LINEAR_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    proj = ProjectConfig(name="t", path=str(tmp_path), linear_api_key="lin_secret")
    spec = await AntigravityLauncher().build(proj, AntigravityOptions())
    assert "ENABLE_PROMPT_CACHING_1H" not in spec.env
    assert "MOBAIDE_SESSION_ID" not in spec.env
    assert "LINEAR_API_KEY" not in spec.env


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        antigravity_launcher, "_resolve_agy_binary",
        lambda *_a, **_k: os.path.join("/opt", "ag", "bin", "agy"),
    )
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "ag", "bin")


# cwd / project files


@pytest.mark.asyncio
async def test_cwd_project_root_no_worktree(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_adhoc_target(tmp_path: Path):
    spec = await AntigravityLauncher().build(
        AdHocLaunch(cwd=str(tmp_path)), AntigravityOptions(),
    )
    assert spec.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_cwd_missing_raises_clean_file_not_found(tmp_path: Path):
    missing = tmp_path / "does" / "not" / "exist"
    with pytest.raises(FileNotFoundError) as exc_info:
        await AntigravityLauncher().build(
            _make_project(str(missing)), AntigravityOptions(),
        )
    assert exc_info.value.filename == str(missing)
    assert exc_info.value.errno == 2


@pytest.mark.asyncio
async def test_writes_no_project_files(tmp_path: Path):
    """Antigravity needs no .mcp.json / .claude settings — created_files is empty."""
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert spec.created_files == []
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".claude").exists()


class _RecordingWorktreeMgr:
    """Minimal WorktreeManager double mirroring the real signature; returns a
    real (existing) worktree dir so ``check_cwd_exists`` passes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_worktree(self, slug, project_root, *, branch_name=None, base_branch=None):
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
    spec = await AntigravityLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)),
        AntigravityOptions(worktree=True, branch_name="feature/x"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_antigravity_prefix(tmp_path: Path):
    """With no branch_name the launcher auto-generates one under the
    ``antigravity-session-`` prefix (distinct from Claude/Codex/Terminal prefixes)."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await AntigravityLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), AntigravityOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("antigravity-session-")


@pytest.mark.asyncio
async def test_cwd_worktree_non_git_falls_back_to_root(tmp_path: Path):
    """A stale worktree=True for a non-git project is downgraded to the project
    root and the WorktreeManager is never touched."""
    mgr = _RecordingWorktreeMgr()
    spec = await AntigravityLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_project(str(tmp_path)), AntigravityOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_cwd_worktree_without_mgr_raises(tmp_path: Path):
    """worktree=True on a git project with no WorktreeManager wired fails loud
    rather than silently landing in the project root (mirrors Claude/Codex)."""
    (tmp_path / ".git").mkdir()
    with pytest.raises(RuntimeError, match="WorktreeManager"):
        await AntigravityLauncher().build(
            _make_project(str(tmp_path)),
            AntigravityOptions(worktree=True, branch_name="x"),
        )


@pytest.mark.asyncio
async def test_cwd_adhoc_worktree_is_ignored(tmp_path: Path):
    """Ad-hoc launches have no git/project context, so worktree=True is ignored
    and the bare cwd is used."""
    spec = await AntigravityLauncher().build(
        AdHocLaunch(cwd=str(tmp_path)), AntigravityOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)


# binary resolution


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    def _raise(*_a, **_kw):
        raise AntigravityBinaryNotFoundError(searched_paths=["PATH:agy"])

    monkeypatch.setattr(antigravity_launcher, "_resolve_agy_binary", _raise)
    with pytest.raises(AntigravityBinaryNotFoundError):
        await AntigravityLauncher().build(
            _make_project(str(tmp_path)), AntigravityOptions(),
        )


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "agy"

    monkeypatch.setattr(antigravity_launcher, "_resolve_agy_binary", _capture)
    await AntigravityLauncher(agy_bin_override="/my/agy").build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert captured["override"] == "/my/agy"


def test_binary_not_found_message_is_actionable():
    err = AntigravityBinaryNotFoundError(searched_paths=["PATH:agy", "fallback:/x"])
    msg = str(err)
    assert "https://antigravity.google/docs/cli" in msg
    assert "MOBAIDE_AGY_BIN" in msg
    assert err.cli_name == "antigravity"
    assert err.searched_paths == ["PATH:agy", "fallback:/x"]


# real resolver (override / env / not-found)


@pytest.fixture
def _clean_agy_env(monkeypatch):
    monkeypatch.delenv("MOBAIDE_AGY_BIN", raising=False)
    yield


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path, _clean_agy_env):
    fake = _make_executable(tmp_path / "myagy")
    monkeypatch.setenv("MOBAIDE_AGY_BIN", str(fake))
    assert _REAL_AGY_RESOLVER() == str(fake)


def test_real_resolver_prefers_override_over_env(monkeypatch, tmp_path, _clean_agy_env):
    fake_override = _make_executable(tmp_path / "from_override")
    fake_env = _make_executable(tmp_path / "from_env")
    monkeypatch.setenv("MOBAIDE_AGY_BIN", str(fake_env))
    assert _REAL_AGY_RESOLVER(override=str(fake_override)) == str(fake_override)


def test_real_resolver_raises_with_actionable_message(
    monkeypatch, tmp_path, _clean_agy_env,
):
    from mobaide.core import cli_launcher

    monkeypatch.setattr(antigravity_launcher, "_posix_fallback_candidates", lambda: [])
    monkeypatch.setattr(
        antigravity_launcher, "_windows_fallback_candidates", lambda: [],
    )
    monkeypatch.setattr(cli_launcher.shutil, "which", lambda *_a, **_kw: None)

    with pytest.raises(AntigravityBinaryNotFoundError) as exc_info:
        _REAL_AGY_RESOLVER()

    msg = str(exc_info.value)
    assert "antigravity.google" in msg
    assert "MOBAIDE_AGY_BIN" in msg
    assert exc_info.value.searched_paths


# no auth probe / options guard


@pytest.mark.asyncio
async def test_build_succeeds_with_no_credentials_anywhere(tmp_path: Path, monkeypatch):
    """``agy`` authenticates via Google Sign-In / the system keyring with no
    detectable env or config-file marker, so ``build()`` has NO pre-spawn auth
    probe: it must succeed with zero credential env vars — an unauthenticated
    CLI simply renders its login prompt inside the PTY."""
    for key in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(key, raising=False)
    spec = await AntigravityLauncher().build(
        _make_project(str(tmp_path)), AntigravityOptions(),
    )
    assert spec.argv[0] == "agy"
    assert spec.cwd == str(tmp_path)


def test_options_reject_skip_permissions_with_sandbox():
    """``skip_permissions`` auto-approves ``bypassSandbox`` actions, silently
    defeating ``--sandbox`` — the frozen dataclass rejects the pair as
    defense-in-depth behind the wire DTO's 422."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        AntigravityOptions(skip_permissions=True, sandbox=True)


def test_options_each_flag_alone_is_accepted():
    assert AntigravityOptions(skip_permissions=True).skip_permissions is True
    assert AntigravityOptions(sandbox=True).sandbox is True


def test_options_reject_flag_shaped_model_and_add_dirs():
    """``model`` / ``add_dirs`` are exec'd as argv verbatim, so a flag-shaped
    value would let a payload smuggle a curated-out flag (e.g. the blocked
    ``--dangerously-skip-permissions``) past the pair guard — the dataclass
    rejects any leading-dash value."""
    with pytest.raises(ValueError, match="CLI flag"):
        AntigravityOptions(model="--sandbox")
    with pytest.raises(ValueError, match="CLI flag"):
        AntigravityOptions(
            sandbox=True, add_dirs=("--dangerously-skip-permissions",)
        )


def test_options_reject_a_flag_shaped_name_after_a_tab():
    """``--model`` takes the part after the tab of a saved ``agy models``
    line, so that part must not look like a CLI flag either."""
    with pytest.raises(ValueError, match="CLI flag"):
        AntigravityOptions(
            model="gemini-3.8-flash\t--dangerously-skip-permissions", sandbox=True
        )


def test_options_accept_ordinary_model_and_dirs():
    opts = AntigravityOptions(
        model="Gemini 3.1 Pro (Low)", add_dirs=("/srv/x", "C:/y")
    )
    assert opts.model == "Gemini 3.1 Pro (Low)"
    assert opts.add_dirs == ("/srv/x", "C:/y")


# list_models (memoized ``agy models`` enumeration)


@pytest.mark.asyncio
async def test_list_models_parses_output_and_memoizes(monkeypatch):
    """``list_models`` strips blank lines / whitespace from ``agy models``
    output, memoizes the result, and never re-invokes the runner on a warm
    cache — ``cached_models`` is the non-spawning capability view."""
    calls = {"n": 0}

    def _runner(_binary):
        calls["n"] += 1
        return "A\nB\n\n C \n"

    monkeypatch.setattr(antigravity_launcher, "_run_agy_models", _runner)
    launcher = AntigravityLauncher()
    assert launcher.cached_models is None

    first = await launcher.list_models()
    second = await launcher.list_models()

    assert first == ["A", "B", "C"]
    assert second == ["A", "B", "C"]
    assert launcher.cached_models == ("A", "B", "C")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_list_models_takes_the_display_name_of_an_id_and_name_line(monkeypatch):
    """agy 1.5 prints ``<model id><TAB><display name>``; ``--model`` takes
    the display name, so the id must not reach the wizard. A name-only line
    (older builds) is kept, and a line without a name is dropped."""
    output = (
        "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        "claude-sonnet-4-6\t Claude Sonnet 4.6 (Thinking) \n"
        "Gemini 3.1 Pro (Low)\n"
        "orphan-id\t\n"
    )
    monkeypatch.setattr(antigravity_launcher, "_run_agy_models", lambda _binary: output)
    launcher = AntigravityLauncher()

    models = await launcher.list_models()

    assert models == [
        "Gemini 3.8 Flash (High)",
        "Claude Sonnet 4.6 (Thinking)",
        "Gemini 3.1 Pro (Low)",
    ]
    assert launcher.cached_models == tuple(models)


@pytest.mark.asyncio
async def test_list_models_with_only_ids_and_no_names_returns_none(monkeypatch):
    monkeypatch.setattr(
        antigravity_launcher, "_run_agy_models", lambda _binary: "a-id\t\nb-id\t  \n"
    )
    launcher = AntigravityLauncher()

    assert await launcher.list_models() is None
    assert launcher.cached_models is None


@pytest.mark.asyncio
async def test_list_models_none_output_returns_none_without_memoizing(monkeypatch):
    """A failed run (non-zero exit → runner returns None) yields ``None`` and
    must NOT memoize: the next call retries the runner instead of serving a
    cached failure."""
    calls = {"n": 0}

    def _runner(_binary):
        calls["n"] += 1
        return None

    monkeypatch.setattr(antigravity_launcher, "_run_agy_models", _runner)
    launcher = AntigravityLauncher()

    assert await launcher.list_models() is None
    assert launcher.cached_models is None
    assert await launcher.list_models() is None
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_list_models_missing_binary_returns_none(monkeypatch):
    def _raise(*_a, **_kw):
        raise AntigravityBinaryNotFoundError(searched_paths=["PATH:agy"])

    def _boom(_binary):
        raise AssertionError("runner must not be invoked when the binary is missing")

    monkeypatch.setattr(antigravity_launcher, "_resolve_agy_binary", _raise)
    monkeypatch.setattr(antigravity_launcher, "_run_agy_models", _boom)
    launcher = AntigravityLauncher()

    assert await launcher.list_models() is None
    assert launcher.cached_models is None

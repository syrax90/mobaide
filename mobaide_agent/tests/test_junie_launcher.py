from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobaide.core import hook_settings, junie_launcher
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



_HELP_WITH_CONFIG_LOCATION = (
    "Usage: junie [OPTIONS]\n"
    "  --config-location=<path>  Extra config file to load\n"
    "  --brave                   Run commands without asking\n"
)
_HELP_WITHOUT_CONFIG_LOCATION = "Usage: junie [OPTIONS]\n  --brave\n"


class _HelpProbe:
    """Stands in for ``cli_help_text``; ``None`` is a probe that failed."""

    def __init__(self, text: str | None) -> None:
        self.text = text
        self.calls = 0

    def __call__(self, _binary: str, _env, _timeout: float) -> str | None:
        self.calls += 1
        return self.text


class _HookHost:
    """A real ``junie`` file, a scripted help probe, and the hooks temp dir
    redirected under ``tmp_path``."""

    def __init__(self, monkeypatch, tmp_path: Path) -> None:
        self.binary = str(_make_executable(tmp_path / "bin" / "junie"))
        self.probe = _HelpProbe(_HELP_WITH_CONFIG_LOCATION)
        self.temp = tmp_path / "tmp"
        self.temp.mkdir()
        monkeypatch.setattr(junie_launcher, "_resolve_junie_binary", lambda *_a, **_kw: self.binary)
        monkeypatch.setattr(junie_launcher, "cli_help_text", self.probe)
        monkeypatch.setattr(hook_settings.tempfile, "gettempdir", lambda: str(self.temp))
        # The agent's own env may carry a token when it runs inside a session.
        monkeypatch.delenv(hook_settings.HOOK_TOKEN_ENV, raising=False)

    def hook_files(self) -> list[Path]:
        return sorted(self.temp.rglob("*.json"))


@pytest.fixture
def hook_host(monkeypatch, tmp_path) -> _HookHost:
    return _HookHost(monkeypatch, tmp_path)


def _hooked_launcher() -> JunieLauncher:
    return JunieLauncher(hook_port=9100, hook_token="tok-secret")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows sessions run without hooks")
async def test_a_terminal_session_gets_its_hook_config(hook_host, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    spec = await _hooked_launcher().build(
        _make_workspace(str(workspace)), JunieOptions(brave=True), session_id="sid-1",
    )

    [created] = spec.created_files
    assert hook_host.hook_files() == [Path(created)]
    assert spec.argv == [hook_host.binary, f"--config-location={created}", "--brave"]
    text = Path(created).read_text(encoding="utf-8")
    assert json.loads(text) == hook_settings.junie_hook_config("sid-1", 9100)
    # The token reaches the poster only through the Junie process env:
    # argv and the config file are readable by other local users.
    assert spec.env[hook_settings.HOOK_TOKEN_ENV] == "tok-secret"
    assert spec.env["MOBAIDE_SESSION_ID"] == "sid-1"
    assert "tok-secret" not in text
    assert not any("tok-secret" in arg for arg in spec.argv)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("launcher_kwargs", "session_id", "help_text", "platform"),
    [
        ({"hook_port": 9100, "hook_token": "tok"}, None, _HELP_WITH_CONFIG_LOCATION, "linux"),
        ({}, "sid-1", _HELP_WITH_CONFIG_LOCATION, "linux"),
        ({"hook_port": 9100, "hook_token": "tok"}, "sid-1", _HELP_WITHOUT_CONFIG_LOCATION, "linux"),
        ({"hook_port": 9100, "hook_token": "tok"}, "sid-1", None, "linux"),
        ({"hook_port": 9100, "hook_token": "tok"}, "sid-1", _HELP_WITH_CONFIG_LOCATION, "win32"),
    ],
    ids=["no-session-id", "no-hook-endpoint", "old-junie", "help-probe-failed", "windows"],
)
async def test_without_hook_support_the_session_launches_hookless(
    hook_host, tmp_path, monkeypatch, launcher_kwargs, session_id, help_text, platform,
):
    # An older Junie exits with "no such option" on --config-location, so the
    # flag may appear only when the binary's help lists it.
    hook_host.probe.text = help_text
    monkeypatch.setattr(sys, "platform", platform)

    spec = await JunieLauncher(**launcher_kwargs).build(
        _make_workspace(str(tmp_path)), JunieOptions(), session_id=session_id,
    )

    assert spec.argv == [hook_host.binary]
    assert spec.created_files == []
    assert hook_settings.HOOK_TOKEN_ENV not in spec.env
    assert hook_host.hook_files() == []


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows sessions run without hooks")
async def test_a_failed_config_write_launches_hookless(hook_host, tmp_path, monkeypatch):
    def full_disk(*_a, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(junie_launcher, "write_junie_session_hooks", full_disk)

    spec = await _hooked_launcher().build(
        _make_workspace(str(tmp_path)), JunieOptions(), session_id="sid-1",
    )

    assert spec.argv == [hook_host.binary]
    assert spec.created_files == []
    assert hook_settings.HOOK_TOKEN_ENV not in spec.env


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows sessions run without hooks")
async def test_a_build_that_fails_after_the_write_removes_the_config(
    hook_host, tmp_path, monkeypatch,
):
    # A session that never spawns never reaches the session manager's cleanup.
    async def no_worktree(*_a, **_kw):
        raise RuntimeError("worktree failed")

    launcher = _hooked_launcher()
    monkeypatch.setattr(launcher, "resolve_cwd", no_worktree)

    with pytest.raises(RuntimeError):
        await launcher.build(_make_workspace(str(tmp_path)), JunieOptions(), session_id="sid-1")

    assert hook_host.hook_files() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "help_text",
    [_HELP_WITH_CONFIG_LOCATION, _HELP_WITHOUT_CONFIG_LOCATION, None],
    ids=["supported", "unsupported", "probe-failed"],
)
async def test_the_help_probe_answer_stands_for_the_next_launch(hook_host, help_text):
    # The probe starts a JVM: a launch right after another must not pay for
    # it again, whatever the first answer was.
    hook_host.probe.text = help_text
    launcher = _hooked_launcher()

    first = await launcher.supports_config_location({})
    second = await launcher.supports_config_location({})

    assert first == second == (help_text == _HELP_WITH_CONFIG_LOCATION)
    assert hook_host.probe.calls == 1


@pytest.mark.asyncio
async def test_only_a_negative_answer_expires(hook_host, monkeypatch):
    # Junie updates itself by switching a ``current`` link, so the shim path
    # stays the same: "no flag" (or a failed probe) must be asked again later.
    # A newer Junie keeps the flag, so "yes" stands.
    clock = [1000.0]
    monkeypatch.setattr(junie_launcher, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    retry = junie_launcher._HELP_PROBE_RETRY_SECONDS
    hook_host.probe.text = _HELP_WITHOUT_CONFIG_LOCATION
    launcher = _hooked_launcher()
    assert await launcher.supports_config_location({}) is False

    hook_host.probe.text = _HELP_WITH_CONFIG_LOCATION
    clock[0] += retry - 1
    assert await launcher.supports_config_location({}) is False
    assert hook_host.probe.calls == 1

    clock[0] += 2
    assert await launcher.supports_config_location({}) is True
    assert hook_host.probe.calls == 2

    hook_host.probe.text = None
    clock[0] += retry * 10
    assert await launcher.supports_config_location({}) is True
    assert hook_host.probe.calls == 2


@pytest.mark.asyncio
async def test_concurrent_launches_share_one_probe(hook_host, monkeypatch):
    # Several sessions starting at once (Auto Scan) must not start one JVM each.
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_probe(binary: str, _env, _timeout: float) -> str:
        calls.append(binary)
        started.set()
        release.wait(10)
        return _HELP_WITH_CONFIG_LOCATION

    monkeypatch.setattr(junie_launcher, "cli_help_text", slow_probe)
    launcher = _hooked_launcher()

    first = asyncio.create_task(launcher.supports_config_location({}))
    second = asyncio.create_task(launcher.supports_config_location({}))
    assert await asyncio.to_thread(started.wait, 10)
    for _ in range(10):
        await asyncio.sleep(0)  # let the second launch reach the probe
    release.set()

    assert await asyncio.gather(first, second) == [True, True]
    assert calls == [hook_host.binary]

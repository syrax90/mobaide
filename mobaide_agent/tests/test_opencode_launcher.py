from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from mobaide.core import opencode_launcher
from mobaide.core.claude_launcher import AdHocLaunch
from mobaide.core.opencode_launcher import (
    INITIAL_PROMPT_MAX,
    OpenCodeBinaryNotFoundError,
    OpenCodeLauncher,
    OpenCodeOptions,
)
from mobaide.core.workspaces_config import WorkspaceConfig
from tests.conftest import skip_if_not_linux

# Captured before the autouse ``_pin_opencode_binary`` fixture swaps it, so
# the resolver tests below exercise the real implementation.
_REAL_OPENCODE_RESOLVER = opencode_launcher._resolve_opencode_binary


def _make_workspace(path: str) -> WorkspaceConfig:
    return WorkspaceConfig(name="test", path=path)


def _missing_binary(*_a, **_kw):
    raise OpenCodeBinaryNotFoundError(searched_paths=["PATH:opencode"])


# argv


@pytest.mark.asyncio
async def test_argv_default_is_bare_opencode(tmp_path: Path):
    """Default options launch a bare ``opencode``: no ``--auto`` (OpenCode asks
    before it acts) and no ``--prompt``."""
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(),
    )
    assert spec.argv == ["opencode"]


@pytest.mark.asyncio
async def test_argv_auto_approve_adds_the_auto_flag(tmp_path: Path):
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(auto_approve=True),
    )
    assert spec.argv == ["opencode", "--auto"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    ["--auto", "-h", "fix the bug\nthen run --auto", "say \"hi\" && rm -rf ~"],
    ids=["flag-shaped", "short-flag", "multi-line", "shell-syntax"],
)
async def test_argv_prompt_is_one_element_that_can_never_read_as_a_flag(
    tmp_path: Path, prompt: str,
):
    """The first message is client text exec'd as argv. It travels as ONE
    ``--prompt=<text>`` element, so a prompt that is (or starts with) a flag
    cannot turn on ``--auto`` for a session the user created without it."""
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(initial_prompt=prompt),
    )
    assert spec.argv == ["opencode", f"--prompt={prompt}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [None, ""], ids=["none", "empty"])
async def test_argv_no_prompt_adds_no_prompt_flag(tmp_path: Path, prompt):
    """An empty first message must not reach OpenCode as ``--prompt=``, which
    would send an empty turn."""
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)),
        OpenCodeOptions(auto_approve=True, initial_prompt=prompt),
    )
    assert spec.argv == ["opencode", "--auto"]


@pytest.mark.asyncio
async def test_argv_connect_runs_the_provider_sign_in_and_nothing_else(tmp_path: Path):
    """A sign-in is not an agent session: neither ``--auto`` nor the first
    message may reach ``opencode auth login``."""
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)),
        OpenCodeOptions(connect=True, auto_approve=True, initial_prompt="fix the bug"),
    )
    assert spec.argv == ["opencode", "auth", "login"]


def test_options_cap_the_prompt_length():
    """The prompt is a single argv element, so its length is capped below the
    argv limit of every supported OS — the frozen dataclass rejects a longer
    one as defense-in-depth behind the wire DTO's 422."""
    assert OpenCodeOptions(initial_prompt="x" * INITIAL_PROMPT_MAX).initial_prompt
    with pytest.raises(ValueError, match="initial_prompt"):
        OpenCodeOptions(initial_prompt="x" * (INITIAL_PROMPT_MAX + 1))


# env


@pytest.mark.asyncio
async def test_env_adds_no_claude_specific_vars(tmp_path: Path, monkeypatch):
    """OpenCode copies the inherited env but ADDS none of Claude's hook
    plumbing and — even for a workspace carrying a Linear key — no injected
    ``LINEAR_API_KEY``."""
    for key in (
        "ENABLE_PROMPT_CACHING_1H", "MOBAIDE_SESSION_ID",
        "MOBAIDE_HOOK_URL", "MOBAIDE_HOOK_TOKEN", "LINEAR_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    proj = WorkspaceConfig(name="t", path=str(tmp_path), linear_api_key="lin_secret")
    spec = await OpenCodeLauncher().build(proj, OpenCodeOptions(), session_id="s-1")
    assert "MOBAIDE_SESSION_ID" not in spec.env
    assert "MOBAIDE_HOOK_TOKEN" not in spec.env
    assert "LINEAR_API_KEY" not in spec.env
    assert spec.created_files == []


@pytest.mark.asyncio
async def test_env_prepends_binary_dir_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        opencode_launcher, "_resolve_opencode_binary",
        lambda *_a, **_k: os.path.join("/opt", "oc", "bin", "opencode"),
    )
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(),
    )
    assert spec.env["PATH"].split(os.pathsep)[0] == os.path.join("/opt", "oc", "bin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("own", "expected"),
    [
        (None, {"tabs": {"mode": "off"}}),
        ('{"mouse": false}', {"mouse": False, "tabs": {"mode": "off"}}),
        ('{"tabs": {"mode": "on"}}', {"tabs": {"mode": "on"}}),
    ],
    ids=["unset", "merged-with-own-settings", "own-tabs-choice-wins"],
)
async def test_terminal_env_turns_the_shared_tab_set_off(
    tmp_path: Path, monkeypatch, own, expected,
):
    """OpenCode's status report covers every tab of a folder, and all TUIs in
    one folder share the tab set: with tabs on, a request waiting in one
    terminal session alerts in every other session of the workspace."""
    if own is None:
        monkeypatch.delenv("OPENCODE_CLI_CONFIG_CONTENT", raising=False)
    else:
        monkeypatch.setenv("OPENCODE_CLI_CONFIG_CONTENT", own)
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(),
    )
    assert json.loads(spec.env["OPENCODE_CLI_CONFIG_CONTENT"]) == expected


@pytest.mark.asyncio
async def test_terminal_env_leaves_unreadable_tui_settings_alone(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CLI_CONFIG_CONTENT", "{not json")
    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(),
    )
    assert spec.env["OPENCODE_CLI_CONFIG_CONTENT"] == "{not json"


@pytest.mark.asyncio
async def test_env_pwd_is_the_session_folder_not_the_agent_s_own(tmp_path: Path, monkeypatch):
    """OpenCode takes its folder from ``PWD`` when it is set, and the agent's
    inherited ``PWD`` is the folder the agent was started in. For a worktree
    session it must be the worktree, not the workspace root."""
    monkeypatch.setenv("PWD", str(tmp_path / "agent-start-folder"))
    (tmp_path / ".git").mkdir()
    spec = await OpenCodeLauncher(worktree_mgr=_RecordingWorktreeMgr()).build(  # type: ignore[arg-type]
        _make_workspace(str(tmp_path)), OpenCodeOptions(worktree=True, branch_name="feature/x"),
    )
    assert spec.cwd != str(tmp_path)
    assert spec.env["PWD"] == spec.cwd


def test_build_env_works_without_the_binary(tmp_path: Path, monkeypatch):
    """A chat session builds its environment before OpenCode is looked up, so
    a missing binary must not fail ``build_env`` — the chat spec reports it
    with its own install hint instead."""
    monkeypatch.setattr(opencode_launcher, "_resolve_opencode_binary", _missing_binary)
    monkeypatch.setenv("PATH", "/usr/bin")

    env = OpenCodeLauncher().build_env(AdHocLaunch(cwd=str(tmp_path)))

    assert env["PATH"] == "/usr/bin"


# Code Mode


_CODE_MODE_OFF = {"action": "execute", "resource": "*", "effect": "deny"}


def _opencode_reports(monkeypatch, version):
    """Pin what ``opencode --version`` reports, without running a CLI."""
    asked: list[tuple[str, dict[str, str] | None]] = []

    def _version(cli_bin, env=None):
        asked.append((cli_bin, env))
        return version

    monkeypatch.setattr(opencode_launcher, "cli_version", _version)
    return asked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("own", "expected"),
    [
        (None, {"permissions": [_CODE_MODE_OFF]}),
        (
            '{"model": "anthropic/x", "permissions": [{"action": "bash", "effect": "ask"}]}',
            {
                "model": "anthropic/x",
                "permissions": [{"action": "bash", "effect": "ask"}, _CODE_MODE_OFF],
            },
        ),
    ],
    ids=["unset", "after-own-rules"],
)
async def test_code_mode_off_denies_execute_on_opencode_2(
    tmp_path: Path, monkeypatch, own, expected,
):
    """OpenCode appends the inline rules to the user's own, so the user's
    config and rules stay as they were."""
    if own is None:
        monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    else:
        monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", own)
    _opencode_reports(monkeypatch, (2, 0, 1))

    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(code_mode=False),
    )

    assert json.loads(spec.env["OPENCODE_CONFIG_CONTENT"]) == expected


@pytest.mark.asyncio
async def test_code_mode_off_runs_the_tui_on_its_own_server(tmp_path: Path, monkeypatch):
    """The TUI otherwise reads its settings from the background service that
    every OpenCode TUI on the PC shares: the rule would reach the other
    sessions, or none at all when the service already runs."""
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    _opencode_reports(monkeypatch, (2, 0, 1))

    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)),
        OpenCodeOptions(code_mode=False, auto_approve=True, initial_prompt="fix it"),
    )

    assert spec.argv == ["opencode", "--standalone", "--auto", "--prompt=fix it"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version", [(1, 9, 9), None], ids=["opencode-1-refuses-the-key", "version-unknown"],
)
async def test_code_mode_off_leaves_the_config_alone_without_opencode_2(
    tmp_path: Path, monkeypatch, version,
):
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    _opencode_reports(monkeypatch, version)

    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(code_mode=False),
    )

    assert "OPENCODE_CONFIG_CONTENT" not in spec.env
    assert "--standalone" not in spec.argv


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "own", ["{not json", "[1, 2]", '{"permissions": {"execute": "deny"}}'],
    ids=["unreadable", "not-an-object", "permissions-not-a-list"],
)
async def test_code_mode_off_leaves_an_unusable_own_config_alone(
    tmp_path: Path, monkeypatch, own,
):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", own)
    _opencode_reports(monkeypatch, (2, 0, 0))

    spec = await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(code_mode=False),
    )

    assert spec.env["OPENCODE_CONFIG_CONTENT"] == own
    assert "--standalone" not in spec.argv


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [OpenCodeOptions(), OpenCodeOptions(code_mode=False, connect=True)],
    ids=["code-mode-on", "sign-in"],
)
async def test_code_mode_rule_only_for_a_session_that_turns_it_off(
    tmp_path: Path, monkeypatch, options,
):
    """Code Mode on is OpenCode's own default, and a sign-in is not an agent
    session: neither runs ``opencode --version``."""
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    asked = _opencode_reports(monkeypatch, (2, 0, 0))

    spec = await OpenCodeLauncher().build(_make_workspace(str(tmp_path)), options)

    assert "OPENCODE_CONFIG_CONTENT" not in spec.env
    assert "--standalone" not in spec.argv
    assert asked == []


@pytest.mark.asyncio
async def test_code_mode_version_check_finds_the_node_next_to_an_npm_opencode(
    tmp_path: Path, monkeypatch,
):
    """An npm install of OpenCode is a Node script whose ``node`` sits next
    to it; without that folder on ``PATH`` the check reads no version."""
    binary = os.path.join("/opt", "opencode", "bin", "opencode")
    monkeypatch.setattr(opencode_launcher, "_resolve_opencode_binary", lambda *_a, **_k: binary)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    asked = _opencode_reports(monkeypatch, (2, 0, 0))

    await OpenCodeLauncher().build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(code_mode=False),
    )

    [(cli_bin, env)] = asked
    assert cli_bin == binary
    assert env is not None
    assert env["PATH"].split(os.pathsep) == [os.path.join("/opt", "opencode", "bin"), "/usr/bin"]


# cwd


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
    """worktree=True on a git workspace resolves cwd to the created worktree (not
    the workspace root) and hands the user's branch and base to the manager."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await OpenCodeLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_workspace(str(tmp_path)),
        OpenCodeOptions(worktree=True, branch_name="feature/x", base_branch="origin/main"),
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0]["branch_name"] == "feature/x"
    assert mgr.calls[0]["base_branch"] == "origin/main"
    assert spec.cwd != str(tmp_path)
    assert "feature-x" in spec.cwd


@pytest.mark.asyncio
async def test_cwd_worktree_blank_branch_uses_opencode_prefix(tmp_path: Path):
    """With no branch_name the launcher auto-generates one under the
    ``opencode-session-`` prefix (distinct from the other agents' prefixes)."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    await OpenCodeLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_workspace(str(tmp_path)), OpenCodeOptions(worktree=True),
    )
    branch = mgr.calls[0]["branch_name"]
    assert isinstance(branch, str)
    assert branch.startswith("opencode-session-")


@pytest.mark.asyncio
async def test_cwd_adhoc_worktree_is_ignored(tmp_path: Path):
    """Ad-hoc launches have no git/workspace context, so worktree=True is ignored
    and the bare cwd is used."""
    mgr = _RecordingWorktreeMgr()
    spec = await OpenCodeLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        AdHocLaunch(cwd=str(tmp_path)), OpenCodeOptions(worktree=True),
    )
    assert spec.cwd == str(tmp_path)
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_cwd_connect_never_creates_a_worktree(tmp_path: Path):
    """A sign-in changes nothing in the workspace: a ``worktree`` flag that
    arrives with ``connect`` must not leave a branch and a folder behind."""
    (tmp_path / ".git").mkdir()
    mgr = _RecordingWorktreeMgr()
    spec = await OpenCodeLauncher(worktree_mgr=mgr).build(  # type: ignore[arg-type]
        _make_workspace(str(tmp_path)),
        OpenCodeOptions(connect=True, worktree=True, branch_name="feature/x"),
    )
    assert mgr.calls == []
    assert spec.cwd == str(tmp_path)
    assert spec.env["PWD"] == str(tmp_path)


# binary resolution


@pytest.mark.asyncio
async def test_build_propagates_binary_not_found(tmp_path: Path, monkeypatch):
    """The create paths map the error to ``<cli_name>_cli_missing``, so the
    name must be OpenCode's own wire string."""
    monkeypatch.setattr(opencode_launcher, "_resolve_opencode_binary", _missing_binary)
    with pytest.raises(OpenCodeBinaryNotFoundError) as exc_info:
        await OpenCodeLauncher().build(
            _make_workspace(str(tmp_path)), OpenCodeOptions(),
        )
    assert exc_info.value.cli_name == "opencode"


@pytest.mark.asyncio
async def test_launcher_forwards_override_to_resolver(tmp_path: Path, monkeypatch):
    captured: dict[str, str | None] = {}

    def _capture(override=None):
        captured["override"] = override
        return "opencode"

    monkeypatch.setattr(opencode_launcher, "_resolve_opencode_binary", _capture)
    await OpenCodeLauncher(opencode_bin_override="/my/opencode").build(
        _make_workspace(str(tmp_path)), OpenCodeOptions(),
    )
    assert captured["override"] == "/my/opencode"


# real resolver (env / install-script location)


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(stat.S_IRWXU)
    return path


def test_real_resolver_uses_env_var_override(monkeypatch, tmp_path):
    fake = _make_executable(tmp_path / "myopencode")
    monkeypatch.setenv("MOBAIDE_OPENCODE_BIN", str(fake))
    assert _REAL_OPENCODE_RESOLVER() == str(fake)


@skip_if_not_linux
def test_real_resolver_finds_the_install_script_location_off_path(monkeypatch, tmp_path):
    """OpenCode's install script lands in ``~/.opencode/bin``, which a service
    started by launchd / systemd does not have on PATH."""
    home = tmp_path / "home"
    empty = tmp_path / "empty-path"
    empty.mkdir()
    installed = _make_executable(home / ".opencode" / "bin" / "opencode")
    monkeypatch.delenv("MOBAIDE_OPENCODE_BIN", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(empty))

    assert _REAL_OPENCODE_RESOLVER() == str(installed)

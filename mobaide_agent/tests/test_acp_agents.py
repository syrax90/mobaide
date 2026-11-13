"""Tests for the ACP launch specs: which command starts the Claude, Codex,
Antigravity and GitHub Copilot chat agents (including the pinned install on
first use and the ``agy help acp`` probe with its cache), the create-session
options as ACP config intents (Antigravity's model name split into model and
thinking level), the launch plan (Codex's shared app-server included) and the
``capabilities.chat`` map."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mobaide.acp import agents, codex_bridge
from mobaide.acp.agents import (
    CODEX_USER_CODE_PATTERN,
    COPILOT_USER_CODE_PATTERN,
    AntigravityChatSpec,
    ChatAgentUnavailableError,
    ClaudeChatSpec,
    CodexChatSpec,
    ConfigIntent,
    CopilotChatSpec,
    CursorChatSpec,
    OpenCodeChatSpec,
    antigravity_config_intents,
    antigravity_sign_in_methods,
    build_chat_agents,
    build_chat_capabilities,
    claude_config_intents,
    claude_sign_in_methods,
    codex_config_intents,
    codex_mode,
    codex_sign_in_methods,
    copilot_sign_in_methods,
    cursor_sign_in_methods,
    fake_agent_argv,
    fake_login_argv,
    resolve_antigravity_config,
)
from mobaide.acp.install import INSTALLED_MARKER
from mobaide.acp.trust import (
    AntigravityFolderTrust,
    CodexFolderTrust,
    CopilotFolderTrust,
    CursorFolderTrust,
)
from mobaide.core.antigravity_launcher import AntigravityBinaryNotFoundError, AntigravityOptions
from mobaide.core.claude_launcher import ClaudeBinaryNotFoundError, ClaudeOptions
from mobaide.core.cli_launcher import AdHocLaunch, LaunchSpec
from mobaide.core import codex_auth
from mobaide.core.codex_launcher import CodexBinaryNotFoundError, CodexOptions
from mobaide.core.copilot_launcher import CopilotBinaryNotFoundError, CopilotOptions
from mobaide.core.cursor_launcher import CursorBinaryNotFoundError, CursorOptions
from mobaide.core.opencode_launcher import OpenCodeBinaryNotFoundError, OpenCodeOptions
from mobaide.core.projects_config import ProjectConfig
from tests.conftest import skip_if_not_linux
from tests.test_acp_install import fake_npm


class FakeLauncher:
    """The two ``ClaudeLauncher`` calls a chat spec makes."""

    def __init__(
        self, binary: str | None = "/opt/claude/bin/claude", env: dict[str, str] | None = None
    ) -> None:
        self.binary = binary
        self.env = env or {}
        self.built: list[tuple[object, ClaudeOptions, str | None]] = []

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise ClaudeBinaryNotFoundError(["/nowhere/claude"])
        return self.binary

    async def build(self, target, options, session_id=None) -> LaunchSpec:
        self.built.append((target, options, session_id))
        return LaunchSpec(
            argv=["claude"],
            cwd=target.cwd,
            env={"PATH": "/usr/bin", "LAUNCHER": "1", **self.env},
            created_files=["/work/.mcp.json"],
        )


class FakeCodexLauncher:
    """The two ``CodexLauncher`` calls a chat spec makes."""

    def __init__(
        self, binary: str | None = "/opt/codex/bin/codex", env: dict[str, str] | None = None
    ) -> None:
        self.binary = binary
        self.env = env or {}
        self.require_auth: list[bool] = []

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise CodexBinaryNotFoundError(["/nowhere/codex"])
        return self.binary

    async def build(self, target, options, session_id=None, *, require_auth=True) -> LaunchSpec:
        self.require_auth.append(require_auth)
        cwd = target.path if isinstance(target, ProjectConfig) else target.cwd
        return LaunchSpec(
            argv=["codex"], cwd=cwd, env={"PATH": "/usr/bin", "LAUNCHER": "1", **self.env}
        )


class FakeAgyLauncher:
    """The two ``AntigravityLauncher`` calls a chat spec makes."""

    def __init__(self, binary: str | None = "/opt/agy/bin/agy") -> None:
        self.binary = binary
        self.built: list[tuple[object, AntigravityOptions]] = []

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise AntigravityBinaryNotFoundError(searched_paths=["PATH:agy"])
        return self.binary

    async def build(self, target, options, session_id=None) -> LaunchSpec:
        self.built.append((target, options))
        cwd = target.path if isinstance(target, ProjectConfig) else target.cwd
        return LaunchSpec(argv=["agy"], cwd=cwd, env={"PATH": "/usr/bin", "LAUNCHER": "1"})


class FakeCopilotLauncher:
    """The three ``CopilotLauncher`` calls a chat spec makes."""

    def __init__(
        self, binary: str | None = "/opt/copilot/bin/copilot", env: dict[str, str] | None = None
    ) -> None:
        self.binary = binary
        self.env = env or {}
        self.resolved: list[tuple[object, CopilotOptions]] = []

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise CopilotBinaryNotFoundError(searched_paths=["PATH:copilot"])
        return self.binary

    async def resolve_cwd(self, target, options) -> str:
        self.resolved.append((target, options))
        return target.path if isinstance(target, ProjectConfig) else target.cwd

    def build_env(self, target) -> dict[str, str]:
        return {"PATH": "/usr/bin", "LAUNCHER": "1", **self.env}


class FakeCursorLauncher:
    """The three ``CursorLauncher`` calls a chat spec makes."""

    def __init__(
        self, binary: str | None = "/opt/cursor/bin/cursor-agent", env: dict[str, str] | None = None
    ) -> None:
        self.binary = binary
        self.env = env or {}
        self.resolved: list[tuple[object, CursorOptions]] = []

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise CursorBinaryNotFoundError(searched_paths=["PATH:cursor-agent"])
        return self.binary

    async def resolve_cwd(self, target, options) -> str:
        self.resolved.append((target, options))
        return target.path if isinstance(target, ProjectConfig) else target.cwd

    def build_env(self, target) -> dict[str, str]:
        return {"PATH": "/usr/bin", "LAUNCHER": "1", **self.env}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        agents.CLAUDE_ACP_COMMAND_ENV,
        agents.CODEX_ACP_COMMAND_ENV,
        agents.ANTIGRAVITY_ACP_COMMAND_ENV,
        agents.COPILOT_ACP_COMMAND_ENV,
        agents.CURSOR_ACP_COMMAND_ENV,
        agents.REAL_IN_TEST_ENV,
        "CLAUDE_CODE_EXECUTABLE",
        "CODEX_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch) -> Path:
    """An empty PATH directory and no npm fallback locations."""
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setattr(agents, "posix_npm_fallbacks", lambda _name: [])
    monkeypatch.setattr(agents, "windows_npm_fallbacks", lambda _name: [])
    return directory


def _install(directory: Path, name: str) -> str:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _installed_adapter(root: Path, adapter) -> Path:
    """A complete pinned install of ``adapter`` under ``root``; returns its entry."""
    base = root / adapter.folder_name
    package = base.joinpath("node_modules", *adapter.package.split("/"))
    (package / "dist").mkdir(parents=True)
    entry = package / "dist" / "index.js"
    entry.write_text("// adapter\n", encoding="utf-8")
    (package / "package.json").write_text(
        json.dumps({"bin": {adapter.bin_name: "dist/index.js"}}), encoding="utf-8"
    )
    (base / INSTALLED_MARKER).write_text(adapter.version, encoding="utf-8")
    return entry.resolve()


@pytest.fixture(autouse=True)
def _no_shared_codex(monkeypatch):
    """No Codex app-server answers unless a test says so (never the host's)."""
    monkeypatch.setattr(codex_bridge, "socket_reachable", lambda _path, timeout=1.0: False)


def _socket_answers(monkeypatch) -> list[str]:
    probed: list[str] = []

    def reachable(path: str, timeout: float = 1.0) -> bool:
        probed.append(path)
        return True

    monkeypatch.setattr(codex_bridge, "socket_reachable", reachable)
    return probed


class TestCommandResolution:

    def test_env_override_beats_test_mode(self, bin_dir, monkeypatch):
        monkeypatch.setenv(agents.CLAUDE_ACP_COMMAND_ENV, "env-adapter --y")
        spec = ClaudeChatSpec(FakeLauncher(), test_mode=True)

        assert spec.command() == ["env-adapter", "--y"]

    def test_test_mode_runs_the_fake_agent(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher(), test_mode=True)

        argv = spec.command()

        assert argv[0] == sys.executable
        assert os.path.basename(argv[1]) == "fake_agent.py"
        assert os.path.isfile(argv[1])

    @skip_if_not_linux
    def test_real_flag_in_test_mode_resolves_the_real_adapter(self, bin_dir, monkeypatch):
        monkeypatch.setenv(agents.REAL_IN_TEST_ENV, "1")
        adapter = _install(bin_dir, agents.CLAUDE_ACP_BIN)
        spec = ClaudeChatSpec(FakeLauncher(), test_mode=True)

        assert spec.command() == [adapter]

    @skip_if_not_linux
    def test_adapter_on_path_is_preferred_over_the_pinned_install(self, bin_dir, tmp_path):
        adapter = _install(bin_dir, agents.CLAUDE_ACP_BIN)
        _install(bin_dir, "node")
        adapters = tmp_path / "adapters"
        _installed_adapter(adapters, agents.CLAUDE_ADAPTER)
        spec = ClaudeChatSpec(FakeLauncher(), adapters_dir=adapters)

        assert spec.command() == [adapter]

    @skip_if_not_linux
    def test_the_pinned_install_runs_with_node_not_npx(self, bin_dir, tmp_path):
        node = _install(bin_dir, "node")
        _install(bin_dir, "npx")
        adapters = tmp_path / "adapters"
        entry = _installed_adapter(adapters, agents.CLAUDE_ADAPTER)
        spec = ClaudeChatSpec(FakeLauncher(), adapters_dir=adapters)

        assert spec.command() == [node, str(entry)]
        assert spec.available() is True

    @skip_if_not_linux
    def test_node_and_npm_without_an_install_mean_install_on_first_use(self, bin_dir, tmp_path):
        _install(bin_dir, "node")
        _install(bin_dir, "npm")
        spec = ClaudeChatSpec(FakeLauncher(), adapters_dir=tmp_path / "adapters")

        assert spec.command() is None
        assert spec.available() is True

    @skip_if_not_linux
    @pytest.mark.parametrize(
        ("tools", "with_adapters_dir"),
        [((), True), (("npx",), True), (("node",), True), (("node", "npm"), False)],
        ids=["nothing", "npx-only", "node-without-npm", "no-adapters-dir"],
    )
    def test_unavailable(self, bin_dir, tmp_path, tools, with_adapters_dir):
        for tool in tools:
            _install(bin_dir, tool)
        adapters = tmp_path / "adapters" if with_adapters_dir else None
        spec = ClaudeChatSpec(FakeLauncher(), adapters_dir=adapters)

        assert spec.command() is None
        assert spec.available() is False

    @skip_if_not_linux
    def test_resolution_is_cached_for_the_ttl(self, bin_dir, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(agents.time, "monotonic", lambda: now[0])
        spec = ClaudeChatSpec(FakeLauncher())
        assert spec.available() is False

        _install(bin_dir, agents.CLAUDE_ACP_BIN)
        now[0] += agents.AVAILABILITY_TTL - 1
        assert spec.available() is False

        now[0] += 2
        assert spec.available() is True


class TestPrepare:
    async def test_plan_carries_the_options_and_the_launcher_env(self, bin_dir):
        launcher = FakeLauncher()
        spec = ClaudeChatSpec(launcher, command_override="adapter")
        options = ClaudeOptions(
            skip_permissions=False,
            model="smart",
            append_system_prompt="Be brief.",
            initial_prompt="Run the tests",
            title="Tests",
        )

        plan = await spec.prepare(AdHocLaunch(cwd="/work"), options)

        assert plan.argv == ["adapter"]
        assert plan.cwd == "/work"
        assert plan.env["LAUNCHER"] == "1"
        assert plan.env["CLAUDE_CODE_EXECUTABLE"] == "/opt/claude/bin/claude"
        assert plan.env["PATH"].split(os.pathsep)[0] == "/opt/claude/bin"
        assert plan.initial_config == [ConfigIntent("smart", category="model")]
        assert plan.system_prompt_append == "Be brief."
        assert plan.initial_prompt == "Run the tests"
        assert plan.title == "Tests"
        assert plan.created_files == ["/work/.mcp.json"]
        assert launcher.built[0][2] is None

    async def test_missing_claude_binary_leaves_the_adapter_default(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher(binary=None), command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd="/work"), ClaudeOptions())

        assert "CLAUDE_CODE_EXECUTABLE" not in plan.env

    async def test_plan_signs_in_with_the_host_claude(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher(), command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd="/work"), ClaudeOptions())

        assert [(m.id, m.argv) for m in plan.sign_in] == [
            ("claude_ai", ["/opt/claude/bin/claude", "auth", "login", "--claudeai"]),
            ("console", ["/opt/claude/bin/claude", "auth", "login", "--console"]),
        ]
        assert plan.sign_in[0].env is not None and plan.sign_in[0].env["LAUNCHER"] == "1"

    async def test_without_claude_there_is_no_sign_in(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher(binary=None), command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd="/work"), ClaudeOptions())

        assert plan.sign_in == []

    async def test_host_claude_executable_is_not_overridden(self, bin_dir, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_EXECUTABLE", "/custom/claude")
        spec = ClaudeChatSpec(FakeLauncher(), command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd="/work"), ClaudeOptions())

        assert "CLAUDE_CODE_EXECUTABLE" not in plan.env

    async def test_no_command_raises_unavailable(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher())

        with pytest.raises(ChatAgentUnavailableError) as excinfo:
            await spec.prepare(AdHocLaunch(cwd="/work"), ClaudeOptions())

        assert excinfo.value.kind == "claude"
        assert "Node.js" in str(excinfo.value)

    async def test_adapter_process_runs_from_home_not_from_the_project(self, bin_dir, tmp_path):
        spec = ClaudeChatSpec(FakeLauncher(), command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), ClaudeOptions())

        assert plan.cwd == str(tmp_path)
        assert plan.process_cwd == os.path.expanduser("~")

    async def test_trust_gate_reads_the_config_dir_of_the_launch_env(
        self, bin_dir, tmp_path, monkeypatch
    ):
        project = tmp_path / "project"
        project.mkdir()
        launch_config = tmp_path / "launch-config"
        launch_config.mkdir()
        (launch_config / ".claude.json").write_text(
            json.dumps({"projects": {os.path.realpath(project): {"hasTrustDialogAccepted": True}}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "host-config"))
        launcher = FakeLauncher(env={"CLAUDE_CONFIG_DIR": str(launch_config)})
        spec = ClaudeChatSpec(launcher, command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd=str(project)), ClaudeOptions())

        assert plan.trust is not None
        assert plan.trust.is_trusted() is True

    async def test_fake_agent_has_no_trust_gate(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher(), test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd="/work"), ClaudeOptions())

        assert plan.trust is None

    async def test_catalog_plan_runs_in_home_with_the_claude_preset(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher(), command_override="adapter")

        plan = await spec.catalog_plan()

        assert plan.argv == ["adapter"]
        assert plan.cwd == os.path.expanduser("~")
        assert plan.env["ENABLE_PROMPT_CACHING_1H"] == "1"
        assert plan.initial_config == []


class TestClaudeConfigIntents:
    def test_plan_mode_wins_over_skip_permissions(self):
        intents = claude_config_intents(ClaudeOptions(plan_mode=True, skip_permissions=True))
        assert intents == [ConfigIntent("plan", category="mode", required=True)]

    def test_bypass_permissions_is_not_required(self):
        # Falling back to the agent's default asks more, never less.
        intents = claude_config_intents(ClaudeOptions(skip_permissions=True))
        assert intents[0].required is False

    def test_skip_permissions_means_bypass(self):
        intents = claude_config_intents(ClaudeOptions(skip_permissions=True))
        assert intents == [ConfigIntent("bypassPermissions", category="mode")]


    def test_model_effort_and_fast(self):
        intents = claude_config_intents(
            ClaudeOptions(skip_permissions=False, model="opus", effort="high", fast_mode=True)
        )
        assert intents == [
            ConfigIntent("opus", category="model"),
            ConfigIntent("high", category="thought_level"),
            ConfigIntent(True, option_id="fast"),
        ]

    def test_ultracode_replaces_effort(self):
        intents = claude_config_intents(
            ClaudeOptions(skip_permissions=False, effort="high", ultracode=True)
        )
        assert intents == [ConfigIntent(True, option_id="ultracode")]


class _StubSpec:
    def __init__(self, available, resume=True, live=False):
        self._available = available
        self.supports_resume = resume
        self.live_sessions = live

    def available(self):
        if isinstance(self._available, Exception):
            raise self._available
        return self._available


class TestCapabilitiesAndRegistry:
    def test_capabilities_list_only_runnable_agents(self):
        caps = build_chat_capabilities(
            {
                "claude": _StubSpec(True),
                "codex": _StubSpec(False),
                "broken": _StubSpec(RuntimeError("probe failed")),
                "other": _StubSpec(True, resume=False),
                "live": _StubSpec(True, live=True),
            }
        )
        assert caps == {
            "claude": {"resume": True, "live": False},
            "other": {"resume": False, "live": False},
            "live": {"resume": True, "live": True},
        }

    def test_registry_has_a_spec_per_chat_capable_launcher(self, tmp_path):
        assert build_chat_agents({"shell": object()}) == {}

        specs = build_chat_agents(
            {
                "claude": FakeLauncher(),
                "codex": FakeCodexLauncher(),
                "antigravity": FakeAgyLauncher(),
                "shell": object(),
            },
            test_mode=True,
            command_overrides={
                "claude": "custom --acp",
                "codex": "codex-adapter --x",
                "antigravity": "agy-dev acp",
            },
            antigravity_app_data_dir=tmp_path / "agy",
        )

        assert list(specs) == ["claude", "codex", "antigravity"]
        assert specs["claude"].command() == ["custom", "--acp"]
        assert specs["codex"].command() == ["codex-adapter", "--x"]
        assert specs["antigravity"].command() == ["agy-dev", "acp"]


class TestPinnedInstallOnFirstUse:
    @skip_if_not_linux
    async def test_prepare_leaves_the_install_to_the_session(self, bin_dir, tmp_path):
        node = _install(bin_dir, "node")
        fake_npm(bin_dir)
        adapters = tmp_path / "adapters"
        launcher = FakeCodexLauncher(
            env={"FAKE_NPM_LOG": str(tmp_path / "npm.log"), "FAKE_NPM_BIN_NAME": "codex-acp"}
        )
        spec = CodexChatSpec(launcher, adapters_dir=adapters)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert plan.argv == []
        assert plan.ensure_argv is not None
        assert not adapters.exists()

        argv = plan.ensure_argv()

        entry = adapters / "codex-acp-2.5.0" / "node_modules" / "@agentclientprotocol" / "codex-acp"
        assert argv == [node, str((entry / "dist" / "index.js").resolve())]
        # The next session starts at once.
        assert spec.command() == argv

    @skip_if_not_linux
    async def test_an_installed_adapter_needs_no_install_step(self, bin_dir, tmp_path):
        node = _install(bin_dir, "node")
        adapters = tmp_path / "adapters"
        entry = _installed_adapter(adapters, agents.CODEX_ADAPTER)
        spec = CodexChatSpec(FakeCodexLauncher(), adapters_dir=adapters)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert plan.argv == [node, str(entry)]
        assert plan.ensure_argv is None


class TestCodexConfigIntents:
    @pytest.mark.parametrize(
        ("options", "mode"),
        [
            (CodexOptions(full_auto=True, sandbox="read-only"), "agent-full-access"),
            (CodexOptions(sandbox="danger-full-access", approval="never"), "agent-full-access"),
            (CodexOptions(sandbox="danger-full-access", approval="on-request"), "workspace-write"),
            (CodexOptions(sandbox="read-only", approval="never"), "read-only"),
            (CodexOptions(sandbox="workspace-write", approval="never"), "workspace-write"),
            (CodexOptions(sandbox="workspace-write", approval="on-request"), "workspace-write"),
            (CodexOptions(sandbox="workspace-write", approval="untrusted"), "workspace-write"),
        ],
        ids=[
            "full-auto-wins",
            "danger-never-ask",
            "danger-still-asks",
            "read-only",
            "never-ask-in-workspace",
            "on-request",
            "untrusted",
        ],
    )
    def test_sandbox_and_approval_pick_the_mode(self, options, mode):
        assert codex_mode(options) == mode

    def test_model_and_reasoning_effort(self):
        intents = codex_config_intents(
            CodexOptions(model="gpt-5.6-terra", approval="on-request", reasoning_effort="xhigh")
        )

        assert intents == [
            ConfigIntent("workspace-write", category="mode", required=True),
            ConfigIntent("gpt-5.6-terra", category="model"),
            ConfigIntent("xhigh", category="thought_level"),
        ]


class TestCodexPrepare:
    async def test_plan_runs_the_host_codex_with_the_mapped_options(
        self, bin_dir, tmp_path, monkeypatch
    ):
        project = tmp_path / "project"
        project.mkdir()
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text(
            f'[projects.{json.dumps(os.path.realpath(project))}]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host-codex-home"))
        launcher = FakeCodexLauncher(env={"CODEX_HOME": str(codex_home)})
        spec = CodexChatSpec(launcher, command_override="adapter")
        options = CodexOptions(
            model="gpt-5.6-terra",
            sandbox="read-only",
            approval="on-request",
            reasoning_effort="low",
            initial_prompt="Run the tests",
        )

        plan = await spec.prepare(AdHocLaunch(cwd=str(project)), options)

        assert plan.argv == ["adapter"]
        assert plan.cwd == str(project)
        assert plan.process_cwd == os.path.expanduser("~")
        assert plan.env["LAUNCHER"] == "1"
        assert plan.env["CODEX_PATH"] == "/opt/codex/bin/codex"
        assert plan.env["PATH"].split(os.pathsep)[0] == "/opt/codex/bin"
        assert plan.initial_config == [
            ConfigIntent("read-only", category="mode", required=True),
            ConfigIntent("gpt-5.6-terra", category="model"),
            ConfigIntent("low", category="thought_level"),
        ]
        assert plan.initial_prompt == "Run the tests"
        assert plan.shared_host is False
        assert isinstance(plan.trust, CodexFolderTrust)
        assert plan.trust.is_trusted() is True

    async def test_a_chat_is_built_without_the_sign_in_check(self, bin_dir, tmp_path):
        launcher = FakeCodexLauncher()
        spec = CodexChatSpec(launcher, command_override="adapter")

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert launcher.require_auth == [False]
        assert [m.id for m in plan.sign_in] == ["chatgpt", "api_key"]
        if sys.platform == "win32":
            assert plan.sign_in[0].argv == ["/opt/codex/bin/codex", "login", "--device-auth"]
        else:
            # Through the shared app-server while it runs: it never reads a
            # login that ``codex login`` saves next to it.
            assert plan.sign_in[0].argv == [
                sys.executable,
                "-I",
                agents.CODEX_BRIDGE_SCRIPT,
                "/opt/codex/bin/codex",
                "login",
                "--device-auth",
            ]

    async def test_missing_codex_binary_leaves_the_adapter_default(self, bin_dir, tmp_path, monkeypatch):
        _socket_answers(monkeypatch)
        spec = CodexChatSpec(
            FakeCodexLauncher(binary=None),
            command_override="adapter",
            adapters_dir=tmp_path / "adapters",
        )

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert "CODEX_PATH" not in plan.env
        assert plan.shared_host is False

    async def test_host_codex_path_is_kept_and_never_shared(self, bin_dir, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_PATH", "/custom/codex")
        probed = _socket_answers(monkeypatch)
        spec = CodexChatSpec(
            FakeCodexLauncher(), command_override="adapter", adapters_dir=tmp_path / "adapters"
        )

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert "CODEX_PATH" not in plan.env
        assert plan.shared_host is False
        assert probed == []

    @skip_if_not_linux
    async def test_a_running_shared_app_server_is_joined_through_the_bridge(
        self, bin_dir, tmp_path, monkeypatch
    ):
        codex = tmp_path / "codex-bin" / "codex"
        codex.parent.mkdir()
        codex.write_text('#!/bin/sh\necho "real codex $*"\n', encoding="utf-8")
        codex.chmod(0o755)
        codex_home = tmp_path / "codex-home"
        probed = _socket_answers(monkeypatch)
        adapters = tmp_path / "adapters"
        launcher = FakeCodexLauncher(binary=str(codex), env={"CODEX_HOME": str(codex_home)})
        spec = CodexChatSpec(launcher, command_override="adapter", adapters_dir=adapters)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())
        again = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert plan.shared_host is True
        assert probed[0] == os.path.join(
            codex_home, "app-server-control", "app-server-control.sock"
        )
        wrapper = Path(plan.env["CODEX_PATH"])
        assert wrapper.parent == adapters
        assert again.env["CODEX_PATH"] == str(wrapper)
        assert os.listdir(adapters) == [wrapper.name]

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(wrapper), *args],
                env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(codex_home)},
                capture_output=True,
                text=True,
                timeout=30,
            )

        assert run("login", "status").stdout == "real codex login status\n"
        # Nothing listens in this test, so the bridge runs a private app-server.
        assert run("app-server").stdout == "real codex app-server\n"

    @pytest.mark.parametrize("case", ["conda-project", "no-adapters-dir", "fake-agent"])
    async def test_a_private_app_server_where_sharing_does_not_apply(
        self, bin_dir, tmp_path, monkeypatch, case
    ):
        _socket_answers(monkeypatch)
        adapters = tmp_path / "adapters"
        target = AdHocLaunch(cwd=str(tmp_path))
        if case == "conda-project":
            target = ProjectConfig(name="ml", path=str(tmp_path), conda_env="ml")
        spec = CodexChatSpec(
            FakeCodexLauncher(),
            command_override=None if case == "fake-agent" else "adapter",
            test_mode=case == "fake-agent",
            adapters_dir=None if case == "no-adapters-dir" else adapters,
        )

        plan = await spec.prepare(target, CodexOptions())

        assert plan.shared_host is False
        assert plan.env["CODEX_PATH"] == "/opt/codex/bin/codex"
        assert not adapters.exists()

    async def test_fake_agent_is_the_codex_flavor_without_a_trust_gate(self, bin_dir, tmp_path):
        spec = CodexChatSpec(FakeCodexLauncher(), test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CodexOptions())

        assert plan.argv[-2:] == ["--flavor", "codex"]
        assert os.path.basename(plan.argv[1]) == "fake_agent.py"
        assert plan.trust is None
        assert spec.live_sessions is False
        assert plan.sign_in[0].argv == [*fake_login_argv("codex"), "--device"]


@pytest.fixture
def agy_bin(tmp_path) -> Path:
    """A file where the launcher finds ``agy`` (never run: the probe is faked)."""
    path = tmp_path / "agy-bin" / "agy"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return path


def _acp_probe(monkeypatch, *answers: bool | None) -> list[str]:
    """Stands in for ``agy help acp``: gives ``answers`` in turn (the last one
    repeats; None is a probe that could not run) and returns the binaries it
    was asked about."""
    probed: list[str] = []

    def supports(binary: str) -> bool | None:
        probed.append(binary)
        return answers[min(len(probed), len(answers)) - 1]

    monkeypatch.setattr(agents, "_agy_supports_acp", supports)
    return probed


class _ArrivalLock:
    """A lock that counts the threads that reached it, so a test can hold
    the probe until every caller waits on the lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._arrivals = threading.Condition()
        self.arrived = 0

    def __enter__(self) -> None:
        with self._arrivals:
            self.arrived += 1
            self._arrivals.notify_all()
        self._lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self._lock.release()

    def wait_for_arrivals(self, count: int, timeout: float) -> bool:
        with self._arrivals:
            return self._arrivals.wait_for(lambda: self.arrived >= count, timeout)


def _agy_spec(agy_bin: Path, tmp_path: Path, **kwargs) -> AntigravityChatSpec:
    return AntigravityChatSpec(
        FakeAgyLauncher(str(agy_bin)), app_data_dir=tmp_path / "agy-data", **kwargs
    )


class TestAntigravityCommand:


    def test_an_agy_with_the_acp_server_runs_agy_acp(self, agy_bin, tmp_path, monkeypatch):
        probed = _acp_probe(monkeypatch, True)
        spec = _agy_spec(agy_bin, tmp_path)

        assert spec.command() == [str(agy_bin), "acp"]
        assert spec.available() is True
        assert spec.uses_fake_agent() is False
        assert probed == [str(agy_bin)]

    def test_an_agy_without_the_acp_server_is_unavailable(self, agy_bin, tmp_path, monkeypatch):
        _acp_probe(monkeypatch, False)
        spec = _agy_spec(agy_bin, tmp_path)

        assert spec.command() is None
        assert spec.available() is False

    def test_no_agy_is_unavailable_without_a_probe(self, tmp_path, monkeypatch):
        probed = _acp_probe(monkeypatch, True)
        spec = AntigravityChatSpec(FakeAgyLauncher(binary=None), app_data_dir=tmp_path / "agy-data")

        assert spec.available() is False
        assert probed == []

    def test_the_probe_answer_is_kept_while_the_binary_is_the_same(
        self, agy_bin, tmp_path, monkeypatch
    ):
        now = [1000.0]
        monkeypatch.setattr(agents.time, "monotonic", lambda: now[0])
        probed = _acp_probe(monkeypatch, False, True)
        spec = _agy_spec(agy_bin, tmp_path)
        assert spec.available() is False

        now[0] += 100 * agents.AVAILABILITY_TTL

        assert spec.available() is False
        assert len(probed) == 1

    def test_a_probe_that_could_not_run_is_tried_again_after_the_ttl(
        self, agy_bin, tmp_path, monkeypatch
    ):
        now = [1000.0]
        monkeypatch.setattr(agents.time, "monotonic", lambda: now[0])
        probed = _acp_probe(monkeypatch, None, True)
        spec = _agy_spec(agy_bin, tmp_path)
        assert spec.available() is False

        now[0] += agents.AVAILABILITY_TTL - 1
        assert spec.available() is False
        assert len(probed) == 1

        now[0] += 2
        assert spec.available() is True
        assert len(probed) == 2

    def test_concurrent_checks_share_one_probe(self, agy_bin, tmp_path, monkeypatch):
        callers = 4
        spec = _agy_spec(agy_bin, tmp_path)
        gate = _ArrivalLock()
        spec._probe_lock = gate
        probed: list[str] = []

        def supports(binary: str) -> bool:
            probed.append(binary)
            # Answer only once every caller has missed the cache and waits.
            assert gate.wait_for_arrivals(callers, timeout=10)
            return True

        monkeypatch.setattr(agents, "_agy_supports_acp", supports)

        with ThreadPoolExecutor(callers) as pool:
            answers = list(pool.map(lambda _: spec.available(), range(callers)))

        assert answers == [True] * callers
        assert probed == [str(agy_bin)]

    def test_an_updated_agy_is_probed_again_at_once(self, agy_bin, tmp_path, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(agents.time, "monotonic", lambda: now[0])
        probed = _acp_probe(monkeypatch, False, True)
        spec = _agy_spec(agy_bin, tmp_path)
        assert spec.available() is False

        # ``agy update`` replaced the binary: a new mtime.
        mtime = agy_bin.stat().st_mtime
        os.utime(agy_bin, (mtime + 60, mtime + 60))

        assert spec.available() is True
        assert len(probed) == 2


@skip_if_not_linux
class TestAgyHelpAcpProbe:
    """``agy help acp`` against stand-in scripts, never the real ``agy``."""

    def _stub(self, tmp_path: Path, body: str) -> str:
        path = tmp_path / "agy"
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def test_a_build_with_the_acp_server(self, tmp_path):
        agy = self._stub(tmp_path, 'if [ "$1" = help ] && [ "$2" = acp ]; then exit 0; fi\nexit 1\n')

        assert agents._agy_supports_acp(agy) is True

    def test_a_build_without_it(self, tmp_path):
        agy = self._stub(tmp_path, 'echo "unknown help topic: $2" >&2\nexit 1\n')

        assert agents._agy_supports_acp(agy) is False

    def test_a_binary_that_cannot_run(self, tmp_path):
        # Not an answer: the spec checks again later.
        assert agents._agy_supports_acp(str(tmp_path / "missing-agy")) is None


class TestAntigravityPrepare:
    async def test_plan_runs_agy_acp_in_the_session_folder(self, agy_bin, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        project = tmp_path / "work" / "app"
        project.mkdir(parents=True)
        data = tmp_path / "agy-data"
        data.mkdir()
        (data / "settings.json").write_text(
            json.dumps({"trustedWorkspaces": [os.path.realpath(project)]}), encoding="utf-8"
        )
        spec = AntigravityChatSpec(
            FakeAgyLauncher(str(agy_bin)), command_override="agy acp", app_data_dir=data
        )
        elsewhere = tmp_path / "elsewhere"
        options = AntigravityOptions(
            model="Gemini 3.8 Flash (High)",
            skip_permissions=True,
            add_dirs=("../shared", str(elsewhere), "~/notes"),
        )

        plan = await spec.prepare(AdHocLaunch(cwd=str(project)), options)

        assert plan.argv == ["agy", "acp", "--dangerously-skip-permissions"]
        assert plan.cwd == str(project)
        # ``agy acp`` works in its own working directory, whatever session/new says.
        assert plan.process_cwd == str(project)
        assert plan.env["LAUNCHER"] == "1"
        assert plan.initial_config == [ConfigIntent("Gemini 3.8 Flash (High)", category="model")]
        assert plan.additional_dirs == [
            os.path.normpath(tmp_path / "work" / "shared"),
            os.path.normpath(elsewhere),
            os.path.normpath(home / "notes"),
        ]
        assert plan.config_resolver is resolve_antigravity_config
        assert plan.presence is spec.presence
        assert isinstance(plan.trust, AntigravityFolderTrust)
        assert plan.trust.is_trusted() is True

    async def test_sandbox_is_an_agy_flag_and_no_model_sends_no_config(self, agy_bin, tmp_path):
        spec = _agy_spec(agy_bin, tmp_path, command_override="agy acp")

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), AntigravityOptions(sandbox=True))

        assert plan.argv == ["agy", "acp", "--sandbox"]
        assert plan.initial_config == []
        assert plan.additional_dirs == []

    async def test_flags_never_pile_up_on_the_probed_command(self, agy_bin, tmp_path, monkeypatch):
        _acp_probe(monkeypatch, True)
        spec = _agy_spec(agy_bin, tmp_path)
        options = AntigravityOptions(skip_permissions=True)

        await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), options)
        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), options)

        assert plan.argv == [str(agy_bin), "acp", "--dangerously-skip-permissions"]
        assert spec.command() == [str(agy_bin), "acp"]

    async def test_no_acp_server_fails_before_the_launcher_runs(self, agy_bin, tmp_path, monkeypatch):
        _acp_probe(monkeypatch, False)
        launcher = FakeAgyLauncher(str(agy_bin))
        spec = AntigravityChatSpec(launcher, app_data_dir=tmp_path / "agy-data")

        with pytest.raises(ChatAgentUnavailableError) as excinfo:
            await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), AntigravityOptions())

        assert excinfo.value.kind == "antigravity"
        assert "agy update" in str(excinfo.value)
        # The launcher would have made a worktree for a session that never starts.
        assert launcher.built == []

    async def test_fake_agent_has_no_trust_gate_and_no_presence_lock(self, agy_bin, tmp_path):
        spec = _agy_spec(agy_bin, tmp_path, test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), AntigravityOptions(sandbox=True))

        assert plan.argv[-3:] == ["--flavor", "antigravity", "--sandbox"]
        assert plan.trust is None
        assert plan.presence is None
        assert [m.argv for m in plan.sign_in] == [fake_login_argv("antigravity")]

    async def test_plan_signs_in_with_the_agy_it_found(self, agy_bin, tmp_path):
        spec = _agy_spec(agy_bin, tmp_path, command_override="agy acp")

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), AntigravityOptions())

        assert [(m.id, m.argv) for m in plan.sign_in] == [
            ("google", [str(agy_bin), "acp", "--login"])
        ]
        assert plan.sign_in[0].reads_code is True


class FakeOpenCodeLauncher:
    """The three ``OpenCodeLauncher`` calls a chat spec makes."""

    def __init__(self, binary: str | None = "/opt/opencode/bin/opencode") -> None:
        self.binary = binary
        self.resolved: list[tuple[object, OpenCodeOptions]] = []

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise OpenCodeBinaryNotFoundError(searched_paths=["PATH:opencode"])
        return self.binary

    def build_env(self, target) -> dict[str, str]:
        return {"PATH": "/usr/bin", "LAUNCHER": "1"}

    async def resolve_cwd(self, target, options) -> str:
        self.resolved.append((target, options))
        return target.path if isinstance(target, ProjectConfig) else target.cwd


class TestOpenCodePrepare:
    async def test_plan_runs_opencode_acp_from_home_for_the_session_folder(
        self, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.delenv(agents.OPENCODE_ACP_COMMAND_ENV, raising=False)
        project = tmp_path / "work" / "app"
        project.mkdir(parents=True)
        launcher = FakeOpenCodeLauncher()
        spec = OpenCodeChatSpec(launcher)
        options = OpenCodeOptions(auto_approve=True, initial_prompt="Fix the tests")

        plan = await spec.prepare(AdHocLaunch(cwd=str(project)), options)

        assert plan.argv == ["/opt/opencode/bin/opencode", "acp"]
        assert plan.cwd == str(project)
        assert plan.process_cwd == str(home)
        assert plan.env["LAUNCHER"] == "1"
        assert plan.initial_prompt == "Fix the tests"
        # ``opencode acp`` has no ``--auto``: the chat session answers for it.
        assert plan.auto_approve is True
        assert "--auto" not in plan.argv
        # OpenCode asks for no folder trust and has no account to sign in to.
        assert plan.trust is None
        assert plan.sign_in == []
        assert plan.sign_in_hint == agents.OPENCODE_SIGN_IN_HINT
        assert launcher.resolved == [(AdHocLaunch(cwd=str(project)), options)]

    async def test_default_options_ask_before_each_permission(self, tmp_path, monkeypatch):
        monkeypatch.delenv(agents.OPENCODE_ACP_COMMAND_ENV, raising=False)
        spec = OpenCodeChatSpec(FakeOpenCodeLauncher())

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), OpenCodeOptions())

        assert plan.auto_approve is False
        assert plan.initial_prompt is None

    async def test_no_opencode_fails_before_a_worktree_can_be_made(self, tmp_path, monkeypatch):
        monkeypatch.delenv(agents.OPENCODE_ACP_COMMAND_ENV, raising=False)
        launcher = FakeOpenCodeLauncher(binary=None)
        spec = OpenCodeChatSpec(launcher)

        assert spec.available() is False
        with pytest.raises(ChatAgentUnavailableError) as excinfo:
            await spec.prepare(
                ProjectConfig(name="p", path=str(tmp_path)), OpenCodeOptions(worktree=True)
            )

        assert excinfo.value.kind == "opencode"
        assert "opencode.ai" in str(excinfo.value)
        # ``resolve_cwd`` would have made a worktree for a session that never starts.
        assert launcher.resolved == []

    async def test_test_mode_runs_the_fake_agent_without_the_cli(self, tmp_path, monkeypatch):
        monkeypatch.delenv(agents.OPENCODE_ACP_COMMAND_ENV, raising=False)
        monkeypatch.delenv(agents.REAL_IN_TEST_ENV, raising=False)
        spec = OpenCodeChatSpec(FakeOpenCodeLauncher(binary=None), test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), OpenCodeOptions())

        assert plan.argv[-2:] == ["--flavor", "opencode"]
        assert spec.uses_fake_agent() is True

    def test_registry_and_capabilities_carry_opencode(self, monkeypatch):
        monkeypatch.delenv(agents.OPENCODE_ACP_COMMAND_ENV, raising=False)
        found = build_chat_agents(
            {"opencode": FakeOpenCodeLauncher()},
            command_overrides={"opencode": "opencode-dev acp"},
        )
        missing = build_chat_agents({"opencode": FakeOpenCodeLauncher(binary=None)})

        assert found["opencode"].command() == ["opencode-dev", "acp"]
        # Stored sessions can be continued; a session open in the terminal UI
        # too is not kept in sync live.
        assert build_chat_capabilities(found) == {"opencode": {"resume": True, "live": False}}
        assert build_chat_capabilities(missing) == {}


class TestCopilotCommand:
    def test_copilot_runs_its_own_acp_server(self):
        spec = CopilotChatSpec(FakeCopilotLauncher())

        assert spec.command() == ["/opt/copilot/bin/copilot", "--acp"]
        assert spec.available() is True
        assert spec.uses_fake_agent() is False

    def test_no_copilot_is_unavailable(self):
        spec = CopilotChatSpec(FakeCopilotLauncher(binary=None))

        assert spec.command() is None
        assert spec.available() is False

    def test_the_registry_gives_copilot_its_command_override(self):
        specs = build_chat_agents(
            {"copilot": FakeCopilotLauncher(binary=None), "shell": object()},
            command_overrides={"copilot": "copilot-dev --acp"},
        )

        assert list(specs) == ["copilot"]
        assert specs["copilot"].kind == "copilot"
        assert specs["copilot"].command() == ["copilot-dev", "--acp"]


class TestCopilotPrepare:
    async def test_plan_runs_in_home_and_opens_the_session_folder(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        project = tmp_path / "work" / "app"
        project.mkdir(parents=True)
        copilot_home = tmp_path / "copilot-home"
        copilot_home.mkdir()
        (copilot_home / "config.json").write_text(
            "// This file is managed automatically.\n"
            + json.dumps({"trustedFolders": [os.path.realpath(project)]}),
            encoding="utf-8",
        )
        launcher = FakeCopilotLauncher(env={"COPILOT_HOME": str(copilot_home)})
        spec = CopilotChatSpec(launcher)
        options = CopilotOptions(allow_all=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(project)), options)

        assert plan.argv == ["/opt/copilot/bin/copilot", "--acp"]
        # The folder goes to Copilot in ``session/new``; the server runs in home.
        assert plan.cwd == str(project)
        assert plan.process_cwd == str(home)
        assert plan.env["LAUNCHER"] == "1"
        # ``allow_all`` is a config option of the session, never a launch flag.
        assert plan.initial_config == [ConfigIntent("on", option_id="allow_all")]
        assert plan.initial_prompt is None
        # The trust record is the one of the Copilot the launch env points to.
        assert isinstance(plan.trust, CopilotFolderTrust)
        assert plan.trust.is_trusted() is True
        assert [(m.id, m.argv) for m in plan.sign_in] == [
            ("github", ["/opt/copilot/bin/copilot", "login", "--device-code"])
        ]
        assert plan.sign_in[0].env["COPILOT_HOME"] == str(copilot_home)

    async def test_a_folder_copilot_does_not_trust_yet_gets_the_trust_gate(self, tmp_path):
        """``copilot --acp`` loads a folder's hooks and MCP servers without its
        own trust question, so the chat must ask first."""
        project = tmp_path / "work" / "app"
        project.mkdir(parents=True)
        launcher = FakeCopilotLauncher(env={"COPILOT_HOME": str(tmp_path / "copilot-home")})

        plan = await CopilotChatSpec(launcher).prepare(
            AdHocLaunch(cwd=str(project)), CopilotOptions()
        )

        assert isinstance(plan.trust, CopilotFolderTrust)
        assert plan.trust.is_trusted() is False
        assert plan.initial_config == []
        assert plan.initial_prompt is None

    async def test_no_copilot_fails_before_the_launcher_runs(self, tmp_path):
        launcher = FakeCopilotLauncher(binary=None)
        spec = CopilotChatSpec(launcher)

        with pytest.raises(ChatAgentUnavailableError) as excinfo:
            await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CopilotOptions(worktree=True))

        assert excinfo.value.kind == "copilot"
        assert "npm install -g @github/copilot" in str(excinfo.value)
        # The launcher would have made a worktree for a session that never starts.
        assert launcher.resolved == []

    async def test_fake_agent_has_no_trust_gate_and_a_fake_device_login(self, tmp_path):
        spec = CopilotChatSpec(FakeCopilotLauncher(binary=None), test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CopilotOptions(allow_all=True))

        assert plan.argv[-2:] == ["--flavor", "copilot"]
        assert plan.trust is None
        assert plan.initial_config == [ConfigIntent("on", option_id="allow_all")]
        assert [m.argv for m in plan.sign_in] == [[*fake_login_argv("copilot"), "--device"]]

    async def test_catalog_plan_finds_the_node_next_to_an_npm_copilot(self, bin_dir):
        """An npm install of Copilot is a Node script whose ``node`` sits next
        to it; without that folder on ``PATH`` the stored sessions do not list."""
        spec = CopilotChatSpec(FakeCopilotLauncher())

        plan = await spec.catalog_plan()

        assert plan.argv == ["/opt/copilot/bin/copilot", "--acp"]
        assert plan.env["PATH"].split(os.pathsep) == ["/opt/copilot/bin", str(bin_dir)]
        assert plan.cwd == os.path.expanduser("~")


class TestCursorCommand:
    def test_cursor_runs_its_own_acp_server(self):
        spec = CursorChatSpec(FakeCursorLauncher())

        assert spec.command() == ["/opt/cursor/bin/cursor-agent", "acp"]
        assert spec.available() is True
        assert spec.uses_fake_agent() is False

    def test_no_cursor_is_unavailable(self):
        spec = CursorChatSpec(FakeCursorLauncher(binary=None))

        assert spec.command() is None
        assert spec.available() is False

    def test_the_env_override_wins_over_the_installed_cursor(self, monkeypatch):
        monkeypatch.setenv(agents.CURSOR_ACP_COMMAND_ENV, "cursor-nightly acp")

        assert CursorChatSpec(FakeCursorLauncher()).command() == ["cursor-nightly", "acp"]

    def test_test_mode_runs_the_fake_agent_in_its_cursor_flavor(self):
        spec = CursorChatSpec(FakeCursorLauncher(), test_mode=True)

        assert spec.command() == fake_agent_argv("cursor")
        assert spec.uses_fake_agent() is True

    def test_the_registry_gives_cursor_its_command_override(self):
        specs = build_chat_agents(
            {"cursor": FakeCursorLauncher(binary=None), "shell": object()},
            command_overrides={"cursor": "cursor-dev acp"},
        )

        assert list(specs) == ["cursor"]
        assert specs["cursor"].kind == "cursor"
        assert specs["cursor"].display_name == "Cursor"
        assert specs["cursor"].command() == ["cursor-dev", "acp"]


class TestCursorPrepare:
    async def test_plan_runs_the_server_in_the_session_folder(self, tmp_path):
        """Cursor reads a folder's rules, hooks and MCP servers from its own
        working directory, not from ``session/new``."""
        project = tmp_path / "work" / "app"
        project.mkdir(parents=True)
        data_dir = tmp_path / "cursor-data"
        launcher = FakeCursorLauncher(env={"CURSOR_DATA_DIR": str(data_dir)})
        spec = CursorChatSpec(launcher)
        options = CursorOptions()

        plan = await spec.prepare(AdHocLaunch(cwd=str(project)), options)

        assert plan.argv == ["/opt/cursor/bin/cursor-agent", "acp"]
        assert plan.cwd == str(project)
        assert plan.process_cwd == str(project)
        assert plan.env["LAUNCHER"] == "1"
        assert plan.initial_config == []
        assert plan.initial_prompt is None
        assert plan.auto_approve is False
        assert plan.client_meta == {"parameterizedModelPicker": True}
        # Cursor writes a tool title in Markdown: `pytest -q`, Edit `app.py`.
        assert plan.markdown_titles is True
        assert [(target.cwd, opts) for target, opts in launcher.resolved] == [
            (str(project), options)
        ]

    async def test_force_is_a_flag_of_the_program_before_its_acp_command(self, tmp_path):
        spec = CursorChatSpec(FakeCursorLauncher())

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CursorOptions(force=True))

        assert plan.argv == ["/opt/cursor/bin/cursor-agent", "--force", "acp"]
        # Cursor's own flag does the work: the chat accepts nothing for the user.
        assert plan.auto_approve is False

    async def test_force_with_an_operator_command_makes_the_chat_accept_the_requests(
        self, tmp_path
    ):
        """A command the operator set has no place for Cursor's flag."""
        spec = CursorChatSpec(FakeCursorLauncher(), command_override="cursor-dev acp")

        forced = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CursorOptions(force=True))
        asking = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CursorOptions())

        assert forced.argv == ["cursor-dev", "acp"]
        assert forced.auto_approve is True
        assert asking.argv == ["cursor-dev", "acp"]
        assert asking.auto_approve is False

    async def test_the_trust_record_is_the_one_of_the_cursor_the_launch_env_points_to(
        self, tmp_path
    ):
        project = tmp_path / "work" / "app"
        project.mkdir(parents=True)
        data_dir = tmp_path / "cursor-data"
        launcher = FakeCursorLauncher(env={"CURSOR_DATA_DIR": str(data_dir)})

        plan = await CursorChatSpec(launcher).prepare(
            AdHocLaunch(cwd=str(project)), CursorOptions()
        )

        # ``cursor-agent acp`` does not ask Cursor's trust question itself.
        assert isinstance(plan.trust, CursorFolderTrust)
        assert plan.trust.is_trusted() is False
        assert plan.trust.grant() is True
        (state_dir,) = (data_dir / "projects").iterdir()
        assert (state_dir / ".workspace-trusted").is_file()
        assert plan.trust.is_trusted() is True

    async def test_sign_in_is_the_login_of_the_same_cursor(self, tmp_path):
        launcher = FakeCursorLauncher(env={"CURSOR_DATA_DIR": str(tmp_path / "data")})

        plan = await CursorChatSpec(launcher).prepare(
            AdHocLaunch(cwd=str(tmp_path)), CursorOptions()
        )

        assert [(m.id, m.argv) for m in plan.sign_in] == [
            ("cursor", ["/opt/cursor/bin/cursor-agent", "login"])
        ]
        assert plan.sign_in[0].env["CURSOR_DATA_DIR"] == str(tmp_path / "data")
        assert plan.sign_in[0].env["NO_OPEN_BROWSER"] == "1"

    async def test_no_cursor_fails_before_the_launcher_runs(self, tmp_path):
        launcher = FakeCursorLauncher(binary=None)
        spec = CursorChatSpec(launcher)

        with pytest.raises(ChatAgentUnavailableError) as excinfo:
            await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CursorOptions(worktree=True))

        assert excinfo.value.kind == "cursor"
        assert "curl https://cursor.com/install -fsS | bash" in str(excinfo.value)
        # The launcher would have made a worktree for a session that never starts.
        assert launcher.resolved == []

    async def test_fake_agent_has_no_trust_gate_and_a_fake_login(self, tmp_path):
        spec = CursorChatSpec(FakeCursorLauncher(binary=None), test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CursorOptions())

        assert plan.argv == fake_agent_argv("cursor")
        assert plan.process_cwd == str(tmp_path)
        assert plan.trust is None
        assert plan.auto_approve is False
        assert plan.client_meta == {"parameterizedModelPicker": True}
        assert [m.argv for m in plan.sign_in] == [[*fake_login_argv("cursor"), "--device"]]
        assert plan.sign_in[0].env["NO_OPEN_BROWSER"] == "1"

    async def test_force_with_the_fake_agent_makes_the_chat_accept_the_requests(self, tmp_path):
        spec = CursorChatSpec(FakeCursorLauncher(), test_mode=True)

        plan = await spec.prepare(AdHocLaunch(cwd=str(tmp_path)), CursorOptions(force=True))

        assert plan.argv == fake_agent_argv("cursor")
        assert plan.auto_approve is True

    async def test_catalog_plan_runs_in_the_home_folder(self):
        spec = CursorChatSpec(FakeCursorLauncher())

        plan = await spec.catalog_plan()

        assert plan.argv == ["/opt/cursor/bin/cursor-agent", "acp"]
        assert plan.cwd == os.path.expanduser("~")
        assert plan.process_cwd == os.path.expanduser("~")

    async def test_catalog_plan_without_cursor_names_the_install_command(self):
        with pytest.raises(ChatAgentUnavailableError) as excinfo:
            await CursorChatSpec(FakeCursorLauncher(binary=None)).catalog_plan()

        assert excinfo.value.kind == "cursor"
        assert "cursor.com/install" in str(excinfo.value)


def _agy_setup(*, available_models: bool = True) -> dict:
    """The ``session/new`` result of ``agy acp`` 1.5: ``model`` and
    ``thought_level`` options, and one ``availableModels`` entry per model and
    thinking level."""
    result: dict = {
        "sessionId": "conv-1",
        "configOptions": [
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": "gemini-3.8-flash",
                "options": [
                    {"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"},
                    {"value": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
                ],
            },
            {
                "id": "thought_level",
                "name": "Thinking Level",
                "category": "thought_level",
                "type": "select",
                "currentValue": "high",
                "options": [
                    {"value": "low", "name": "Low"},
                    {"value": "medium", "name": "Medium"},
                    {"value": "high", "name": "High"},
                ],
            },
            {
                "id": "mode",
                "name": "Mode",
                "category": "mode",
                "type": "select",
                "currentValue": "default",
                "options": [{"value": "default", "name": "Default"}, {"value": "plan", "name": "Plan"}],
            },
        ],
    }
    if available_models:
        result["models"] = {
            "availableModels": [
                {"modelId": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)"},
                {"modelId": "gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)"},
                {"modelId": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
            ],
            "currentModelId": "gemini-3.8-flash-high",
        }
    return result


def _model(value: str, required: bool = False) -> ConfigIntent:
    return ConfigIntent(value, category="model", required=required)


def _level(value: str) -> ConfigIntent:
    return ConfigIntent(value, category="thought_level")


def _padded_flash_high(length: int) -> str:
    """``Gemini 3.8 Flash   …   (High)``, exactly ``length`` characters."""
    name = "Gemini 3.8 Flash".ljust(length - len("(High)")) + "(High)"
    assert len(name) == length
    return name


class TestAntigravityConfig:
    def test_the_model_name_is_the_only_intent(self):
        options = AntigravityOptions(model="Gemini 3.8 Flash (High)", skip_permissions=True)

        assert antigravity_config_intents(options) == [_model("Gemini 3.8 Flash (High)")]
        assert antigravity_config_intents(AntigravityOptions(sandbox=True)) == []

    @pytest.mark.parametrize(
        ("saved", "intents"),
        [
            ("gemini-3.8-flash-high\tGemini 3.8 Flash (High)", [_model("Gemini 3.8 Flash (High)")]),
            ("orphan-id\t", []),
        ],
        ids=["id-and-name", "id-without-a-name"],
    )
    def test_a_saved_agy_models_line_sends_only_the_name(self, saved, intents):
        """A choice saved from an older agent may hold a whole ``agy models``
        line: the model id, a tab and the name."""
        assert antigravity_config_intents(AntigravityOptions(model=saved)) == intents

    @pytest.mark.parametrize(
        ("wanted", "resolved"),
        [
            ("Gemini 3.8 Flash (High)", [_model("gemini-3.8-flash"), _level("high")]),
            ("gemini 3.8 flash (low)", [_model("gemini-3.8-flash"), _level("low")]),
            ("gemini-3.8-flash-low", [_model("gemini-3.8-flash"), _level("low")]),
            ("Claude Sonnet 4.6 (Thinking)", [_model("claude-sonnet-4-6")]),
            ("gemini-3.8-flash", [_model("gemini-3.8-flash")]),
            ("Gemini 3.8 Flash", [_model("gemini-3.8-flash")]),
            ("Gemini 9 Ultra (High)", [_model("Gemini 9 Ultra (High)")]),
        ],
        ids=[
            "name-with-level",
            "name-any-case",
            "model-id-with-level",
            "parentheses-in-a-model-name",
            "option-value",
            "option-name",
            "unknown-stays",
        ],
    )
    def test_a_model_name_becomes_the_session_options(self, wanted, resolved):
        assert resolve_antigravity_config(_agy_setup(), [_model(wanted)]) == resolved

    @pytest.mark.parametrize(
        ("wanted", "resolved"),
        [
            ("Gemini 3.8 Flash (Medium)", [_model("gemini-3.8-flash"), _level("medium")]),
            ("gemini 3.8 flash (medium)", [_model("gemini-3.8-flash"), _level("medium")]),
            ("Claude Sonnet 4.6 (Thinking)", [_model("claude-sonnet-4-6")]),
        ],
        ids=["name-with-level", "name-any-case", "parentheses-in-a-model-name"],
    )
    def test_without_the_models_list_the_parentheses_name_the_level(self, wanted, resolved):
        setup = _agy_setup(available_models=False)

        assert resolve_antigravity_config(setup, [_model(wanted)]) == resolved

    def test_required_stays_on_the_model(self):
        resolved = resolve_antigravity_config(
            _agy_setup(), [_model("Gemini 3.8 Flash (High)", required=True)]
        )

        assert resolved == [_model("gemini-3.8-flash", required=True), _level("high")]

    def test_other_intents_pass_through_in_order(self):
        intents = [
            ConfigIntent("plan", category="mode", required=True),
            # A Restart keeps the option values in use, by option id.
            ConfigIntent("gemini-3.8-flash-high", option_id="model", category="model"),
            ConfigIntent("low", option_id="thought_level"),
            _model("Gemini 3.8 Flash (High)"),
        ]

        assert resolve_antigravity_config(_agy_setup(), intents) == [
            *intents[:3],
            _model("gemini-3.8-flash"),
            _level("high"),
        ]

    @pytest.mark.parametrize(
        "result",
        [
            {},
            {"configOptions": "junk"},
            {"configOptions": [{"category": "mode", "options": []}]},
            # The session drops an option without a name: it cannot set it.
            {
                "configOptions": [
                    {
                        "id": "model",
                        "category": "model",
                        "type": "select",
                        "currentValue": "gemini-3.8-flash",
                        "options": [{"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"}],
                    }
                ]
            },
        ],
        ids=["nothing", "not-a-list", "no-model-option", "model-option-without-a-name"],
    )
    def test_a_session_without_a_model_option_keeps_the_intents(self, result):
        intents = [_model("Gemini 3.8 Flash (High)")]

        assert resolve_antigravity_config(result, intents) == intents

    @pytest.mark.parametrize(
        ("wanted", "resolved"),
        [
            ("Gemini 3.8 Flash (Low)", [_model("gemini-3.8-flash"), _level("low")]),
            ("Claude Sonnet 4.6 (Thinking)", [_model("claude-sonnet-4-6")]),
        ],
        ids=["name-with-level", "parentheses-in-a-model-name"],
    )
    def test_grouped_model_values_are_matched(self, wanted, resolved):
        setup = _agy_setup(available_models=False)
        setup["configOptions"][0]["options"] = [
            {"name": "Google", "options": [{"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"}]},
            {
                "name": "Anthropic",
                "options": [{"value": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"}],
            },
        ]

        assert resolve_antigravity_config(setup, [_model(wanted)]) == resolved

    def test_the_name_is_matched_in_the_model_option_the_session_sets(self):
        """The session applies a model intent to the first model option, so
        only that option's values can resolve the name."""
        setup = _agy_setup(available_models=False)
        setup["configOptions"].append(
            {
                "id": "subagent_model",
                "name": "Subagent model",
                "category": "model",
                "type": "select",
                "currentValue": "sub-flash",
                "options": [
                    {"value": "sub-flash", "name": "Gemini 3.8 Flash"},
                    {"value": "sub-only", "name": "Subagent Only"},
                ],
            }
        )

        assert resolve_antigravity_config(setup, [_model("Gemini 3.8 Flash (High)")]) == [
            _model("gemini-3.8-flash"),
            _level("high"),
        ]
        assert resolve_antigravity_config(setup, [_model("Subagent Only")]) == [
            _model("Subagent Only")
        ]


    def test_a_name_longer_than_the_cap_is_kept_as_sent(self):
        wanted = _padded_flash_high(agents.MODEL_NAME_MAX + 1)

        assert resolve_antigravity_config(_agy_setup(), [_model(wanted)]) == [_model(wanted)]

    @pytest.mark.timeout(10)
    @pytest.mark.parametrize(
        "wanted",
        ["a(" * 50_000 + "b)", "(" * 100_000 + ")"],
        ids=["nested-parentheses", "open-parentheses"],
    )
    def test_a_huge_client_name_is_answered_at_once_and_unchanged(self, wanted):
        assert resolve_antigravity_config(_agy_setup(), [_model(wanted)]) == [_model(wanted)]


class TestSplitLevel:
    @pytest.mark.parametrize(
        ("name", "split"),
        [
            ("Gemini 3.8 Flash (High)", ("Gemini 3.8 Flash", "High")),
            ("Gemini 3.8 Flash(Low)", ("Gemini 3.8 Flash", "Low")),
            ("Gemini 3.8 Flash ( Medium )", ("Gemini 3.8 Flash", "Medium")),
            ("Claude (Preview) 5 (High)", ("Claude (Preview) 5", "High")),
            ("Gemini 3.8 Flash", None),
            ("Gemini 3.8 Flash High)", None),
            ("(High)", None),
            ("  (High)", None),
            ("Gemini ()", None),
            ("Gemini (  )", None),
            ("Gemini (a) b)", None),
            (")", None),
        ],
        ids=[
            "name-and-level",
            "no-space-before-the-level",
            "spaces-in-the-parentheses",
            "parentheses-earlier-in-the-name",
            "no-level",
            "no-open-parenthesis",
            "no-name",
            "blank-name",
            "empty-level",
            "blank-level",
            "close-parenthesis-in-the-level",
            "only-a-close-parenthesis",
        ],
    )
    def test_the_last_parentheses_name_the_level(self, name, split):
        assert agents._split_level(name) == split

    @pytest.mark.timeout(10)
    def test_a_long_name_is_split_in_one_pass(self):
        base = "a(" * 50_000 + "a"

        assert agents._split_level(base + " (High)") == (base, "High")
        assert agents._split_level("(" * 100_000 + ")") is None


class TestSignInMethods:
    def test_claude_logs_in_from_home_and_reads_the_pasted_code(self):
        methods = claude_sign_in_methods("/opt/claude/bin/claude", {"PATH": "/usr/bin"})

        assert [m.id for m in methods] == ["claude_ai", "console"]
        for method in methods:
            assert method.cwd == os.path.expanduser("~")
            assert method.env == {"PATH": "/usr/bin"}
            assert method.reads_code is True
            assert method.user_code_pattern is None
            assert method.submit_secret is None

    def test_the_fake_claude_login_is_the_fake_agent(self):
        methods = claude_sign_in_methods(None, {}, fake=True)

        assert [m.argv for m in methods] == [fake_login_argv(), fake_login_argv()]

    @pytest.mark.parametrize(
        "build",
        [
            claude_sign_in_methods,
            codex_sign_in_methods,
            antigravity_sign_in_methods,
            copilot_sign_in_methods,
            cursor_sign_in_methods,
        ],
    )
    def test_no_binary_means_no_sign_in(self, build):
        assert build(None, {"PATH": "/usr/bin"}) == []

    def test_codex_shows_a_one_time_code_and_takes_an_api_key(self):
        device, key = codex_sign_in_methods("/opt/codex/bin/codex", {"CODEX_HOME": "/c"})

        assert device.argv == ["/opt/codex/bin/codex", "login", "--device-auth"]
        assert device.user_code_pattern == CODEX_USER_CODE_PATTERN
        assert device.reads_code is False
        assert key.argv is None
        assert key.submit_secret is not None
        assert key.input_label == "OpenAI API key"

    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("Enter this one-time code\n   ABCD-12345\n", "ABCD-12345"),
            ("code: X1Y2-Z3W4\n", "X1Y2-Z3W4"),
            ("https://auth.openai.com/codex/device\n", None),
            ("abcd-12345\n", None),
        ],
    )
    def test_the_codex_one_time_code_pattern(self, text, code):
        match = re.search(CODEX_USER_CODE_PATTERN, text)

        assert (match.group(0) if match else None) == code

    async def test_the_codex_api_key_goes_to_codex_login_on_stdin(self, monkeypatch):
        runs: list[tuple[list[str], bytes, dict[str, str]]] = []

        def run(argv, *, input, env, **_kw):
            runs.append((argv, input, env))
            return subprocess.CompletedProcess(argv, 0, b"Successfully logged in", b"")

        monkeypatch.setattr(codex_auth.subprocess, "run", run)
        _, key = codex_sign_in_methods("/opt/codex/bin/codex", {"CODEX_HOME": "/c"})
        assert key.submit_secret is not None

        ok, _detail = await key.submit_secret("sk-test")

        assert ok is True
        assert runs == [
            (["/opt/codex/bin/codex", "login", "--with-api-key"], b"sk-test\n", {"CODEX_HOME": "/c"})
        ]

    async def test_the_fake_codex_key_login_refuses_bad_and_ends_the_signed_out_state(
        self, tmp_path, monkeypatch
    ):
        marker = tmp_path / "signed-out"
        marker.write_text("", encoding="utf-8")
        monkeypatch.setenv(agents.FAKE_SIGNED_OUT_ENV, str(marker))
        _, key = codex_sign_in_methods(None, {}, fake=True)
        assert key.submit_secret is not None

        assert (await key.submit_secret("bad"))[0] is False
        assert marker.exists()
        assert await key.submit_secret("sk-test") == (True, "")
        assert not marker.exists()

    def test_antigravity_logs_in_through_agy_acp(self):
        (method,) = antigravity_sign_in_methods("/opt/agy/bin/agy", {"PATH": "/usr/bin"})

        assert method.id == "google"
        assert method.argv == ["/opt/agy/bin/agy", "acp", "--login"]
        assert method.reads_code is True
        assert method.input_label == "Code or address from the sign-in page"

    def test_copilot_shows_a_one_time_code_and_reads_nothing(self):
        (method,) = copilot_sign_in_methods("/opt/copilot/bin/copilot", {"COPILOT_HOME": "/c"})

        assert method.id == "github"
        assert method.argv == ["/opt/copilot/bin/copilot", "login", "--device-code"]
        assert method.env == {"COPILOT_HOME": "/c"}
        assert method.cwd == os.path.expanduser("~")
        assert method.user_code_pattern == COPILOT_USER_CODE_PATTERN
        assert method.reads_code is False
        assert method.submit_secret is None

    @pytest.mark.parametrize(
        ("text", "code"),
        [
            (
                "To authenticate, visit https://github.com/login/device and enter code 1A2B-3C4D\n"
                "Waiting for authorization...\n",
                "1A2B-3C4D",
            ),
            ("To authenticate, visit https://github.com/login/device and enter code\n", None),
            ("enter code ABCD-12345\n", None),
            ("enter code abcd-1234\n", None),
        ],
        ids=["the-login-line", "code-not-printed-yet", "too-long", "lower-case"],
    )
    def test_the_copilot_one_time_code_pattern(self, text, code):
        match = re.search(COPILOT_USER_CODE_PATTERN, text)

        assert (match.group(0) if match else None) == code

    def test_cursor_prints_a_sign_in_page_and_opens_no_browser_on_the_pc(self):
        (method,) = cursor_sign_in_methods(
            "/opt/cursor/bin/cursor-agent", {"CURSOR_DATA_DIR": "/c"}
        )

        assert method.id == "cursor"
        assert method.name == "Cursor account"
        assert method.argv == ["/opt/cursor/bin/cursor-agent", "login"]
        assert method.env == {"CURSOR_DATA_DIR": "/c", "NO_OPEN_BROWSER": "1"}
        assert method.cwd == os.path.expanduser("~")
        # The page needs no code, and the login finishes alone.
        assert method.user_code_pattern is None
        assert method.reads_code is False
        assert method.submit_secret is None

    def test_cursor_does_not_change_the_env_it_was_given(self):
        env = {"PATH": "/usr/bin"}

        cursor_sign_in_methods("/opt/cursor/bin/cursor-agent", env)

        assert env == {"PATH": "/usr/bin"}

    def test_the_fake_cursor_login_needs_no_binary(self):
        (method,) = cursor_sign_in_methods(None, {"PATH": "/usr/bin"}, fake=True)

        assert method.argv == [*fake_login_argv("cursor"), "--device"]
        assert method.env["NO_OPEN_BROWSER"] == "1"

"""Tests for the ACP launch specs: which command starts the Claude chat
adapter, the create-session options as ACP config intents, the launch plan
and the ``capabilities.chat`` map."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from mobaide.acp import agents
from mobaide.acp.agents import (
    CLAUDE_ACP_PACKAGE,
    ChatAgentUnavailableError,
    ClaudeChatSpec,
    ConfigIntent,
    build_chat_agents,
    build_chat_capabilities,
    claude_config_intents,
)
from mobaide.core.claude_launcher import ClaudeBinaryNotFoundError, ClaudeOptions
from mobaide.core.cli_launcher import AdHocLaunch, LaunchSpec
from tests.conftest import skip_if_not_linux


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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (agents.CLAUDE_ACP_COMMAND_ENV, agents.REAL_IN_TEST_ENV, "CLAUDE_CODE_EXECUTABLE"):
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


class TestCommandResolution:
    def test_override_argument_wins(self, bin_dir, monkeypatch):
        monkeypatch.setenv(agents.CLAUDE_ACP_COMMAND_ENV, "from-env --x")
        spec = ClaudeChatSpec(FakeLauncher(), command_override="my-adapter --flag 'a b'", test_mode=True)

        assert spec.command() == ["my-adapter", "--flag", "a b"]

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
    def test_adapter_on_path_is_preferred_over_npx(self, bin_dir):
        adapter = _install(bin_dir, agents.CLAUDE_ACP_BIN)
        _install(bin_dir, "npx")
        spec = ClaudeChatSpec(FakeLauncher())

        assert spec.command() == [adapter]

    @skip_if_not_linux
    def test_npx_runs_the_pinned_package_without_install_scripts(self, bin_dir):
        npx = _install(bin_dir, "npx")
        spec = ClaudeChatSpec(FakeLauncher())

        assert spec.command() == [npx, "--yes", "--ignore-scripts", CLAUDE_ACP_PACKAGE]
        assert spec.available() is True

    def test_nothing_installed_means_unavailable(self, bin_dir):
        spec = ClaudeChatSpec(FakeLauncher())

        assert spec.command() is None
        assert spec.available() is False

    @skip_if_not_linux
    def test_resolution_is_cached_for_the_ttl(self, bin_dir, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(agents.time, "monotonic", lambda: now[0])
        spec = ClaudeChatSpec(FakeLauncher())
        assert spec.available() is False

        _install(bin_dir, "npx")
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
        assert intents == [ConfigIntent("plan", category="mode")]

    def test_skip_permissions_means_bypass(self):
        intents = claude_config_intents(ClaudeOptions(skip_permissions=True))
        assert intents == [ConfigIntent("bypassPermissions", category="mode")]

    def test_ask_first_sends_no_mode(self):
        assert claude_config_intents(ClaudeOptions(skip_permissions=False)) == []

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
    def __init__(self, available, resume=True):
        self._available = available
        self.supports_resume = resume

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
            }
        )
        assert caps == {"claude": {"resume": True}, "other": {"resume": False}}

    def test_registry_has_claude_only_with_a_claude_launcher(self):
        assert build_chat_agents({"codex": object()}) == {}

        specs = build_chat_agents(
            {"claude": FakeLauncher()},
            test_mode=True,
            command_overrides={"claude": "custom --acp"},
        )

        assert list(specs) == ["claude"]
        assert specs["claude"].command() == ["custom", "--acp"]

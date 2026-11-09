"""Tests for the ACP launch specs: which command starts the Claude and Codex
chat adapters (including the pinned install on first use), the
create-session options as ACP config intents, the launch plan (Codex's shared
app-server included) and the ``capabilities.chat`` map."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mobaide.acp import agents, codex_bridge
from mobaide.acp.agents import (
    ChatAgentUnavailableError,
    ClaudeChatSpec,
    CodexChatSpec,
    ConfigIntent,
    build_chat_agents,
    build_chat_capabilities,
    claude_config_intents,
    codex_config_intents,
    codex_mode,
)
from mobaide.acp.install import INSTALLED_MARKER
from mobaide.acp.trust import CodexFolderTrust
from mobaide.core.claude_launcher import ClaudeBinaryNotFoundError, ClaudeOptions
from mobaide.core.cli_launcher import AdHocLaunch, LaunchSpec
from mobaide.core.codex_launcher import CodexBinaryNotFoundError, CodexOptions
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

    def resolve_binary(self) -> str:
        if self.binary is None:
            raise CodexBinaryNotFoundError(["/nowhere/codex"])
        return self.binary

    async def build(self, target, options, session_id=None) -> LaunchSpec:
        cwd = target.path if isinstance(target, ProjectConfig) else target.cwd
        return LaunchSpec(
            argv=["codex"], cwd=cwd, env={"PATH": "/usr/bin", "LAUNCHER": "1", **self.env}
        )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        agents.CLAUDE_ACP_COMMAND_ENV,
        agents.CODEX_ACP_COMMAND_ENV,
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

    def test_registry_has_a_spec_per_chat_capable_launcher(self):
        assert build_chat_agents({"antigravity": object()}) == {}

        specs = build_chat_agents(
            {"claude": FakeLauncher(), "codex": FakeCodexLauncher(), "antigravity": object()},
            test_mode=True,
            command_overrides={"claude": "custom --acp", "codex": "codex-adapter --x"},
        )

        assert list(specs) == ["claude", "codex"]
        assert specs["claude"].command() == ["custom", "--acp"]
        assert specs["codex"].command() == ["codex-adapter", "--x"]

    def test_codex_capability_in_test_mode_is_not_live(self, bin_dir):
        specs = build_chat_agents({"codex": FakeCodexLauncher()}, test_mode=True)

        assert build_chat_capabilities(specs) == {"codex": {"resume": True, "live": False}}


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

    def test_only_the_mode_without_model_or_effort(self):
        intents = codex_config_intents(CodexOptions(reasoning_effort=None))

        assert intents == [ConfigIntent("workspace-write", category="mode", required=True)]


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

    def test_a_real_adapter_keeps_sessions_live_on_posix(self, bin_dir):
        spec = CodexChatSpec(FakeCodexLauncher(), command_override="adapter")

        assert spec.live_sessions is (sys.platform != "win32")

    async def test_catalog_plan_runs_in_home_with_the_host_codex(self, bin_dir):
        spec = CodexChatSpec(FakeCodexLauncher(), command_override="adapter")

        plan = await spec.catalog_plan()

        assert plan.argv == ["adapter"]
        assert plan.cwd == os.path.expanduser("~")
        assert plan.env["CODEX_PATH"] == "/opt/codex/bin/codex"
        assert plan.initial_config == []

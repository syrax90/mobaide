"""Launch specs for ACP agents.

This module is the only place that knows a concrete agent. A spec answers
three questions for the generic chat session: can the agent run here, which
command starts it, and how the create-session options map onto ACP
(config options, system prompt, first prompt). Adding another ACP agent is
one more spec in :func:`build_chat_agents`.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from mobaide.core.cli_launcher import (
    CliBinaryNotFoundError,
    LaunchTarget,
    posix_npm_fallbacks,
    prepend_path_dir,
    shim_safe_argv,
    windows_npm_fallbacks,
)

from mobaide.core.claude_launcher import CLAUDE_SESSION_PRESET_ENV

from .trust import ClaudeFolderTrust, WorkspaceTrust, claude_config_path

if TYPE_CHECKING:
    from mobaide.core.claude_launcher import ClaudeLauncher, ClaudeOptions

CLAUDE_ACP_PACKAGE = "@agentclientprotocol/claude-agent-acp@0.101.0"
CLAUDE_ACP_BIN = "claude-agent-acp"
CLAUDE_ACP_COMMAND_ENV = "MOBAIDE_ACP_CLAUDE_COMMAND"
REAL_IN_TEST_ENV = "MOBAIDE_ACP_REAL"
AVAILABILITY_TTL = 30.0


class ChatAgentUnavailableError(RuntimeError):
    """The chat UI cannot start this agent on this host."""

    def __init__(self, kind: str, hint: str) -> None:
        super().__init__(hint)
        self.kind = kind


@dataclass(frozen=True)
class ConfigIntent:
    """A value to apply after the session starts: matched to an agent config
    option by ``option_id`` first, else by ``category``."""

    value: str | bool
    option_id: str | None = None
    category: str | None = None


@dataclass
class ChatLaunchPlan:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    initial_config: list[ConfigIntent] = field(default_factory=list)
    system_prompt_append: str | None = None
    initial_prompt: str | None = None
    title: str | None = None
    created_files: list[str] = field(default_factory=list)
    # Working directory of the agent process itself; the session folder goes
    # to the agent in ``session/new``. Kept out of the project so files in it
    # cannot pick which adapter runs (npx resolves packages from its cwd).
    process_cwd: str | None = None
    # Asked before the agent opens the folder; None when no gate applies.
    trust: WorkspaceTrust | None = None


class ChatAgentSpec(Protocol):
    kind: str
    display_name: str
    supports_resume: bool

    def available(self) -> bool: ...

    async def prepare(self, target: LaunchTarget, options: Any) -> ChatLaunchPlan: ...

    async def catalog_plan(self) -> ChatLaunchPlan:
        """A launch for listing stored sessions: no project, no options."""
        ...


def _real_agent_in_test_mode() -> bool:
    return os.environ.get(REAL_IN_TEST_ENV, "").lower() in ("1", "true", "yes")


def split_command(command: str) -> list[str]:
    """Split an operator command line; on Windows, also drop the quotes
    ``shlex`` keeps around quoted parts."""
    if sys.platform != "win32":
        return shlex.split(command)
    return [
        part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part
        for part in shlex.split(command, posix=False)
    ]


def fake_agent_argv() -> list[str]:
    # By file path: the fake agent needs only the standard library, so it runs
    # whichever ``mobaide`` copy the interpreter would import.
    return [sys.executable, os.path.join(os.path.dirname(__file__), "fake_agent.py")]


def _which_with_fallbacks(name: str) -> str | None:
    if sys.platform == "win32":
        for candidate in (f"{name}.cmd", f"{name}.exe", name):
            hit = shutil.which(candidate)
            if hit:
                return os.path.abspath(hit)
        fallbacks = windows_npm_fallbacks(name)
    else:
        hit = shutil.which(name)
        if hit:
            return os.path.abspath(hit)
        fallbacks = posix_npm_fallbacks(name)
    for candidate in fallbacks:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class ClaudeChatSpec:
    """Claude Code through the ACP adapter (``claude-agent-acp``).

    The adapter drives the host's own ``claude`` binary
    (``CLAUDE_CODE_EXECUTABLE``), so a chat session uses the same CLI
    version, login and settings as a terminal session.
    """

    kind = "claude"
    display_name = "Claude Code"
    supports_resume = True

    def __init__(
        self,
        launcher: ClaudeLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
    ) -> None:
        self._launcher = launcher
        self._command_override = command_override
        self._test_mode = test_mode
        self._lock = threading.Lock()
        self._cached: tuple[float, list[str] | None, str | None] | None = None

    def _resolve_command(self) -> tuple[list[str] | None, str | None]:
        """``(argv, directory of the resolved executable)``."""
        override = (
            self._command_override or os.environ.get(CLAUDE_ACP_COMMAND_ENV, "")
        ).strip()
        if override:
            return split_command(override), None
        if self._test_mode and not _real_agent_in_test_mode():
            return fake_agent_argv(), None
        adapter = _which_with_fallbacks(CLAUDE_ACP_BIN)
        if adapter:
            return shim_safe_argv(adapter), os.path.dirname(adapter)
        npx = _which_with_fallbacks("npx")
        if npx:
            # npx is a Node script: its directory also holds ``node``.
            return (
                shim_safe_argv(npx, "--yes", "--ignore-scripts", CLAUDE_ACP_PACKAGE),
                os.path.dirname(npx),
            )
        return None, None

    def _resolved(self) -> tuple[list[str] | None, str | None]:
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached[0] < AVAILABILITY_TTL:
                return self._cached[1], self._cached[2]
        argv, bin_dir = self._resolve_command()
        with self._lock:
            self._cached = (now, argv, bin_dir)
        return argv, bin_dir

    def command(self) -> list[str] | None:
        """The adapter command, cached for :data:`AVAILABILITY_TTL` seconds."""
        return self._resolved()[0]

    def available(self) -> bool:
        return self.command() is not None

    def _require_command(self) -> list[str]:
        argv = self.command()
        if argv is None:
            raise ChatAgentUnavailableError(
                self.kind,
                "The chat view needs Node.js 22 or later on the PC (for npx), "
                f"or the {CLAUDE_ACP_BIN} command.",
            )
        return argv

    def _adapter_env(self, base: Mapping[str, str]) -> dict[str, str]:
        env = dict(base)
        try:
            claude_bin = self._launcher.resolve_binary()
        except CliBinaryNotFoundError:
            claude_bin = None
        if claude_bin and "CLAUDE_CODE_EXECUTABLE" not in os.environ:
            env["CLAUDE_CODE_EXECUTABLE"] = claude_bin
            prepend_path_dir(env, os.path.dirname(claude_bin))
        bin_dir = self._resolved()[1]
        if bin_dir:
            prepend_path_dir(env, bin_dir)
        return env

    async def catalog_plan(self) -> ChatLaunchPlan:
        argv = self._require_command()
        env = self._adapter_env(os.environ)
        env.update(CLAUDE_SESSION_PRESET_ENV)
        home = os.path.expanduser("~")
        return ChatLaunchPlan(argv=list(argv), env=env, cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: ClaudeOptions) -> ChatLaunchPlan:
        argv = self._require_command()
        spec = await self._launcher.build(target, options, session_id=None)
        env = self._adapter_env(spec.env)
        return ChatLaunchPlan(
            argv=list(argv),
            env=env,
            cwd=spec.cwd,
            initial_config=claude_config_intents(options),
            system_prompt_append=options.append_system_prompt or None,
            initial_prompt=options.initial_prompt or None,
            title=options.title or None,
            created_files=list(spec.created_files),
            process_cwd=os.path.expanduser("~"),
            trust=(
                None
                if self._uses_fake_agent()
                else ClaudeFolderTrust(spec.cwd, config_path=claude_config_path(env))
            ),
        )

    def _uses_fake_agent(self) -> bool:
        return self.command() == fake_agent_argv()


def claude_config_intents(options: ClaudeOptions) -> list[ConfigIntent]:
    """Map the Claude create-session options onto ACP config values."""
    intents: list[ConfigIntent] = []
    if options.plan_mode:
        intents.append(ConfigIntent("plan", category="mode"))
    elif options.skip_permissions:
        intents.append(ConfigIntent("bypassPermissions", category="mode"))
    if options.model:
        intents.append(ConfigIntent(options.model, category="model"))
    if options.effort and not options.ultracode:
        intents.append(ConfigIntent(options.effort, category="thought_level"))
    if options.fast_mode:
        intents.append(ConfigIntent(True, option_id="fast"))
    if options.ultracode:
        intents.append(ConfigIntent(True, option_id="ultracode"))
    return intents


def build_chat_agents(
    launchers: Mapping[str, Any],
    *,
    test_mode: bool = False,
    command_overrides: Mapping[str, str | None] | None = None,
) -> dict[str, ChatAgentSpec]:
    """The chat-capable agents of this host, keyed by session kind.

    ``test_mode`` (``MOBAIDE_ENV=test``) runs the scripted fake agent instead
    of the real one unless ``MOBAIDE_ACP_REAL=1``.
    """
    overrides = command_overrides or {}
    specs: dict[str, ChatAgentSpec] = {}
    claude = launchers.get("claude")
    if claude is not None:
        specs["claude"] = ClaudeChatSpec(
            claude, command_override=overrides.get("claude"), test_mode=test_mode
        )
    return specs


def build_chat_capabilities(specs: Mapping[str, ChatAgentSpec]) -> dict[str, dict[str, bool]]:
    """``capabilities.chat`` for ``GET /api/version``: one entry per agent
    kind that can start in the chat UI on this host."""
    caps: dict[str, dict[str, bool]] = {}
    for kind, spec in specs.items():
        try:
            if spec.available():
                caps[kind] = {"resume": spec.supports_resume}
        except Exception:
            continue
    return caps

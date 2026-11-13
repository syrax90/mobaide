"""Launch specs for ACP agents.

This module is the only place that knows a concrete agent. A spec answers
three questions for the generic chat session: can the agent run here, which
command starts it, and how the create-session options map onto ACP
(config options, system prompt, first prompt). Adding another ACP agent is
one more spec in :func:`build_chat_agents`.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from mobaide.agent._env_writer import atomic_write
from mobaide.core.codex_auth import codex_login_with_api_key, sanitize_codex_output
from mobaide.core.claude_launcher import CLAUDE_SESSION_PRESET_ENV
from mobaide.core.cli_launcher import (
    WINDOWS_NO_WINDOW,
    CliBinaryNotFoundError,
    LaunchTarget,
    posix_npm_fallbacks,
    prepend_path_dir,
    shim_safe_argv,
    windows_npm_fallbacks,
)
from mobaide.core.projects_config import ProjectConfig

from . import codex_bridge
from . import normalize as nz
from .install import INSTALL_POOL, PROBE_POOL, AdapterInstaller, NpmAdapter
from .presence import ConversationPresence, default_app_data_dir
from .signin import SignInMethod
from .trust import (
    AntigravityFolderTrust,
    ClaudeFolderTrust,
    CodexFolderTrust,
    CopilotFolderTrust,
    CursorFolderTrust,
    WorkspaceTrust,
    antigravity_settings_path,
    claude_config_path,
    codex_config_path,
    copilot_config_path,
    cursor_projects_dir,
)

if TYPE_CHECKING:
    from mobaide.core.antigravity_launcher import AntigravityLauncher, AntigravityOptions
    from mobaide.core.claude_launcher import ClaudeLauncher, ClaudeOptions
    from mobaide.core.codex_launcher import CodexLauncher, CodexOptions
    from mobaide.core.copilot_launcher import CopilotLauncher, CopilotOptions
    from mobaide.core.cursor_launcher import CursorLauncher, CursorOptions
    from mobaide.core.opencode_launcher import OpenCodeLauncher, OpenCodeOptions

# ``resolve_before`` follows each release: dependencies resolve as they were
# then, not to whatever a range matches on the day a PC first installs.
CLAUDE_ADAPTER = NpmAdapter(
    "@agentclientprotocol/claude-agent-acp", "0.101.0", "claude-agent-acp", "2026-11-06T00:00:00Z"
)
CODEX_ADAPTER = NpmAdapter(
    "@agentclientprotocol/codex-acp", "2.5.0", "codex-acp", "2026-10-30T00:00:00Z"
)
CLAUDE_ACP_PACKAGE = CLAUDE_ADAPTER.spec
CLAUDE_ACP_BIN = CLAUDE_ADAPTER.bin_name
CLAUDE_ACP_COMMAND_ENV = "MOBAIDE_ACP_CLAUDE_COMMAND"
CODEX_ACP_COMMAND_ENV = "MOBAIDE_ACP_CODEX_COMMAND"
ANTIGRAVITY_ACP_COMMAND_ENV = "MOBAIDE_ACP_ANTIGRAVITY_COMMAND"
OPENCODE_ACP_COMMAND_ENV = "MOBAIDE_ACP_OPENCODE_COMMAND"
COPILOT_ACP_COMMAND_ENV = "MOBAIDE_ACP_COPILOT_COMMAND"
CURSOR_ACP_COMMAND_ENV = "MOBAIDE_ACP_CURSOR_COMMAND"
REAL_IN_TEST_ENV = "MOBAIDE_ACP_REAL"
AVAILABILITY_TTL = 30.0
NODE_HINT = "The chat view needs Node.js 22 or later on the PC."
AGY_HINT = (
    "The chat view needs an Antigravity CLI with ACP support. "
    "Run `agy update` on the PC, then try again."
)
AGY_PROBE_TIMEOUT = 10.0
OPENCODE_HINT = (
    "OpenCode is not installed on the PC. Install it from "
    "https://opencode.ai/docs/, then try again."
)
# OpenCode has no account of its own to sign in to: a provider that refuses
# its key is connected again in OpenCode itself.
OPENCODE_SIGN_IN_HINT = (
    "OpenCode could not sign in to the model provider. In New session, pick OpenCode "
    "and use Connect a provider, or run `opencode auth login` on the PC. Then tap Restart."
)
COPILOT_HINT = (
    "GitHub Copilot CLI is not installed on the PC. Install it with "
    "`npm install -g @github/copilot`, then try again."
)
CURSOR_HINT = (
    "Cursor CLI is not installed on the PC. Install it with "
    "`curl https://cursor.com/install -fsS | bash` (on Windows, in PowerShell: "
    "`irm 'https://cursor.com/install?win32=true' | iex`), then try again."
)
# Test mode: while this file exists the fake agent is signed out, and its
# fake login removes it.
FAKE_SIGNED_OUT_ENV = "MOBAIDE_FAKE_ACP_SIGNED_OUT"
CODEX_USER_CODE_PATTERN = r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b"
# GitHub's device code, as ``copilot login --device-code`` prints it.
COPILOT_USER_CODE_PATTERN = r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b"
# The config option of ``copilot --acp`` that turns its approval requests off.
COPILOT_ALLOW_ALL_OPTION = "allow_all"
# With this in the environment ``cursor-agent login`` prints the sign-in page
# and does not open a browser on the PC.
CURSOR_NO_BROWSER_ENV = "NO_OPEN_BROWSER"
# Asks ``cursor-agent acp`` for a model list with plain names, and the
# settings of the picked model (effort, context, speed) as separate options.
CURSOR_CLIENT_META = {"parameterizedModelPicker": True}
CODEX_BRIDGE_SCRIPT = os.path.abspath(codex_bridge.__file__)


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
    # The session must not start without it (a sandbox choice).
    required: bool = False


@dataclass
class ChatLaunchPlan:
    argv: list[str]
    env: dict[str, str] = field(repr=False)
    cwd: str
    initial_config: list[ConfigIntent] = field(default_factory=list)
    system_prompt_append: str | None = None
    initial_prompt: str | None = None
    title: str | None = None
    created_files: list[str] = field(default_factory=list)
    # Working directory of the agent process itself; the session folder goes
    # to the agent in ``session/new``. Kept out of the project so files in it
    # cannot pick which adapter runs.
    process_cwd: str | None = None
    # Asked before the agent opens the folder; None when no gate applies.
    trust: WorkspaceTrust | None = None
    # Blocking; installs the adapter on first use and returns the argv to
    # run. Set only while ``argv`` is not known yet.
    ensure_argv: Callable[[], list[str]] | None = None
    # The agent session lives in a process other clients share (Codex's
    # shared app-server): closing the chat leaves it to them.
    shared_host: bool = False
    # Folders the session may also work in (ACP ``additionalDirectories``).
    additional_dirs: list[str] = field(default_factory=list)
    # Turns launch choices into config values once the agent has listed its
    # options (``session/new`` result): ``(result, intents) -> intents``.
    config_resolver: Callable[[Mapping[str, Any], list[ConfigIntent]], list[ConfigIntent]] | None = None
    # The agent's "conversation already open" marker, held while the chat
    # has the conversation open (see :mod:`.presence`).
    presence: ConversationPresence | None = None
    # How the chat can sign the agent in on this PC (see :mod:`.signin`).
    sign_in: list[SignInMethod] = field(default_factory=list)
    # What to tell the user when the agent needs a sign-in that the chat
    # cannot do; None gives the generic text.
    sign_in_hint: str | None = None
    # Accept each permission request without asking (the agent's own
    # "auto-approve" switch, for an agent whose ACP server has none).
    auto_approve: bool = False
    # Extra ``clientCapabilities._meta`` keys for the agent's ``initialize``.
    client_meta: Mapping[str, Any] = field(default_factory=dict)
    # The agent writes its tool titles in Markdown (code spans).
    markdown_titles: bool = False


class ChatAgentSpec(Protocol):
    kind: str
    display_name: str
    supports_resume: bool
    # Sessions stay live with the agent's own apps on the PC.
    live_sessions: bool
    # ``session/list`` ignores its ``cwd`` filter: the catalog filters.
    lists_all_folders: bool

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


def fake_agent_argv(flavor: str | None = None) -> list[str]:
    # By file path: the fake agent needs only the standard library, so it runs
    # whichever ``mobaide`` copy the interpreter would import.
    argv = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_agent.py")]
    if flavor:
        argv += ["--flavor", flavor]
    return argv


def fake_login_argv(flavor: str | None = None) -> list[str]:
    argv = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_agent.py"), "login"]
    if flavor:
        argv += ["--flavor", flavor]
    return argv


async def _fake_key_login(value: str) -> tuple[bool, str]:
    if value == "bad":
        return False, "Incorrect API key provided (fake agent)."
    marker = os.environ.get(FAKE_SIGNED_OUT_ENV)
    if marker:
        try:
            os.remove(marker)
        except FileNotFoundError:
            pass
    return True, ""


CLAUDE_SIGN_IN_TEXT = (
    "Open the sign-in page and sign in. Then copy the code that the page shows "
    "and paste it here."
)
CODEX_SIGN_IN_TEXT = (
    "Open the sign-in page, sign in to ChatGPT and enter the code below. "
    "The chat continues when you are done."
)
CODEX_KEY_TEXT = "Paste an OpenAI API key. Codex keeps it on your PC."
AGY_SIGN_IN_TEXT = (
    "Open the sign-in page and sign in with Google. Then paste the code that the "
    "page shows, or the full address of the page you land on."
)
COPILOT_SIGN_IN_TEXT = (
    "Open the sign-in page, sign in to GitHub and enter the code below. "
    "The chat continues when you are done."
)
CURSOR_SIGN_IN_TEXT = (
    "Open the sign-in page and sign in to Cursor. You can do that on this "
    "device. The chat continues when you are done."
)


def claude_sign_in_methods(
    claude_bin: str | None, env: Mapping[str, str], *, fake: bool = False
) -> list[SignInMethod]:
    """``claude auth login``: it prints the sign-in page and reads the code
    that page shows (or finishes alone when the browser on the PC is used)."""
    if claude_bin is None and not fake:
        return []
    home = os.path.expanduser("~")
    methods = []
    for method_id, name, description, flag in (
        ("claude_ai", "Claude subscription", "Pro, Max, Team or Enterprise plan", "--claudeai"),
        ("console", "Anthropic Console", "API usage billing", "--console"),
    ):
        argv = fake_login_argv() if fake else shim_safe_argv(claude_bin or "", "auth", "login", flag)
        methods.append(
            SignInMethod(
                method_id,
                name,
                description,
                CLAUDE_SIGN_IN_TEXT,
                argv=argv,
                env=dict(env),
                cwd=home,
                reads_code=True,
            )
        )
    return methods


async def _codex_key_login(
    api_key: str, *, codex_bin: str, env: Mapping[str, str]
) -> tuple[bool, str]:
    """An API key login through the shared app-server while it runs, else
    ``codex login --with-api-key``."""
    result = await codex_bridge.login_with_api_key(codex_bridge.control_socket(env), api_key)
    if result is not None:
        ok, detail = result
        if ok:
            return True, ""
        return False, sanitize_codex_output(detail, api_key) or "Codex did not accept the API key."
    return await codex_login_with_api_key(api_key, codex_bin=codex_bin, env=env)


def codex_sign_in_methods(
    codex_bin: str | None,
    env: Mapping[str, str],
    *,
    fake: bool = False,
    shared_app_server: bool = False,
) -> list[SignInMethod]:
    """ChatGPT through ``codex login --device-auth`` (a one-time code to
    enter on the sign-in page), or an API key through ``codex login
    --with-api-key``.

    With ``shared_app_server`` (Codex's shared app-server can run on this
    PC), both go through that app-server while it runs: it never reads a
    login that ``codex login`` saves next to it (see :mod:`.codex_bridge`)."""
    if codex_bin is None and not fake:
        return []
    login_env = dict(env)
    if fake:
        device_argv = [*fake_login_argv("codex"), "--device"]
        submit_key = _fake_key_login
    elif shared_app_server:
        device_argv = [
            sys.executable,
            "-I",
            CODEX_BRIDGE_SCRIPT,
            codex_bin or "",
            *codex_bridge.DEVICE_LOGIN_ARGS,
        ]
        submit_key = functools.partial(_codex_key_login, codex_bin=codex_bin or "", env=login_env)
    else:
        device_argv = shim_safe_argv(codex_bin or "", "login", "--device-auth")
        submit_key = functools.partial(
            codex_login_with_api_key, codex_bin=codex_bin or "", env=login_env
        )
    return [
        SignInMethod(
            "chatgpt",
            "ChatGPT account",
            "Plus, Pro, Business or Enterprise plan",
            CODEX_SIGN_IN_TEXT,
            argv=device_argv,
            env=login_env,
            cwd=os.path.expanduser("~"),
            user_code_pattern=CODEX_USER_CODE_PATTERN,
        ),
        SignInMethod(
            "api_key",
            "OpenAI API key",
            "API usage billing",
            CODEX_KEY_TEXT,
            input_label="OpenAI API key",
            submit_secret=submit_key,
        ),
    ]


def antigravity_sign_in_methods(
    agy: str | None, env: Mapping[str, str], *, fake: bool = False
) -> list[SignInMethod]:
    """``agy acp --login``, the terminal login ``agy acp`` itself offers:
    it prints the Google sign-in page and reads the code that page shows."""
    if agy is None and not fake:
        return []
    argv = fake_login_argv("antigravity") if fake else shim_safe_argv(agy or "", "acp", "--login")
    return [
        SignInMethod(
            "google",
            "Google account",
            "Sign in with Google",
            AGY_SIGN_IN_TEXT,
            argv=argv,
            env=dict(env),
            cwd=os.path.expanduser("~"),
            reads_code=True,
            input_label="Code or address from the sign-in page",
        )
    ]


def copilot_sign_in_methods(
    copilot_bin: str | None, env: Mapping[str, str], *, fake: bool = False
) -> list[SignInMethod]:
    """``copilot login --device-code``: it prints GitHub's device page and a
    one-time code to enter there, then finishes alone."""
    if copilot_bin is None and not fake:
        return []
    argv = (
        [*fake_login_argv("copilot"), "--device"]
        if fake
        else shim_safe_argv(copilot_bin or "", "login", "--device-code")
    )
    return [
        SignInMethod(
            "github",
            "GitHub account",
            "The Copilot plan of your GitHub account",
            COPILOT_SIGN_IN_TEXT,
            argv=argv,
            env=dict(env),
            cwd=os.path.expanduser("~"),
            user_code_pattern=COPILOT_USER_CODE_PATTERN,
        )
    ]


def cursor_sign_in_methods(
    cursor_bin: str | None, env: Mapping[str, str], *, fake: bool = False
) -> list[SignInMethod]:
    """``cursor-agent login``: it prints the sign-in page, which works in a
    browser on any device, then finishes alone."""
    if cursor_bin is None and not fake:
        return []
    argv = (
        [*fake_login_argv("cursor"), "--device"]
        if fake
        else shim_safe_argv(cursor_bin or "", "login")
    )
    return [
        SignInMethod(
            "cursor",
            "Cursor account",
            "The plan of your Cursor account",
            CURSOR_SIGN_IN_TEXT,
            argv=argv,
            env={**env, CURSOR_NO_BROWSER_ENV: "1"},
            cwd=os.path.expanduser("~"),
        )
    ]


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


def _which_node() -> str | None:
    if sys.platform == "win32":
        hit = shutil.which("node.exe") or shutil.which("node")
        return os.path.abspath(hit) if hit else None
    return _which_with_fallbacks("node")


@dataclass(frozen=True)
class _Resolution:
    argv: list[str] | None
    bin_dir: str | None = None
    # (npm, node) when the pinned adapter still has to be installed.
    install_with: tuple[str, str] | None = None


class _ChatSpecBase:
    """What every agent spec shares: the operator override
    (``command_env``) and the fake agent in test mode come before the
    agent's own lookup (:meth:`command`)."""

    kind = ""
    display_name = ""
    supports_resume = True
    live_sessions = False
    lists_all_folders = False
    command_env = ""
    fake_flavor: str | None = None

    def __init__(
        self,
        launcher: Any,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
    ) -> None:
        self._launcher = launcher
        self._command_override = command_override
        self._test_mode = test_mode

    def _fixed_command(self) -> list[str] | None:
        """The override, else the fake agent in test mode; None when the
        agent's own lookup decides."""
        override = (self._command_override or os.environ.get(self.command_env, "")).strip()
        if override:
            return split_command(override)
        if self._test_mode and not _real_agent_in_test_mode():
            return fake_agent_argv(self.fake_flavor)
        return None

    def command(self) -> list[str] | None:
        raise NotImplementedError

    def uses_fake_agent(self) -> bool:
        return self.command() == fake_agent_argv(self.fake_flavor)


class _AdapterSpec(_ChatSpecBase):
    """An agent whose ACP adapter is an npm package (see :mod:`.install`).

    The command is, in order: the operator override, the fake agent in test
    mode, the adapter command on PATH, and the pinned install under
    ``adapters_dir`` (installed on first use).
    """

    adapter: NpmAdapter

    def __init__(
        self,
        launcher: Any,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
        adapters_dir: Path | None = None,
    ) -> None:
        super().__init__(launcher, command_override=command_override, test_mode=test_mode)
        self._installer = AdapterInstaller(self.adapter, adapters_dir) if adapters_dir else None
        self._lock = threading.Lock()
        self._cached: tuple[float, _Resolution] | None = None

    def _resolve_command(self) -> _Resolution:
        fixed = self._fixed_command()
        if fixed is not None:
            return _Resolution(fixed)
        on_path = _which_with_fallbacks(self.adapter.bin_name)
        if on_path:
            return _Resolution(shim_safe_argv(on_path), os.path.dirname(on_path))
        node = _which_node()
        if node is None or self._installer is None:
            return _Resolution(None)
        node_dir = os.path.dirname(node)
        entry = self._installer.entry()
        if entry is not None:
            return _Resolution([node, str(entry)], node_dir)
        npm = _which_with_fallbacks("npm")
        if npm is None:
            return _Resolution(None)
        return _Resolution(None, node_dir, install_with=(npm, node))

    def _resolved(self) -> _Resolution:
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached[0] < AVAILABILITY_TTL:
                return self._cached[1]
        resolution = self._resolve_command()
        with self._lock:
            self._cached = (now, resolution)
        return resolution

    def command(self) -> list[str] | None:
        """The adapter command, or None until the pinned adapter is installed.
        Cached for :data:`AVAILABILITY_TTL` seconds."""
        return self._resolved().argv

    def available(self) -> bool:
        resolution = self._resolved()
        return resolution.argv is not None or resolution.install_with is not None

    def _launch(self, env: Mapping[str, str]) -> tuple[list[str], Callable[[], list[str]] | None]:
        """``(argv, ensure_argv)``: a known command, or an installer to run."""
        resolution = self._resolved()
        if resolution.argv is not None:
            return list(resolution.argv), None
        if resolution.install_with is None or self._installer is None:
            raise ChatAgentUnavailableError(self.kind, NODE_HINT)
        npm, node = resolution.install_with
        installer = self._installer
        install_env = dict(env)

        def ensure() -> list[str]:
            argv = [node, str(installer.install(npm, install_env))]
            with self._lock:
                self._cached = None
            return argv

        return [], ensure

    def _with_bin_dir(self, env: dict[str, str]) -> dict[str, str]:
        bin_dir = self._resolved().bin_dir
        if bin_dir:
            prepend_path_dir(env, bin_dir)
        return env


class ClaudeChatSpec(_AdapterSpec):
    """Claude Code through the ACP adapter (``claude-agent-acp``).

    The adapter drives the host's own ``claude`` binary
    (``CLAUDE_CODE_EXECUTABLE``), so a chat session uses the same CLI
    version, login and settings as a terminal session.
    """

    kind = "claude"
    display_name = "Claude Code"
    adapter = CLAUDE_ADAPTER
    command_env = CLAUDE_ACP_COMMAND_ENV

    def __init__(
        self,
        launcher: ClaudeLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
        adapters_dir: Path | None = None,
    ) -> None:
        super().__init__(
            launcher,
            command_override=command_override,
            test_mode=test_mode,
            adapters_dir=adapters_dir,
        )

    def _adapter_env(self, base: Mapping[str, str]) -> dict[str, str]:
        env = dict(base)
        claude_bin = self._claude_bin()
        if claude_bin and "CLAUDE_CODE_EXECUTABLE" not in os.environ:
            env["CLAUDE_CODE_EXECUTABLE"] = claude_bin
            prepend_path_dir(env, os.path.dirname(claude_bin))
        return self._with_bin_dir(env)

    async def catalog_plan(self) -> ChatLaunchPlan:
        env = self._adapter_env(os.environ)
        env.update(CLAUDE_SESSION_PRESET_ENV)
        argv = await _known_argv(*self._launch(env))
        home = os.path.expanduser("~")
        return ChatLaunchPlan(argv=argv, env=env, cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: ClaudeOptions) -> ChatLaunchPlan:
        self._launch(os.environ)  # fail before the launcher creates a worktree
        spec = await self._launcher.build(target, options, session_id=None)
        env = self._adapter_env(spec.env)
        argv, ensure = self._launch(env)
        return ChatLaunchPlan(
            argv=argv,
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
                if self.uses_fake_agent()
                else ClaudeFolderTrust(spec.cwd, config_path=claude_config_path(env))
            ),
            ensure_argv=ensure,
            sign_in=claude_sign_in_methods(
                self._claude_bin(), spec.env, fake=self.uses_fake_agent()
            ),
        )

    def _claude_bin(self) -> str | None:
        try:
            return self._launcher.resolve_binary()
        except CliBinaryNotFoundError:
            return None


class CodexChatSpec(_AdapterSpec):
    """Codex through the ACP adapter (``codex-acp``), which runs the host's
    ``codex app-server`` (``CODEX_PATH``): same CLI, login and settings as a
    terminal session.

    While Codex's shared app-server runs (``codex`` in a terminal starts it),
    the session joins it through :mod:`.codex_bridge`, so a session open in a
    terminal and the chat stay in sync live.
    """

    kind = "codex"
    display_name = "Codex"
    adapter = CODEX_ADAPTER
    command_env = CODEX_ACP_COMMAND_ENV
    fake_flavor = "codex"

    def __init__(
        self,
        launcher: CodexLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
        adapters_dir: Path | None = None,
    ) -> None:
        super().__init__(
            launcher,
            command_override=command_override,
            test_mode=test_mode,
            adapters_dir=adapters_dir,
        )
        self._adapters_dir = adapters_dir

    @property
    def live_sessions(self) -> bool:  # type: ignore[override]
        return sys.platform != "win32" and not self.uses_fake_agent()

    def _codex_bin(self) -> str | None:
        try:
            return self._launcher.resolve_binary()
        except CliBinaryNotFoundError:
            return None

    def _adapter_env(self, base: Mapping[str, str], codex_bin: str | None) -> dict[str, str]:
        env = dict(base)
        if codex_bin and "CODEX_PATH" not in os.environ:
            env["CODEX_PATH"] = codex_bin
            prepend_path_dir(env, os.path.dirname(codex_bin))
        return self._with_bin_dir(env)

    async def catalog_plan(self) -> ChatLaunchPlan:
        env = self._adapter_env(os.environ, self._codex_bin())
        argv = await _known_argv(*self._launch(env))
        home = os.path.expanduser("~")
        return ChatLaunchPlan(argv=argv, env=env, cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: CodexOptions) -> ChatLaunchPlan:
        self._launch(os.environ)
        # No sign-in check: a chat that is not signed in offers the sign-in.
        spec = await self._launcher.build(target, options, session_id=None, require_auth=False)
        codex_bin = self._codex_bin()
        env = self._adapter_env(spec.env, codex_bin)
        shared = False
        if codex_bin:
            bridge = await asyncio.get_running_loop().run_in_executor(
                PROBE_POOL, self._shared_codex_path, target, env, codex_bin
            )
            if bridge is not None:
                env["CODEX_PATH"] = bridge
                shared = True
        argv, ensure = self._launch(env)
        fake = self.uses_fake_agent()
        return ChatLaunchPlan(
            argv=argv,
            env=env,
            cwd=spec.cwd,
            initial_config=codex_config_intents(options),
            initial_prompt=options.initial_prompt or None,
            created_files=list(spec.created_files),
            process_cwd=os.path.expanduser("~"),
            trust=None if fake else CodexFolderTrust(spec.cwd, config_path=codex_config_path(env)),
            ensure_argv=ensure,
            shared_host=shared and not fake,
            sign_in=codex_sign_in_methods(
                codex_bin, spec.env, fake=fake, shared_app_server=self.live_sessions
            ),
        )

    def _shared_codex_path(
        self, target: LaunchTarget, env: Mapping[str, str], codex_bin: str
    ) -> str | None:
        """The bridge ``CODEX_PATH`` when the shared app-server can host the
        session, else None (blocking: a socket probe and a file write)."""
        if not self._can_share(target, env):
            return None
        return self._bridge_command(codex_bin)

    def _can_share(self, target: LaunchTarget, env: Mapping[str, str]) -> bool:
        if not self.live_sessions or self._adapters_dir is None or "CODEX_PATH" in os.environ:
            return False
        # The shared app-server runs commands with its own environment, so a
        # project that pins a conda env gets a private app-server instead.
        if isinstance(target, ProjectConfig) and target.conda_env:
            return False
        return codex_bridge.socket_reachable(codex_bridge.control_socket(env))

    def _bridge_command(self, codex_bin: str) -> str:
        """A ``CODEX_PATH`` that runs the bridge for ``app-server`` and the
        real ``codex`` for anything else."""
        assert self._adapters_dir is not None
        script = (
            "#!/bin/sh\n"
            "# Written by Mobaide: lets the Codex ACP adapter join the app-server\n"
            "# that Codex already runs on this PC (see mobaide/acp/codex_bridge.py).\n"
            'if [ "$#" -eq 1 ] && [ "$1" = "app-server" ]; then\n'
            f"  exec {shlex.quote(sys.executable)} -I {shlex.quote(CODEX_BRIDGE_SCRIPT)} {shlex.quote(codex_bin)}\n"
            "fi\n"
            f'exec {shlex.quote(codex_bin)} "$@"\n'
        )
        digest = hashlib.sha256(script.encode("utf-8")).hexdigest()[:12]
        path = self._adapters_dir / f"codex-app-server-{digest}.sh"
        if not path.is_file():
            atomic_write(path, script, mode=0o755)
        return str(path)


def _agy_supports_acp(agy: str) -> bool | None:
    """``agy help acp`` exits 0 only on builds that have the ACP server.
    None when the probe itself failed (try again later)."""
    try:
        done = subprocess.run(
            shim_safe_argv(agy, "help", "acp"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=AGY_PROBE_TIMEOUT,
            creationflags=WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0


class AntigravityChatSpec(_ChatSpecBase):
    """Antigravity through the ACP server built into its CLI (``agy acp``),
    so chat and terminal sessions share the CLI version, login, settings and
    stored conversations.

    ``agy acp`` works in its own working directory, not in the ``cwd`` of
    ``session/new``, so every session runs in its own process started in the
    session folder. Its ``session/list`` lists every folder's conversations.
    """

    kind = "antigravity"
    display_name = "Antigravity"
    lists_all_folders = True
    command_env = ANTIGRAVITY_ACP_COMMAND_ENV
    fake_flavor = "antigravity"

    def __init__(
        self,
        launcher: AntigravityLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
        app_data_dir: Path | None = None,
    ) -> None:
        super().__init__(launcher, command_override=command_override, test_mode=test_mode)
        self._app_data_dir = app_data_dir or default_app_data_dir()
        self.presence = ConversationPresence(self._app_data_dir)
        # One probe at a time: concurrent callers wait for its answer.
        self._probe_lock = threading.Lock()
        # (checked at, binary path and mtime, argv or None, final). A final
        # answer holds for that binary; a missing agy or a failed probe is
        # checked again after AVAILABILITY_TTL.
        self._cached: tuple[float, tuple[str, float] | None, list[str] | None, bool] | None = None

    def _agy_identity(self) -> tuple[str, float] | None:
        try:
            agy = self._launcher.resolve_binary()
            return agy, os.stat(agy).st_mtime
        except (CliBinaryNotFoundError, OSError):
            return None

    def _resolve_command(self) -> list[str] | None:
        fixed = self._fixed_command()
        if fixed is not None:
            return fixed
        identity = self._agy_identity()
        cached = self._fresh_cache(identity)
        if cached is None:
            with self._probe_lock:
                cached = self._fresh_cache(identity)
                if cached is None:
                    cached = self._probe(identity)
                    self._cached = cached
        argv = cached[2]
        return list(argv) if argv is not None else None

    def _fresh_cache(
        self, identity: tuple[str, float] | None
    ) -> tuple[float, tuple[str, float] | None, list[str] | None, bool] | None:
        cached = self._cached
        # An updated binary (new mtime) is probed again at once.
        if cached is None or cached[1] != identity:
            return None
        if cached[3] or time.monotonic() - cached[0] < AVAILABILITY_TTL:
            return cached
        return None

    @staticmethod
    def _probe(
        identity: tuple[str, float] | None,
    ) -> tuple[float, tuple[str, float] | None, list[str] | None, bool]:
        now = time.monotonic()
        if identity is None:
            return (now, None, None, False)
        supported = _agy_supports_acp(identity[0])
        argv = shim_safe_argv(identity[0], "acp") if supported else None
        return (now, identity, argv, supported is not None)

    def command(self) -> list[str] | None:
        return self._resolve_command()

    def available(self) -> bool:
        return self._resolve_command() is not None

    def _argv(self) -> list[str]:
        argv = self._resolve_command()
        if argv is None:
            raise ChatAgentUnavailableError(self.kind, AGY_HINT)
        return argv

    async def catalog_plan(self) -> ChatLaunchPlan:
        loop = asyncio.get_running_loop()
        argv = await loop.run_in_executor(PROBE_POOL, self._argv)
        home = os.path.expanduser("~")
        return ChatLaunchPlan(argv=argv, env=dict(os.environ), cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: AntigravityOptions) -> ChatLaunchPlan:
        loop = asyncio.get_running_loop()
        # Before the launcher can create a worktree.
        argv = await loop.run_in_executor(PROBE_POOL, self._argv)
        fake = argv == fake_agent_argv(self.fake_flavor)
        spec = await self._launcher.build(target, options, session_id=None)
        if options.skip_permissions:
            argv.append("--dangerously-skip-permissions")
        if options.sandbox:
            argv.append("--sandbox")
        return ChatLaunchPlan(
            argv=argv,
            env=dict(spec.env),
            cwd=spec.cwd,
            initial_config=antigravity_config_intents(options),
            created_files=list(spec.created_files),
            process_cwd=spec.cwd,
            trust=(
                None
                if fake
                else AntigravityFolderTrust(
                    spec.cwd, settings_path=antigravity_settings_path(self._app_data_dir)
                )
            ),
            # ACP takes absolute paths; ``--add-dir`` read them from the cwd.
            additional_dirs=[
                os.path.normpath(os.path.join(spec.cwd, os.path.expanduser(d)))
                for d in options.add_dirs
            ],
            config_resolver=resolve_antigravity_config,
            presence=None if fake else self.presence,
            sign_in=antigravity_sign_in_methods(
                None if fake else self._agy_identity_path(), spec.env, fake=fake
            ),
        )

    def _agy_identity_path(self) -> str | None:
        identity = self._agy_identity()
        return identity[0] if identity else None


class OpenCodeChatSpec(_ChatSpecBase):
    """OpenCode through the ACP server built into its CLI (``opencode acp``),
    so chat and terminal sessions share the CLI version, providers, settings
    and stored sessions.

    ``opencode acp`` runs a server of its own, apart from the one OpenCode's
    terminal UI uses: a session open in both is not kept in sync live.
    OpenCode asks for no folder trust and starts without an account.
    """

    kind = "opencode"
    display_name = "OpenCode"
    command_env = OPENCODE_ACP_COMMAND_ENV
    fake_flavor = "opencode"

    def __init__(
        self,
        launcher: OpenCodeLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
    ) -> None:
        super().__init__(launcher, command_override=command_override, test_mode=test_mode)

    def command(self) -> list[str] | None:
        fixed = self._fixed_command()
        if fixed is not None:
            return fixed
        try:
            return shim_safe_argv(self._launcher.resolve_binary(), "acp")
        except CliBinaryNotFoundError:
            return None

    def available(self) -> bool:
        return self.command() is not None

    def _argv(self) -> list[str]:
        argv = self.command()
        if argv is None:
            raise ChatAgentUnavailableError(self.kind, OPENCODE_HINT)
        return argv

    async def catalog_plan(self) -> ChatLaunchPlan:
        home = os.path.expanduser("~")
        return ChatLaunchPlan(argv=self._argv(), env=dict(os.environ), cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: OpenCodeOptions) -> ChatLaunchPlan:
        # Before the launcher can create a worktree.
        argv = self._argv()
        cwd = await self._launcher.resolve_cwd(target, options)
        return ChatLaunchPlan(
            argv=argv,
            env=self._launcher.build_env(target),
            cwd=cwd,
            initial_prompt=options.initial_prompt or None,
            process_cwd=os.path.expanduser("~"),
            sign_in_hint=OPENCODE_SIGN_IN_HINT,
            auto_approve=options.auto_approve,
        )


class CopilotChatSpec(_ChatSpecBase):
    """GitHub Copilot through the ACP server built into its CLI
    (``copilot --acp``), so chat and terminal sessions share the CLI version,
    login, settings and stored sessions.

    The server takes the session folder from ``session/new`` and filters
    ``session/list`` by folder. It loads a folder's hooks and MCP servers
    without Copilot's own trust question, so the chat asks it first.
    """

    kind = "copilot"
    display_name = "GitHub Copilot"
    command_env = COPILOT_ACP_COMMAND_ENV
    fake_flavor = "copilot"

    def __init__(
        self,
        launcher: CopilotLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
    ) -> None:
        super().__init__(launcher, command_override=command_override, test_mode=test_mode)

    def _copilot_bin(self) -> str | None:
        try:
            return self._launcher.resolve_binary()
        except CliBinaryNotFoundError:
            return None

    def command(self) -> list[str] | None:
        fixed = self._fixed_command()
        if fixed is not None:
            return fixed
        copilot_bin = self._copilot_bin()
        return shim_safe_argv(copilot_bin, "--acp") if copilot_bin else None

    def available(self) -> bool:
        return self.command() is not None

    def _argv(self) -> list[str]:
        argv = self.command()
        if argv is None:
            raise ChatAgentUnavailableError(self.kind, COPILOT_HINT)
        return argv

    async def catalog_plan(self) -> ChatLaunchPlan:
        home = os.path.expanduser("~")
        env = dict(os.environ)
        # An npm install of Copilot is a Node script: ``node`` sits next to it.
        copilot_bin = self._copilot_bin()
        if copilot_bin:
            prepend_path_dir(env, os.path.dirname(copilot_bin))
        return ChatLaunchPlan(argv=self._argv(), env=env, cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: CopilotOptions) -> ChatLaunchPlan:
        # Before the launcher can create a worktree.
        argv = self._argv()
        fake = argv == fake_agent_argv(self.fake_flavor)
        cwd = await self._launcher.resolve_cwd(target, options)
        env = self._launcher.build_env(target)
        return ChatLaunchPlan(
            argv=argv,
            env=env,
            cwd=cwd,
            initial_config=copilot_config_intents(options),
            process_cwd=os.path.expanduser("~"),
            trust=None if fake else CopilotFolderTrust(cwd, config_path=copilot_config_path(env)),
            sign_in=copilot_sign_in_methods(
                None if fake else self._copilot_bin(), env, fake=fake
            ),
        )


class CursorChatSpec(_ChatSpecBase):
    """Cursor through the ACP server built into its CLI (``cursor-agent
    acp``), so chat and terminal sessions share the CLI version, login and
    settings.

    The server keeps its sessions apart from the ones of Cursor's terminal
    UI: the chat lists and continues chat sessions only. It takes the
    session folder from ``session/new`` but reads the folder's rules, hooks
    and MCP servers from its own working directory, so each chat runs its own
    server in the session folder. The server does not ask Cursor's trust
    question, so the chat asks it first.
    """

    kind = "cursor"
    display_name = "Cursor"
    command_env = CURSOR_ACP_COMMAND_ENV
    fake_flavor = "cursor"

    def __init__(
        self,
        launcher: CursorLauncher,
        *,
        command_override: str | None = None,
        test_mode: bool = False,
    ) -> None:
        super().__init__(launcher, command_override=command_override, test_mode=test_mode)

    def _cursor_bin(self) -> str | None:
        try:
            return self._launcher.resolve_binary()
        except CliBinaryNotFoundError:
            return None

    def _own_command(self, *, force: bool = False) -> list[str] | None:
        cursor_bin = self._cursor_bin()
        if cursor_bin is None:
            return None
        # ``--force`` is an option of the program, not of its ``acp`` command.
        return shim_safe_argv(cursor_bin, *(["--force"] if force else []), "acp")

    def command(self) -> list[str] | None:
        fixed = self._fixed_command()
        return fixed if fixed is not None else self._own_command()

    def available(self) -> bool:
        return self.command() is not None

    def _argv(self) -> list[str]:
        argv = self.command()
        if argv is None:
            raise ChatAgentUnavailableError(self.kind, CURSOR_HINT)
        return argv

    async def catalog_plan(self) -> ChatLaunchPlan:
        home = os.path.expanduser("~")
        return ChatLaunchPlan(argv=self._argv(), env=dict(os.environ), cwd=home, process_cwd=home)

    async def prepare(self, target: LaunchTarget, options: CursorOptions) -> ChatLaunchPlan:
        # Before the launcher can create a worktree.
        argv = self._argv()
        fixed = self._fixed_command() is not None
        fake = argv == fake_agent_argv(self.fake_flavor)
        if options.force and not fixed:
            argv = self._own_command(force=True) or argv
        cwd = await self._launcher.resolve_cwd(target, options)
        env = self._launcher.build_env(target)
        return ChatLaunchPlan(
            argv=argv,
            env=env,
            cwd=cwd,
            process_cwd=cwd,
            trust=None if fake else CursorFolderTrust(cwd, projects_dir=cursor_projects_dir(env)),
            sign_in=cursor_sign_in_methods(
                None if fake else self._cursor_bin(), env, fake=fake
            ),
            # A command the operator set, or the fake agent, has no place
            # for Cursor's own flag: the chat accepts the requests instead.
            auto_approve=options.force and fixed,
            client_meta=CURSOR_CLIENT_META,
            markdown_titles=True,
        )


async def _known_argv(argv: list[str], ensure: Callable[[], list[str]] | None) -> list[str]:
    if ensure is None:
        return argv
    return await asyncio.get_running_loop().run_in_executor(INSTALL_POOL, ensure)


def claude_config_intents(options: ClaudeOptions) -> list[ConfigIntent]:
    """Map the Claude create-session options onto ACP config values."""
    intents: list[ConfigIntent] = []
    if options.plan_mode:
        # Plan mode restricts what the agent may do: never start without it.
        intents.append(ConfigIntent("plan", category="mode", required=True))
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


def codex_mode(options: CodexOptions) -> str:
    """The codex-acp mode for the sandbox choice.

    The adapter's modes pair a sandbox with an approval policy. "Never ask"
    inside the workspace sandbox has no twin; it gets Workspace access, where
    a person (not a reviewer model) approves each step outside the sandbox.
    """
    if options.full_auto or (options.sandbox == "danger-full-access" and options.approval == "never"):
        return "agent-full-access"
    if options.sandbox == "read-only":
        return "read-only"
    # Also full access that should still ask: a person approves each escape.
    return "workspace-write"


def codex_config_intents(options: CodexOptions) -> list[ConfigIntent]:
    """Map the Codex create-session options onto ACP config values."""
    intents = [ConfigIntent(codex_mode(options), category="mode", required=True)]
    if options.model:
        intents.append(ConfigIntent(options.model, category="model"))
    if options.reasoning_effort:
        intents.append(ConfigIntent(options.reasoning_effort, category="thought_level"))
    return intents


def antigravity_config_intents(options: AntigravityOptions) -> list[ConfigIntent]:
    """Map the Antigravity create-session options onto ACP config values.

    The model is the name ``agy models`` prints (``Gemini 3.8 Flash (High)``);
    :func:`resolve_antigravity_config` matches it to the session's options.
    ``skip_permissions`` and ``sandbox`` are ``agy acp`` flags.
    """
    if not options.model:
        return []
    return [ConfigIntent(options.model, category="model")]


def copilot_config_intents(options: CopilotOptions) -> list[ConfigIntent]:
    """Map the Copilot create-session options onto ACP config values.

    ``allow_all`` goes through Copilot's own config option, not a launch
    flag: the chat then shows it, and the user can turn it off again.
    """
    if not options.allow_all:
        return []
    return [ConfigIntent("on", option_id=COPILOT_ALLOW_ALL_OPTION)]


# Longer than any model name; bounds the work on a client-sent value.
MODEL_NAME_MAX = 200


def _split_level(name: str) -> tuple[str, str] | None:
    """``"Gemini 3.8 Flash (High)"`` → ``("Gemini 3.8 Flash", "High")``."""
    if not name.endswith(")"):
        return None
    base, sep, level = name[:-1].rpartition("(")
    base, level = base.rstrip(), level.strip()
    if not sep or not base or not level or ")" in level:
        return None
    return base, level


def _option_values(options: list[dict[str, Any]], category: str) -> list[tuple[str, str]]:
    """``(value, name)`` pairs of the first select option of ``category``,
    the one :meth:`ChatSession._match_config` applies an intent to."""
    option = next(
        (o for o in options if o.get("category") == category and o.get("type") == "select"), None
    )
    return [(v["value"], v["name"]) for v in option["options"]] if option else []


def resolve_antigravity_config(
    result: Mapping[str, Any], intents: list[ConfigIntent]
) -> list[ConfigIntent]:
    """Split a model name with its thinking level into the ``model`` and
    ``thought_level`` options of an Antigravity session.

    ``agy`` names ``Gemini 3.8 Flash (High)`` (id ``gemini-3.8-flash-high``)
    one model, while its ACP options offer the model ``gemini-3.8-flash`` and
    the thinking level ``high``. Other intents pass through unchanged.
    """
    options = nz.config_options(result.get("configOptions"))
    models = _option_values(options, "model")
    levels = _option_values(options, "thought_level")
    if not models:
        return intents
    raw_models = result.get("models")
    available = raw_models.get("availableModels") if isinstance(raw_models, Mapping) else None
    out: list[ConfigIntent] = []
    for intent in intents:
        # An intent by option id (a Restart) already holds an option value.
        if intent.category != "model" or intent.option_id is not None or not isinstance(intent.value, str):
            out.append(intent)
            continue
        out.extend(_resolve_model(intent, models, levels, available))
    return out


def _resolve_model(
    intent: ConfigIntent,
    models: list[tuple[str, str]],
    levels: list[tuple[str, str]],
    available: Any,
) -> list[ConfigIntent]:
    wanted = str(intent.value).strip()
    if len(wanted) > MODEL_NAME_MAX:
        return [intent]
    for value, name in models:
        if wanted == value or wanted.casefold() == name.casefold():
            return [ConfigIntent(value, category="model", required=intent.required)]
    model_id: str | None = None
    for entry in available if isinstance(available, list) else []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("modelId"), str):
            continue
        name = entry.get("name")
        if wanted in (entry["modelId"], name) or (
            isinstance(name, str) and wanted.casefold() == name.casefold()
        ):
            model_id = entry["modelId"]
            break
    level_values = {value for value, _ in levels}
    if model_id is not None:
        for value, _ in models:
            if model_id == value:
                return [ConfigIntent(value, category="model", required=intent.required)]
            level = model_id[len(value) + 1:] if model_id.startswith(value + "-") else None
            if level in level_values:
                return [
                    ConfigIntent(value, category="model", required=intent.required),
                    ConfigIntent(level, category="thought_level"),
                ]
    split = _split_level(wanted)
    if split:
        base, level_name = split[0].casefold(), split[1].casefold()
        for value, name in models:
            if name.casefold() != base:
                continue
            resolved = [ConfigIntent(value, category="model", required=intent.required)]
            for level_value, level_label in levels:
                if level_name in (level_value.casefold(), level_label.casefold()):
                    resolved.append(ConfigIntent(level_value, category="thought_level"))
                    break
            return resolved
    return [intent]


def build_chat_agents(
    launchers: Mapping[str, Any],
    *,
    test_mode: bool = False,
    command_overrides: Mapping[str, str | None] | None = None,
    adapters_dir: Path | None = None,
    antigravity_app_data_dir: Path | None = None,
) -> dict[str, ChatAgentSpec]:
    """The chat-capable agents of this host, keyed by session kind.

    ``test_mode`` (``MOBAIDE_ENV=test``) runs the scripted fake agent instead
    of the real one unless ``MOBAIDE_ACP_REAL=1``. ``adapters_dir`` holds the
    pinned adapter installs; without it only an adapter on PATH can run.
    """
    overrides = command_overrides or {}
    specs: dict[str, ChatAgentSpec] = {}
    for kind, spec_type in (("claude", ClaudeChatSpec), ("codex", CodexChatSpec)):
        launcher = launchers.get(kind)
        if launcher is None:
            continue
        specs[kind] = spec_type(
            launcher,
            command_override=overrides.get(kind),
            test_mode=test_mode,
            adapters_dir=adapters_dir,
        )
    agy_launcher = launchers.get("antigravity")
    if agy_launcher is not None:
        specs["antigravity"] = AntigravityChatSpec(
            agy_launcher,
            command_override=overrides.get("antigravity"),
            test_mode=test_mode,
            app_data_dir=antigravity_app_data_dir,
        )
    opencode_launcher = launchers.get("opencode")
    if opencode_launcher is not None:
        specs["opencode"] = OpenCodeChatSpec(
            opencode_launcher,
            command_override=overrides.get("opencode"),
            test_mode=test_mode,
        )
    copilot_launcher = launchers.get("copilot")
    if copilot_launcher is not None:
        specs["copilot"] = CopilotChatSpec(
            copilot_launcher,
            command_override=overrides.get("copilot"),
            test_mode=test_mode,
        )
    cursor_launcher = launchers.get("cursor")
    if cursor_launcher is not None:
        specs["cursor"] = CursorChatSpec(
            cursor_launcher,
            command_override=overrides.get("cursor"),
            test_mode=test_mode,
        )
    return specs


def build_chat_capabilities(specs: Mapping[str, ChatAgentSpec]) -> dict[str, dict[str, bool]]:
    """``capabilities.chat`` for ``GET /api/version``: one entry per agent
    kind that can start in the chat UI on this host."""
    caps: dict[str, dict[str, bool]] = {}
    for kind, spec in specs.items():
        try:
            if spec.available():
                caps[kind] = {
                    "resume": spec.supports_resume,
                    "live": bool(getattr(spec, "live_sessions", False)),
                }
        except Exception:
            continue
    return caps

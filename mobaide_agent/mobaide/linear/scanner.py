from __future__ import annotations

import asyncio
import contextvars
import re
import sys
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from mobaide.agent.logging import bind_request_context, get_logger, log_call
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def bind_request_context(**kwargs): pass

from mobaide.core import (
    AntigravityOptions,
    AutoScanFilter,
    ClaudeBinaryNotFoundError,
    ClaudeLauncher,
    ClaudeOptions,
    CliAuthRequiredError,
    CliBinaryNotFoundError,
    CodexOptions,
    CopilotOptions,
    CursorOptions,
    GitHubScanFilter,
    JunieOptions,
    OpenCodeOptions,
    PiOptions,
    WorkspaceConfig,
    WorkspacesStore,
    WorkspaceUpdatePatch,
    SessionConfig,
    SessionKind,
    SessionManager,
)
from mobaide.core.cli_launcher import CliLauncher, probe_cli_available
from mobaide.core.opencode_launcher import INITIAL_PROMPT_MAX
from mobaide.core.workspaces_store import safe_path_exists
from mobaide.github.gh import GhCli

from .client import LinearApiClient
from .config import AutoScanConfig
from .models import (
    ScanEvent,
    ScanIssuePayload,
    ScanPhasePayload,
    ScanSessionPayload,
    ScanStatus,
    ScanWorktreePayload,
    now_iso,
)
from .rate_limiter import LinearRateLimiter
from .sources import (
    AutoScanSourceError,
    GitHubIssueSource,
    IssueSource,
    LinearIssueSource,
    ScanIssue,
)
from .workitems import (
    ActiveWorkItem,
    ActiveWorkItemsStore,
    active_workitems_path,
    now_iso_utc,
)
from .worktree import WorktreeCreationError, WorktreeManager, convert_to_worktree_slug

logger = get_logger(__name__)


class AutoScanAlreadyRunning(Exception):
    """Raised when start() is called for a workspace that already has an active scan."""


class AutoScanCapacityReached(Exception):
    """Raised when the maximum concurrent-scans cap has been hit."""


AUTO_SCAN_CLIS = (
    "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "junie", "pi",
)

CLI_DISPLAY_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
    "opencode": "OpenCode",
    "copilot": "GitHub Copilot",
    "cursor": "Cursor",
    "junie": "Junie",
    "pi": "Pi",
}

_GITHUB_DEFAULT_PROMPT = (
    "Resolve the GitHub issue below. Read it in full with `gh issue view` "
    "first, then analyze it deeply and implement the fix."
)
_DESCRIPTION_MAX = 4000
_CUT_MARK = "\n[…]"
# Windows takes at most 32,767 UTF-16 units per command line; quote escaping
# and surrogate pairs grow the prompt there, and the path and flags need room.
_WIN_PROMPT_MAX = 24_000
# Control characters but line feed and tab: an agent that echoes its prompt
# would pass escape sequences from the issue text on to the apps' terminals.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class AutoScanStart:
    """What a client chose for one scan start, beyond the Claude options.

    ``source`` ``None`` marks an older client: it scans Linear with the stored
    filter (or the legacy defaults) and runs Claude Code. Filters and the
    Linear key, when present, are saved on the workspace before the scan
    starts, and so are ``source`` and ``cli``. ``initial_prompt`` ``None``
    keeps the saved prompt; ``""`` clears it.
    """

    source: str | None = None
    cli: str | None = None
    auto_approve: bool = True
    initial_prompt: str | None = None
    linear_filter: AutoScanFilter | None = None
    github_filter: GitHubScanFilter | None = None
    linear_api_key: str | None = None


def _agent_options(cli: str, auto_approve: bool, prompt: str):
    """The launch options of a non-Claude agent for one issue.

    ``auto_approve`` maps onto the agent's own "run without asking" switch;
    Pi has none (it never asks).
    """
    if cli == "codex":
        return CodexOptions(full_auto=auto_approve, initial_prompt=prompt)
    if cli == "antigravity":
        return AntigravityOptions(skip_permissions=auto_approve, initial_prompt=prompt)
    if cli == "opencode":
        return OpenCodeOptions(auto_approve=auto_approve, initial_prompt=prompt)
    if cli == "copilot":
        return CopilotOptions(allow_all=auto_approve, initial_prompt=prompt)
    if cli == "cursor":
        return CursorOptions(force=auto_approve, initial_prompt=prompt)
    if cli == "junie":
        return JunieOptions(brave=auto_approve, initial_prompt=prompt)
    if cli == "pi":
        return PiOptions(initial_prompt=prompt)
    raise ValueError(f"unknown agent kind {cli!r}")


def _through_cmd_shim(launcher: CliLauncher) -> bool:
    """Whether the agent runs through a Windows ``.cmd``/``.bat`` shim, so
    ``cmd.exe`` parses its command line."""
    if sys.platform != "win32":
        return False
    try:
        return launcher.resolve_binary().lower().endswith((".cmd", ".bat"))
    except CliBinaryNotFoundError:
        return False


# Characters ``cmd.exe`` acts on even inside a quoted argument (``%``, ``!``),
# or once a ``"`` in the text has flipped its quote state.
_CMD_SPECIAL = str.maketrans({'"': "'", "%": " ", "!": " ", "^": " ", "&": "+",
                              "|": " ", "<": " ", ">": " "})
# cmd.exe takes at most 8,191 characters per command line; this leaves room
# for the agent's path and flags.
_CMD_PROMPT_MAX = 6000


def _cmd_safe_prompt(prompt: str) -> str:
    """The prompt with nothing ``cmd.exe`` would interpret, short enough for it.

    The issue text comes from whoever wrote the issue, so through a shim it
    must not be able to end the agent's command line and start another one.
    Line breaks would end it too.
    """
    return " ".join(prompt.translate(_CMD_SPECIAL).split())[:_CMD_PROMPT_MAX]


class AutoScanInstance:
    """One Auto Scan polling loop bound to a single workspace.

    Snapshot semantics:

    * ``path``, ``linear_api_key``, ``conda_env``, ``default_flags``, the
      source, the agent and the prompt are captured at construction time —
      mid-scan mutations do NOT affect the running loop. Stop+start the scan
      to pick those up.
    * The source's filter is re-read from the injected ``WorkspacesStore`` at
      the top of each cycle, so filter edits apply on the next iteration
      (within at most ``scan_interval_seconds``) without needing a restart.

    ``source`` defaults to a Linear source over ``client``; ``cli`` names the
    agent each issue runs (``launchers`` maps it to its launcher), Claude
    Code by default.
    """

    def __init__(
        self,
        *,
        workspace_name: str,
        workspace_config: WorkspaceConfig,
        config: AutoScanConfig,
        session_manager: SessionManager,
        launcher: ClaudeLauncher,
        client: LinearApiClient | None = None,
        worktree_mgr: WorktreeManager,
        workspaces_store: WorkspacesStore,
        event_bus=None,
        client_options: ClaudeOptions | None = None,
        source: IssueSource | None = None,
        cli: str = "claude",
        launchers: Mapping[str, CliLauncher] | None = None,
        auto_approve: bool = True,
        initial_prompt: str | None = None,
    ) -> None:
        if client_options is not None and not isinstance(
            client_options, ClaudeOptions
        ):
            raise TypeError(  # type: ignore[unreachable]
                "client_options must be a ClaudeOptions instance or None, "
                f"got {type(client_options).__name__}"
            )
        self._workspace_name = workspace_name
        self._workspace_config = workspace_config
        self._config = config
        self._session_manager = session_manager
        self._launcher = launcher
        self._worktree_mgr = worktree_mgr
        self._workspaces_store = workspaces_store
        self._event_bus = event_bus
        self._client_options = client_options
        if source is None:
            if client is None:
                raise TypeError("AutoScanInstance needs a source or a Linear client")
            # Pre-seeded from the construction-time snapshot so the first
            # cycle has a valid filter even if the store ever reports the
            # workspace as missing; refreshed at the top of each cycle.
            source = LinearIssueSource(client, workspace_config.auto_scan_filter)
        self._source: IssueSource = source
        self._cli = cli
        self._launchers: Mapping[str, CliLauncher] = launchers or {"claude": launcher}
        self._auto_approve = auto_approve
        self._prompt = self._resolve_prompt(initial_prompt)

        # Per-workspace persistent record of issues whose worktrees have been
        # created by auto-scan. The scanner consults this file AFTER the
        # processing loop each cycle to detect issues that reached the
        # configured Done state and tear down their worktrees.
        #
        # It is intentionally NOT used as a dedup gate for the processing
        # loop — the source of truth for "needs processing" is the Linear
        # query filter (state.name in filter.state_names AND
        # labels.name in filter.label_names). The processing step itself
        # transitions state and removes the configured removal_label, which
        # naturally prevents the issue from reappearing unless the user
        # explicitly resets it in Linear.
        self._workitems_store = ActiveWorkItemsStore(
            active_workitems_path(config.state_dir, workspace_name)
        )
        # Cache of ``len(workitems_store)`` kept in sync by every add/remove
        # call in this class. Primed lazily from disk on first read to cover
        # the case where the store was populated in a previous process.
        self._active_workitem_count: int = self._workitems_store.peek_count()
        self._status = ScanStatus.IDLE
        self._poll_task: asyncio.Task[None] | None = None
        self._last_scan_time: float | None = None
        self._started_at: float = time.time()
        self._last_activity_at: float = self._started_at
        self._event_subscribers: set[asyncio.Queue[ScanEvent]] = set()
        self._recent_events: list[ScanEvent] = []
        self._max_recent_events = 500

        self._seq: int = 0
        self._cycle_number: int = 0
        self._next_scan_at: datetime | None = None
        self._run_now_event: asyncio.Event = asyncio.Event()

    @property
    def project_name(self) -> str:
        return self._workspace_name

    @property
    def status(self) -> ScanStatus:
        return self._status

    @property
    def processed_count(self) -> int:
        """Number of currently tracked active work items for this workspace.

        Name kept for DTO compatibility with the Android client, which
        reads ``processed_count`` from the auto-scan status payload. The
        value is updated in-memory at every add/remove to avoid a
        synchronous disk read on the status-rendering hot path.
        """
        return self._active_workitem_count

    @property
    def current_seq(self) -> int:
        return self._seq

    @property
    def recent_events(self) -> list[ScanEvent]:
        return list(self._recent_events)

    async def list_workitems(self) -> list[ActiveWorkItem]:
        """Return the persisted active work items for this scan instance.

        The git management screen consults this list (across all running
        scans) to refuse deletes of worktrees that auto-scan still owns.
        """
        return await self._workitems_store.read()

    @property
    def source_kind(self) -> str:
        return self._source.kind

    @property
    def cli(self) -> str:
        return self._cli

    def _resolve_prompt(self, initial_prompt: str | None) -> str:
        """The instruction every issue's prompt starts with.

        The client's prompt wins, then this workspace's saved prompt
        (construction-time snapshot, no extra I/O), then the source's default.
        A newer client's explicit ``""`` skips the saved prompt it is clearing.
        """
        if initial_prompt:
            return initial_prompt
        if initial_prompt is not None:
            return self._default_prompt()
        base = self._client_options
        if base is not None and base.initial_prompt:
            return base.initial_prompt
        if self._workspace_config.auto_scan_prompt:
            return self._workspace_config.auto_scan_prompt
        return self._default_prompt()

    def _default_prompt(self) -> str:
        if self._source.kind == "github":
            return _GITHUB_DEFAULT_PROMPT
        return self._config.auto_scan_prompt or ""

    def _issue_prompt(self, issue: ScanIssue, limit: int = INITIAL_PROMPT_MAX) -> str:
        """The first message of an issue's session: the instruction, then the issue.

        The issue text is included because only Claude Code gets the Linear
        MCP server, and every agent should know its issue without a lookup.
        The message is one argv element of at most ``limit`` characters; a
        description cut to fit ends with ``[…]`` so the agent knows text is
        missing.
        """
        title = _CONTROL_CHARS.sub("", issue.title)
        head = [self._prompt, "", f"{self._source.label} issue {issue.identifier}: {title}"]
        if issue.url:
            head.append(issue.url)
        head_text = "\n".join(head).strip()
        description = _CONTROL_CHARS.sub("", (issue.description or "").replace("\r\n", "\n")).strip()
        if not description:
            return head_text[:limit]
        # A GitHub agent reads the rest with ``gh issue view``; a Linear one
        # cannot, so only the prompt limit cuts a Linear description.
        if self._source.kind == "github" and len(description) > _DESCRIPTION_MAX:
            description = description[:_DESCRIPTION_MAX].rstrip() + _CUT_MARK
        room = limit - len(head_text) - 2
        if len(description) > room:
            description = description[: max(room - len(_CUT_MARK), 0)].rstrip() + _CUT_MARK
        return f"{head_text}\n\n{description}"[:limit]

    async def start(self) -> None:
        who = await self._source.connect()
        logger.info(
            "%s authenticated as %s for workspace %s",
            self._source.label,
            who,
            self._workspace_name,
        )

        self._status = ScanStatus.SCANNING
        self._started_at = time.time()
        self._last_activity_at = self._started_at
        # Run the poll loop in a fresh contextvars.Context so it does NOT
        # inherit per-request context (request_id, method, path, client_ip)
        # from whichever HTTP handler happened to call start(). Without this,
        # every structlog entry from the background loop would be tagged
        # with the request_id of the /api/autoscan/start call that spawned
        # it, making debugging run-now cycles very confusing.
        self._poll_task = asyncio.create_task(
            self._poll_loop(),
            name=f"autoscan-poll[{self._workspace_name}]",
            context=contextvars.Context(),
        )
        logger.info(
            "Auto Scan started for workspace '%s'. Interval: %ds",
            self._workspace_name,
            self._config.scan_interval_seconds,
        )

    async def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        self._status = ScanStatus.STOPPED
        self._next_scan_at = None
        self._emit_phase("scan_stopped")
        logger.info("Auto Scan stopped for workspace '%s'", self._workspace_name)

    def request_immediate_scan(self) -> None:
        """Wake the polling loop to run a scan cycle immediately.

        Reanchors ``next_scan_at`` to "now" and republishes status so
        the Android client's countdown can reset synchronously without
        having to wait for the next poll cycle to emit a phase event.

        Re-processing of previously-seen issues is now driven purely by
        the configured Linear query filters (per-workspace trigger states
        and labels) — there is no in-memory dedup set to clear.
        """
        self._run_now_event.set()
        self._next_scan_at = datetime.now(timezone.utc)
        self._publish_status()

    async def _poll_loop(self) -> None:
        try:
            while True:
                self._cycle_number += 1
                try:
                    await self._scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Scan cycle failed", exc_info=True)
                    self._status = ScanStatus.ERROR
                    self._emit(
                        "log",
                        {"line": f"Scan cycle failed: {exc}", "level": "error", "ansi": False},
                    )
                else:
                    self._status = ScanStatus.SCANNING

                interval = self._config.scan_interval_seconds
                self._next_scan_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
                self._publish_status()

                try:
                    await asyncio.wait_for(self._run_now_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self._run_now_event.clear()
        except asyncio.CancelledError:
            return

    def _refresh_filter(self) -> None:
        """Re-read the workspace's filter from the store, fall back to last-known.

        Decoupling the filter from the construction-time snapshot is
        intentional: edits propagate via ``WorkspacesStore`` and take effect
        on the next cycle without a manual stop+start. If the workspace has
        been deleted concurrently, the source keeps the last-known filter to
        avoid spuriously dropping in-flight issues — the next cycle will fail
        at the issue query anyway and the manager will surface a sensible
        error.
        """
        self._source.refresh(self._workspaces_store.get(self._workspace_name))

    async def _scan_once(self) -> None:
        self._last_scan_time = time.time()
        self._last_activity_at = self._last_scan_time
        self._refresh_filter()
        logger.info(
            "autoscan.scan_started workspace=%s source=%s %s",
            self._workspace_name, self._source.kind, self._source.describe(),
        )
        self._emit_phase("scan_started")

        issues = await self._source.fetch()

        processed_count = 0
        if issues:
            logger.info("Found %d matching issue(s) to process", len(issues))
            for issue in issues:
                try:
                    await self._process_issue(issue)
                except ClaudeBinaryNotFoundError:
                    logger.error(
                        "autoscan.aborting_cycle reason=claude_cli_missing workspace=%s",
                        self._workspace_name,
                    )
                    self._emit_error(
                        "Aborting scan cycle: Claude CLI not found "
                        "on PATH (set MOBAIDE_CLAUDE_BIN or install "
                        "claude-code)."
                    )
                    break
                except CliBinaryNotFoundError:
                    logger.error(
                        "autoscan.aborting_cycle reason=cli_missing cli=%s workspace=%s",
                        self._cli, self._workspace_name,
                    )
                    self._emit_error(
                        f"Aborting scan cycle: {self._cli_name} is not installed on the PC."
                    )
                    break
                except CliAuthRequiredError as exc:
                    logger.error(
                        "autoscan.aborting_cycle reason=cli_auth cli=%s workspace=%s",
                        self._cli, self._workspace_name,
                    )
                    self._emit_error(f"Aborting scan cycle: {exc}")
                    break
                processed_count += 1
        else:
            logger.info(
                "autoscan.no_matches workspace=%s source=%s %s",
                self._workspace_name, self._source.kind, self._source.describe(),
            )

        # Run every cycle — items added in prior cycles may have reached Done.
        if self._config.auto_remove_worktrees:
            try:
                await self._cleanup_completed_worktrees()
            except Exception:
                logger.exception("autoscan.cleanup.failed")

        self._emit_phase("scan_completed", issues_processed=processed_count)

    @property
    def _cli_name(self) -> str:
        return CLI_DISPLAY_NAMES.get(self._cli, self._cli)

    def _emit_error(self, line: str) -> None:
        self._emit("log", {"line": line, "level": "error", "ansi": False})

    def _emit_step(
        self, issue: ScanIssue, step: str, status: str, error: str | None,
    ) -> None:
        self._emit_issue(issue, step, status, error=error)

    @log_call(name="autoscan.process_issue", log_args=("issue",))
    async def _process_issue(self, issue: ScanIssue) -> None:
        bind_request_context(issue_identifier=issue.identifier)
        logger.info("Processing: %s - %s", issue.identifier, issue.title)

        launcher = self._launchers.get(self._cli, self._launcher)
        # Before the claim: a claimed issue no longer matches the filter, so
        # with no agent able to run it, it would never be picked up again.
        try:
            launcher.resolve_binary()
            ensure_auth = getattr(launcher, "ensure_auth", None)
            if ensure_auth is not None:
                await ensure_auth(self._launch_workspace())
        except CliBinaryNotFoundError:
            error = f"{self._cli_name} is not installed"
            self._emit_issue(issue, "session_spawn", "failed", error=error)
            raise
        except CliAuthRequiredError:
            error = f"{self._cli_name} is not signed in"
            self._emit_issue(issue, "session_spawn", "failed", error=error)
            raise

        await self._source.claim(issue, self._emit_step)

        slug = convert_to_worktree_slug(issue.slug_seed, issue.title)
        session_id = uuid.uuid4().hex[:12]
        through_shim = _through_cmd_shim(launcher)
        if through_shim:
            limit = _CMD_PROMPT_MAX
        elif sys.platform == "win32":
            limit = _WIN_PROMPT_MAX
        else:
            limit = INITIAL_PROMPT_MAX
        prompt = self._issue_prompt(issue, limit)
        if through_shim:
            prompt = _cmd_safe_prompt(prompt)
        if self._cli == "claude":
            await self._launch_claude(issue, slug, session_id, prompt)
        else:
            await self._launch_agent(issue, slug, session_id, prompt)
        self._last_activity_at = time.time()

    def _launch_workspace(self) -> WorkspaceConfig:
        """The workspace an issue's agent is launched against.

        A GitHub scan has no use for the workspace's Linear key, and with it
        the Claude launcher would put the key in the worktree's ``.mcp.json``
        and the session env, next to an agent reading third-party issue text.
        """
        if self._source.kind == "github" and self._workspace_config.linear_api_key:
            return replace(self._workspace_config, linear_api_key=None)
        return self._workspace_config

    async def _track_workitem(self, issue: ScanIssue, slug: str, cwd: str) -> None:
        workspace = self._workspace_config
        if not self._config.auto_remove_worktrees or cwd == workspace.path:
            return
        try:
            added = await self._workitems_store.add(
                ActiveWorkItem(
                    id=issue.identifier,
                    uuid=issue.key,
                    slug=slug,
                    worktree_path=str(cwd),
                    project_root=workspace.path,
                    added_at=now_iso_utc(),
                    source=self._source.kind,
                )
            )
            if added:
                self._active_workitem_count += 1
        except Exception:
            logger.exception(
                "autoscan.workitem.persist_failed id=%s", issue.identifier,
            )

    async def _spawn(
        self, issue: ScanIssue, session_id: str, spec, kind: SessionKind,
    ) -> bool:
        self._emit_issue(issue, "session_spawn", "started")
        try:
            config = SessionConfig(
                session_id=session_id,
                command=spec.argv,
                cwd=spec.cwd,
                env=spec.env,
                kind=kind,
            )
            session = await self._session_manager.create_session(config)
        except Exception as exc:
            self._emit_issue(issue, "session_spawn", "failed", error=str(exc))
            logger.error(
                "Failed to start session for %s", issue.identifier, exc_info=True
            )
            return False
        pid = getattr(session, "pid", None)
        self._emit_issue(issue, "session_spawn", "ok")
        self._emit("session", asdict(ScanSessionPayload(
            identifier=issue.identifier, session_id=session_id, pid=pid,
        )))
        logger.info("Session %s started for %s", session_id, issue.identifier)
        return True

    async def _launch_claude(
        self, issue: ScanIssue, slug: str, session_id: str, prompt: str,
    ) -> None:
        workspace = self._launch_workspace()
        base = self._client_options
        opts = replace(
            base if base is not None else ClaudeOptions(),
            identifier=issue.slug_seed,
            title=issue.title,
            initial_prompt=prompt,
            # Worktree isolation stays forced per issue; skip_permissions is
            # the client's choice — the build below carries a session_id, so
            # attention hooks install and a non-bypass session raises
            # awaiting_permission instead of hanging unattended.
            worktree=True,
        )

        self._emit_issue(issue, "worktree_create", "started")
        try:
            spec = await self._launcher.build(workspace, opts, session_id=session_id)
        except WorktreeCreationError as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            logger.error(
                "autoscan.worktree_create_failed id=%s: %s",
                issue.identifier, exc.stderr,
            )
            return
        except ClaudeBinaryNotFoundError:
            # ``launcher.build`` resolves the binary BEFORE creating the
            # worktree, so no orphan directory is on disk by the time we
            # land here. Emit a terminal ``worktree_create:failed`` so the
            # event stream's state machine has no dangling ``started``,
            # then ``session_spawn:failed`` for consumers that key off
            # that step, and re-raise so the outer ``_scan_once`` aborts.
            self._emit_issue(
                issue, "worktree_create", "failed", error="Claude CLI not found",
            )
            self._emit_issue(
                issue, "session_spawn", "failed", error="Claude CLI not found",
            )
            logger.error(
                "autoscan.claude_cli_missing workspace=%s", self._workspace_name,
            )
            self._last_activity_at = time.time()
            raise
        except Exception as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            logger.error(
                "autoscan.launcher_build_failed id=%s", issue.identifier,
                exc_info=True,
            )
            return
        self._emit_issue(issue, "worktree_create", "ok")
        self._emit("worktree", asdict(ScanWorktreePayload(
            identifier=issue.identifier, path=str(spec.cwd),
        )))

        if not opts.skip_permissions and not any(
            f.endswith(f"{session_id}.json") for f in spec.created_files
        ):
            # Hookless launcher (or a failed hooks write): nothing will ever
            # notify anyone about a permission prompt, so this non-bypass
            # session can stall silently.
            logger.warning(
                "autoscan.no_attention_hooks session=%s id=%s — non-bypass "
                "session may stall at a permission prompt with no notification",
                session_id, issue.identifier,
            )

        await self._track_workitem(issue, slug, str(spec.cwd))
        if not await self._spawn(issue, session_id, spec, SessionKind.AUTO_SCAN):
            # The SessionEnd hook that normally deletes the hooks settings
            # file never fires for a session that failed to spawn.
            self._launcher.discard_session_hooks(session_id)

    async def _launch_agent(
        self, issue: ScanIssue, slug: str, session_id: str, prompt: str,
    ) -> None:
        """Run a non-Claude agent on the issue in the same per-issue worktree
        Claude would get (``<branch_prefix>/<slug>``)."""
        workspace = self._launch_workspace()
        launcher = self._launchers[self._cli]
        self._emit_issue(issue, "worktree_create", "started")
        try:
            if safe_path_exists(Path(workspace.path) / ".git"):
                result = await self._worktree_mgr.create_worktree(slug, workspace.path)
                cwd = result.path
            else:
                cwd = workspace.path
        except WorktreeCreationError as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            logger.error(
                "autoscan.worktree_create_failed id=%s: %s",
                issue.identifier, exc.stderr,
            )
            return
        except Exception as exc:
            self._emit_issue(issue, "worktree_create", "failed", error=str(exc))
            logger.error(
                "autoscan.worktree_create_failed id=%s", issue.identifier,
                exc_info=True,
            )
            return
        self._emit_issue(issue, "worktree_create", "ok")
        self._emit("worktree", asdict(ScanWorktreePayload(
            identifier=issue.identifier, path=str(cwd),
        )))
        await self._track_workitem(issue, slug, str(cwd))

        try:
            options = _agent_options(self._cli, self._auto_approve, prompt)
            spec = await launcher.build(
                replace(workspace, path=str(cwd)), options, session_id=session_id,
            )
        except CliAuthRequiredError as exc:
            self._emit_issue(issue, "session_spawn", "failed", error=str(exc))
            raise
        except Exception as exc:
            self._emit_issue(issue, "session_spawn", "failed", error=str(exc))
            logger.error(
                "autoscan.launcher_build_failed id=%s cli=%s",
                issue.identifier, self._cli, exc_info=True,
            )
            return
        await self._spawn(issue, session_id, spec, SessionKind(self._cli))

    async def _cleanup_completed_worktrees(self) -> None:
        """Remove worktrees for tracked issues that are done in their tracker.

        Mirrors the PowerShell ``Remove-CompletedWorktrees`` function: for
        each item in ``active_workitems.json`` belonging to the current
        workspace and source, ask the source whether the issue is done
        (Linear: the configured ``done_state``; GitHub: closed); if so,
        remove the git worktree and drop the entry from the store. Items for
        other workspaces or another source are preserved (a later scan of
        that source checks them).
        """
        workspace = self._workspace_config
        workspace_root = workspace.path

        items = await self._workitems_store.read()
        tracked = [
            i for i in items
            if i.project_root == workspace_root and i.source == self._source.kind
        ]
        if not tracked:
            logger.info(
                "autoscan.cleanup.start workspace=%s tracked=0 — nothing to check",
                self._workspace_name,
            )
            return

        logger.info(
            "autoscan.cleanup.start workspace=%s tracked=%d",
            self._workspace_name, len(tracked),
        )

        removed = 0
        kept = 0
        for item in tracked:
            try:
                done = await self._source.is_done(item)
            except Exception as exc:
                logger.warning(
                    "autoscan.cleanup.query_failed id=%s error=%s — keeping for retry",
                    item.id, exc,
                )
                kept += 1
                continue

            if done is None:
                logger.warning(
                    "autoscan.cleanup.query_failed id=%s — %s returned no state; keeping",
                    item.id, self._source.label,
                )
                kept += 1
                continue

            if not done:
                logger.info("autoscan.cleanup.keep id=%s", item.id)
                kept += 1
                continue

            logger.info(
                "autoscan.cleanup.remove id=%s worktree=%s",
                item.id, item.worktree_path,
            )
            try:
                # Anyone who opened a GitHub issue can close it, so a GitHub
                # scan never forces away uncommitted work; git keeps such a
                # worktree and the item stays for a later cycle.
                ok = await self._worktree_mgr.cleanup_worktree(
                    item.worktree_path, workspace_root,
                    force=self._source.kind != "github",
                )
            except Exception as exc:
                logger.warning(
                    "autoscan.cleanup.remove_failed id=%s error=%s — keeping for retry",
                    item.id, exc,
                )
                kept += 1
                continue
            if not ok:
                logger.warning(
                    "autoscan.cleanup.remove_failed id=%s — keeping for retry", item.id,
                )
                kept += 1
                continue
            if await self._workitems_store.remove(item.id):
                self._active_workitem_count = max(
                    0, self._active_workitem_count - 1,
                )
            removed += 1

        logger.info(
            "autoscan.cleanup.summary workspace=%s removed=%d remaining=%d",
            self._workspace_name, removed, kept,
        )

    def _emit(self, type_: str, payload: dict) -> ScanEvent:
        self._seq += 1
        evt = ScanEvent(
            seq=self._seq,
            ts=now_iso(),
            type=type_,
            payload=payload,
        )
        self._recent_events.append(evt)
        if len(self._recent_events) > self._max_recent_events:
            self._recent_events = self._recent_events[-self._max_recent_events:]

        for q in list(self._event_subscribers):
            with suppress(asyncio.QueueFull):
                q.put_nowait(evt)

        if self._event_bus is not None:
            self._event_bus.publish(
                "autoscan.event",
                {
                    "project_name": self._workspace_name,
                    "event": evt.to_dict(),
                },
            )
            self._event_bus.publish("autoscan.status", self.to_dto())
        return evt

    def _emit_phase(self, phase: str, *, issues_processed: int = 0) -> None:
        next_at = self._next_scan_at.isoformat() if self._next_scan_at else None
        self._emit(
            "phase",
            asdict(
                ScanPhasePayload(
                    phase=phase,
                    cycle_number=self._cycle_number,
                    next_scan_at=next_at,
                    issues_processed=issues_processed,
                )
            ),
        )

    def _emit_issue(
        self,
        issue: ScanIssue,
        step: str,
        status: str,
        error: str | None = None,
    ) -> None:
        self._emit(
            "issue",
            asdict(
                ScanIssuePayload(
                    identifier=issue.identifier,
                    title=issue.title,
                    step=step,
                    status=status,
                    error=error,
                )
            ),
        )

    def _publish_status(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish("autoscan.status", self.to_dto())

    def subscribe_events(self) -> asyncio.Queue[ScanEvent]:
        q: asyncio.Queue[ScanEvent] = asyncio.Queue(maxsize=200)
        self._event_subscribers.add(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue[ScanEvent]) -> None:
        self._event_subscribers.discard(q)

    def to_dto(self) -> dict:
        return {
            "project_name": self._workspace_name,
            "status": self._status.value,
            "started_at": self._started_at,
            "last_activity_at": self._last_activity_at,
            "processed_count": self.processed_count,
            "last_scan_time": self._last_scan_time,
            "scan_interval_seconds": self._config.scan_interval_seconds,
            "next_scan_at": self._next_scan_at.isoformat() if self._next_scan_at else None,
            "cycle_number": self._cycle_number,
            "current_seq": self._seq,
            "source": self._source.kind,
            "cli": self._cli,
        }

    def get_status(self) -> dict:
        dto = self.to_dto()
        dto["recent_events"] = [e.to_dict() for e in self._recent_events[-50:]]
        return dto


class AutoScanSessionManager:
    """Manages multiple concurrent AutoScanInstance objects.

    The manager reads workspaces live from the injected
    :class:`WorkspacesStore`, so workspaces added via the REST CRUD API are
    picked up without a restart. ``cli_launchers`` holds the agents a scan
    can run (Claude Code only when absent); ``gh`` reaches GitHub.
    """

    def __init__(
        self,
        config: AutoScanConfig,
        session_manager: SessionManager,
        workspaces_store: WorkspacesStore,
        launcher: ClaudeLauncher | None = None,
        event_bus=None,
        rate_limiter: LinearRateLimiter | None = None,
        max_scans: int = 5,
        worktrees_dir: str | None = None,
        cli_launchers: Mapping[str, CliLauncher] | None = None,
        gh: GhCli | None = None,
        worktree_mgr: WorktreeManager | None = None,
    ) -> None:
        self._config = config
        self._worktrees_dir = worktrees_dir or config.resolved_worktrees_dir()
        self._session_manager = session_manager
        self._workspaces_store = workspaces_store
        self._launcher = launcher or ClaudeLauncher()
        self._launchers: dict[str, CliLauncher] = {"claude": self._launcher}
        if cli_launchers:
            self._launchers.update(
                (cli, l) for cli, l in cli_launchers.items() if cli in AUTO_SCAN_CLIS
            )
        self._gh = gh or GhCli()
        self._shared_worktree_mgr = worktree_mgr
        self._event_bus = event_bus
        self._rate_limiter = rate_limiter or LinearRateLimiter()
        self._max_scans = max_scans
        self._scans: dict[str, AutoScanInstance] = {}
        self._lock = asyncio.Lock()

    @property
    def max_scans(self) -> int:
        return self._max_scans

    @property
    def gh(self) -> GhCli:
        return self._gh

    @property
    def available_projects(self) -> list[str]:
        return self._workspaces_store.list_with_linear_key()

    def get(self, workspace_name: str) -> AutoScanInstance | None:
        return self._scans.get(workspace_name)

    def all_instances(self) -> list[AutoScanInstance]:
        return list(self._scans.values())

    async def list_all_workitems(self) -> dict[str, list[ActiveWorkItem]]:
        """Return ``{workspace_name: [work_items]}`` across every running scan."""
        result: dict[str, list[ActiveWorkItem]] = {}
        for inst in self._scans.values():
            result[inst.project_name] = await inst.list_workitems()
        return result

    def list(self) -> list[dict]:
        return [inst.to_dto() for inst in self._scans.values()]

    def list_response(self) -> dict:
        return {
            "scans": self.list(),
            "capacity": len(self._scans),
            "max_scans": self._max_scans,
            "available_projects": self.available_projects,
            "auto_scan_prompt": self._config.auto_scan_prompt,
        }

    async def start(
        self,
        workspace_name: str,
        *,
        client_options: ClaudeOptions | None = None,
        request: AutoScanStart | None = None,
    ) -> AutoScanInstance:
        """Start a scan of ``workspace_name``.

        Raises ``ValueError`` for an unknown workspace (404),
        ``AutoScanAlreadyRunning`` (409), ``AutoScanCapacityReached`` (429)
        and ``AutoScanSourceError`` when a choice is missing or the source or
        agent cannot be used (400). The client's choices are saved on the
        workspace even when the start then fails, so the wizard shows them
        again.
        """
        req = request or AutoScanStart()
        if self._workspaces_store.get(workspace_name) is None:
            raise ValueError(
                f"Workspace '{workspace_name}' not found in store. "
                f"Available: {self.available_projects}",
            )

        choices_saved = False
        try:
            async with self._lock:
                if workspace_name in self._scans:
                    raise AutoScanAlreadyRunning(
                        f"Auto scan for workspace '{workspace_name}' is already running"
                    )
                if len(self._scans) >= self._max_scans:
                    raise AutoScanCapacityReached(
                        f"Maximum {self._max_scans} concurrent auto scans reached"
                    )

                # Saved only once the scan is known to be startable here, so
                # a rejected start never edits a running scan's settings.
                choices_saved = self._save_choices(workspace_name, req)
                workspace = self._workspaces_store.get(workspace_name)
                if workspace is None:
                    raise ValueError(f"Workspace '{workspace_name}' not found in store.")

                cli = req.cli or "claude"
                source = self._build_source(workspace, req)
                await self._check_agent(cli, workspace)
                instance = AutoScanInstance(
                    workspace_name=workspace_name,
                    workspace_config=workspace,
                    config=self._config,
                    session_manager=self._session_manager,
                    launcher=self._launcher,
                    worktree_mgr=self._worktree_mgr(),
                    workspaces_store=self._workspaces_store,
                    event_bus=self._event_bus,
                    client_options=client_options,
                    source=source,
                    cli=cli,
                    launchers=self._launchers,
                    auto_approve=req.auto_approve,
                    initial_prompt=req.initial_prompt,
                )
                await instance.start()
                self._scans[workspace_name] = instance

                # Persist the applied prompt only after the scan has actually
                # started and been registered, so a rejected start (already-running
                # / at-capacity) or a failed instance.start() never rewrites the
                # workspace's saved prompt.
                if self._persist_applied_prompt(workspace_name, client_options, req):
                    choices_saved = True
        finally:
            # Emitted outside the lock so clients reconcile their cached
            # workspace list — and thus the wizard's pre-seeded choices.
            if choices_saved:
                self._publish_workspaces_changed()

        self._publish_list_changed()
        return instance

    def _worktree_mgr(self) -> WorktreeManager:
        if self._shared_worktree_mgr is not None:
            return self._shared_worktree_mgr
        prefix = self._config.branch_prefix or "feature/mobaide"
        return WorktreeManager(
            worktrees_dir=self._worktrees_dir,
            branch_prefix=prefix,
            main_branch=self._config.main_branch,
            legacy_worktree_base=self._config.worktree_base,
        )

    def _save_choices(self, workspace_name: str, req: AutoScanStart) -> bool:
        """Save the client's source, agent, filters and Linear key on the workspace."""
        patch = WorkspaceUpdatePatch(
            linear_api_key=req.linear_api_key or None,
            auto_scan_filter=req.linear_filter,
            github_scan_filter=req.github_filter,
            auto_scan_source=req.source,
            auto_scan_cli=(req.cli or "claude") if req.source is not None else None,
        )
        if patch == WorkspaceUpdatePatch():
            return False
        try:
            self._workspaces_store.update(workspace_name, patch)
        except KeyError as exc:
            raise ValueError(f"Workspace '{workspace_name}' not found in store.") from exc
        except OSError:
            logger.warning(
                "autoscan.choices_persist.failed workspace=%s",
                workspace_name, exc_info=True,
            )
            return False
        return True

    def _build_source(self, workspace: WorkspaceConfig, req: AutoScanStart) -> IssueSource:
        kind = req.source or "linear"
        if kind == "github":
            if workspace.github_scan_filter is None:
                raise AutoScanSourceError("Choose the GitHub labels to scan for.")
            return GitHubIssueSource(self._gh, workspace.path, workspace.github_scan_filter)
        if kind != "linear":
            raise AutoScanSourceError(f"Unknown issue source '{kind}'.")
        if not workspace.linear_api_key:
            raise AutoScanSourceError(
                f"Workspace '{workspace.name}' has no Linear API key configured.",
            )
        # An older client cannot choose a filter in its wizard: it scans
        # with the stored one, which falls back to the legacy defaults.
        if req.source is not None and not workspace.linear_filter_set:
            raise AutoScanSourceError("Choose the Linear states and labels to scan for.")
        client = LinearApiClient(
            api_key=workspace.linear_api_key,
            rate_limiter=self._rate_limiter,
        )
        return LinearIssueSource(client, workspace.auto_scan_filter)

    async def _check_agent(self, cli: str, workspace: WorkspaceConfig) -> None:
        launcher = self._launchers.get(cli)
        name = CLI_DISPLAY_NAMES.get(cli, cli)
        if launcher is None:
            raise AutoScanSourceError(f"{name} cannot run Auto Scan on this PC.")
        # Claude's absence surfaces per issue, as it always has.
        if cli == "claude":
            return
        if not probe_cli_available(launcher):
            raise AutoScanSourceError(f"{name} is not installed on the PC.")
        ensure_auth = getattr(launcher, "ensure_auth", None)
        if ensure_auth is not None:
            try:
                await ensure_auth(workspace)
            except CliAuthRequiredError as exc:
                raise AutoScanSourceError(f"{name} is not signed in on the PC. {exc}") from exc

    async def stop(self, workspace_name: str) -> None:
        async with self._lock:
            instance = self._scans.pop(workspace_name, None)
        if instance is None:
            raise KeyError(f"No active auto scan for workspace '{workspace_name}'")
        await instance.stop()
        self._publish_list_changed()

    async def shutdown(self) -> None:
        async with self._lock:
            instances = list(self._scans.values())
            self._scans.clear()
        if instances:
            await asyncio.gather(
                *(i.stop() for i in instances), return_exceptions=True
            )
        self._publish_list_changed()

    def _publish_list_changed(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(
                "autoscan.list.changed", self.list_response()
            )

    def _persist_applied_prompt(
        self,
        workspace_name: str,
        client_options: ClaudeOptions | None,
        req: AutoScanStart | None = None,
    ) -> bool:
        """Save the just-applied Auto Scan prompt onto the workspace record.

        Returns ``True`` when the store was written (so the caller emits
        ``projects.changed``). The top-level ``req.initial_prompt`` is the
        newer clients' field: text saves it, ``""`` clears it. Otherwise only
        a non-empty ``client_options.initial_prompt`` is saved: a start that
        omits the prompt or sends a blank one expresses no prompt intent, so
        the workspace's existing saved prompt is preserved rather than
        clobbered — e.g. a flags-only start that just toggles ``no_memory``
        must not wipe the saved prompt.
        """
        if req is not None and req.initial_prompt is not None:
            applied = req.initial_prompt
        elif client_options is not None and client_options.initial_prompt:
            applied = client_options.initial_prompt
        else:
            return False
        try:
            self._workspaces_store.update(
                workspace_name,
                WorkspaceUpdatePatch(auto_scan_prompt=applied),
            )
        except Exception:
            # Best-effort metadata save: the scan is already started and
            # registered by the time we get here, so a persistence failure —
            # the workspace vanished (KeyError) or a transient disk/permission
            # error on the store write — must NOT surface as a start failure.
            # Log and carry on; the prompt simply re-seeds on the next start.
            logger.warning(
                "autoscan.prompt_persist.failed workspace=%s",
                workspace_name, exc_info=True,
            )
            return False
        return True

    def _publish_workspaces_changed(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(
                "projects.changed",
                {"projects": self._workspaces_store.list_public()},
            )

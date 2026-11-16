"""The Herdr dashboard service: finds the herdr servers on this computer,
mirrors them, raises alerts, and opens panes as terminal sessions.

It attaches only. No call here starts or stops a herdr server, and a stopped
Mobaide agent leaves every pane running.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mobaide.core.program_status import ProgramState, ProgramStatus, ProgramStatusGate

try:
    from mobaide.agent.logging import audit_event
except Exception:  # pragma: no cover - logging package optional during isolated tests
    def audit_event(*args, **kwargs): pass

from . import errors
from .agents import AGENT_LABELS, AgentChoice
from .backend import AgentNotStarted, HerdrBackend, ServerGone, ServerLink
from .models import (
    TEXT_MAX_LINES,
    HerdrAttention,
    HerdrCloseRequest,
    HerdrCreateRequest,
    HerdrPane,
    HerdrRenameRequest,
    HerdrServer,
    HerdrSnapshot,
)
from .pane_session import HerdrPaneSession
from .transport import in_thread

logger = logging.getLogger(__name__)

DISCOVER_SECONDS = 5.0
# Changes that arrive together leave as one ``herdr.changed`` event.
PUBLISH_DEBOUNCE_SECONDS = 0.05
AGENT_CATALOG_SECONDS = 60.0
# A pane view that nobody watched for this long is dropped. Opening the pane
# again makes a new one.
PANE_SESSION_IDLE_SECONDS = 30 * 60
# Herdr reads the agent state off the screen and can call an agent finished
# while it only paused, so a ``done`` waits out a longer quiet window than a
# program's own report gets.
DONE_QUIET_SECONDS = 3.0

# How long a pane open waits for a server whose link just dropped.
REOPEN_WAIT_SECONDS = 4.0
# Discovery retries in that wait while the server is not found yet.
REOPEN_RETRY_SECONDS = 0.5

EVENT_CHANGED = "herdr.changed"
EVENT_ATTENTION = "herdr.attention"

_STATUS_TO_PROGRAM = {
    "working": ProgramStatus(ProgramState.WORKING),
    "blocked": ProgramStatus(ProgramState.BLOCKED, kind="permission"),
    "done": ProgramStatus(ProgramState.DONE),
    "failed": ProgramStatus(ProgramState.ERROR),
    "idle": ProgramStatus(ProgramState.IDLE),
}


class _PaneAlerts:
    """Alert state of one pane: the gate that times alerts and the reason
    that stands now."""

    def __init__(self, service: "HerdrService", server_id: str, key: str) -> None:
        self.attention: str | None = None
        self.seen: tuple[str | None, int | None] | None = None
        self.alive = True
        self.gate = ProgramStatusGate(
            on_alert=lambda reason: service._alert(server_id, key, self, reason),
            on_clear_permission=self.clear,
            is_running=lambda: self.alive,
            current_attention=lambda: self.attention,
            quiet_seconds=DONE_QUIET_SECONDS,
        )

    def clear(self) -> None:
        self.attention = None

    def dispose(self) -> None:
        self.alive = False
        self.gate.cancel()


class HerdrService:
    def __init__(
        self,
        *,
        publish: Callable[[str, dict[str, Any]], None],
        session_manager: Any,
        backends: Sequence[Callable[[Callable[[], list[AgentChoice]]], HerdrBackend]] = (),
        cli_launchers: Mapping[str, Any] | None = None,
        discover_seconds: float = DISCOVER_SECONDS,
    ) -> None:
        self._publish = publish
        self._session_manager = session_manager
        self._cli_launchers = cli_launchers or {}
        self._discover_seconds = discover_seconds
        self._agents: list[AgentChoice] = []
        self._agents_read_at: float | None = None
        # Each backend gets the catalog of agents this host can start.
        self._backends: list[HerdrBackend] = [make(self.agent_choices) for make in backends]
        self._links: dict[str, ServerLink] = {}
        self._link_tasks: dict[str, asyncio.Task[None]] = {}
        self._discover_task: asyncio.Task[None] | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._published: HerdrSnapshot | None = None
        self._alerts: dict[tuple[str, str], _PaneAlerts] = {}
        self._pane_sessions: dict[str, HerdrPaneSession] = {}
        self._open_lock = asyncio.Lock()
        # Releases of pane sessions that still run, by session id.
        self._releases: dict[str, asyncio.Future[Any]] = {}
        # Set on every change of a link; see ``_link_after_restart``.
        self._change_seen = asyncio.Event()
        # Loop time at which each server's link dropped, until a new one comes.
        self._lost_at: dict[str, float] = {}

    def agent_choices(self) -> list[AgentChoice]:
        return self._agents

    async def start(self) -> None:
        if self._discover_task is None:
            self._discover_task = asyncio.create_task(
                self._discover_loop(), name="herdr-discover"
            )

    async def shutdown(self) -> None:
        tasks = [t for t in (self._discover_task, *self._link_tasks.values()) if t is not None]
        self._discover_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        for alerts in self._alerts.values():
            alerts.dispose()
        self._alerts.clear()
        self._links.clear()
        self._link_tasks.clear()
        self._lost_at.clear()

    async def _discover_loop(self) -> None:
        while True:
            try:
                await self._refresh_agents()
                await self._discover_once()
                self._sweep_idle_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("herdr.discover_failed")
            await asyncio.sleep(self._discover_seconds)

    async def _discover_once(self) -> None:
        for backend in self._backends:
            found = await backend.discover(frozenset(self._links))
            for server_id, factory in found.items():
                # Checked again after the await: two passes can run at once.
                if server_id in self._links:
                    continue
                link = factory(self._changed)
                self._links[server_id] = link
                self._lost_at.pop(server_id, None)
                self._link_tasks[server_id] = asyncio.create_task(
                    self._run_link(link), name=f"herdr-link-{server_id}"
                )

    async def _refresh_agents(self) -> None:
        now = asyncio.get_running_loop().time()
        if (
            self._agents_read_at is not None
            and now - self._agents_read_at < AGENT_CATALOG_SECONDS
        ):
            return
        self._agents_read_at = now
        self._agents = await in_thread(_installed_agents, dict(self._cli_launchers))

    async def _run_link(self, link: ServerLink) -> None:
        try:
            await link.run()
        except ServerGone as exc:
            logger.info("herdr.server_gone server=%s reason=%s", link.server_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("herdr.link_failed server=%s", link.server_id)
        finally:
            try:
                await link.aclose()
            except Exception:
                logger.debug("herdr.link_close_failed", exc_info=True)
            if self._links.get(link.server_id) is link:
                self._links.pop(link.server_id, None)
                self._link_tasks.pop(link.server_id, None)
                self._lost_at[link.server_id] = asyncio.get_running_loop().time()
            for session in list(self._pane_sessions.values()):
                if session.server_id == link.server_id:
                    session.end()
            self._changed()

    def snapshot(self) -> HerdrSnapshot:
        servers = [
            server
            for server in (link.state() for link in self._links.values())
            if server is not None
        ]
        servers.sort(key=lambda s: (s.product, s.name != "default", s.name))
        return HerdrSnapshot(servers=servers)

    def publish_snapshot(self) -> None:
        """Send the current state again, for a client that just connected."""
        snapshot = self.snapshot()
        self._published = snapshot
        self._publish(EVENT_CHANGED, snapshot.model_dump(mode="json"))

    def _changed(self) -> None:
        self._change_seen.set()
        if self._flush_handle is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._flush_handle = loop.call_later(PUBLISH_DEBOUNCE_SECONDS, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        snapshot = self.snapshot()
        self._update_alerts(snapshot)
        for session_id, session in list(self._pane_sessions.items()):
            session.server_changed()
            if not session.is_alive():
                self._drop_session(session_id)
        if snapshot != self._published:
            self._published = snapshot
            self._publish(EVENT_CHANGED, snapshot.model_dump(mode="json"))

    def _update_alerts(self, snapshot: HerdrSnapshot) -> None:
        live: set[tuple[str, str]] = set()
        for server in snapshot.servers:
            link = self._links.get(server.server_id)
            if link is None:
                continue
            for pane in _panes(server):
                if pane.agent is None:
                    continue
                key = link.pane_key(pane.pane_id)
                if key is None:
                    continue
                ident = (server.server_id, key)
                live.add(ident)
                alerts = self._alerts.get(ident)
                state = (pane.status, pane.turn)
                if alerts is None:
                    # The state an agent has when Mobaide first sees it is not
                    # news: only a later change alerts.
                    alerts = self._alerts[ident] = _PaneAlerts(self, server.server_id, key)
                    alerts.seen = state
                    continue
                if state == alerts.seen:
                    continue
                seen_turn = alerts.seen[1] if alerts.seen is not None else None
                alerts.seen = state
                # A turn can run and end between two reads, so its ``working``
                # is never seen. The gate takes a repeated ``done`` as the old
                # turn and the old alert still stands: replay the work first.
                new_turn = seen_turn is not None and pane.turn not in (None, seen_turn)
                if pane.status == "working" or new_turn:
                    alerts.clear()
                if new_turn and pane.status != "working":
                    alerts.gate.handle(_STATUS_TO_PROGRAM["working"])
                report = _STATUS_TO_PROGRAM.get(pane.status or "")
                if report is not None:
                    alerts.gate.handle(report)
        for ident in [i for i in self._alerts if i not in live]:
            self._alerts.pop(ident).dispose()

    def _alert(self, server_id: str, key: str, alerts: _PaneAlerts, reason: str) -> None:
        link = self._links.get(server_id)
        server = link.state() if link is not None else None
        pane_id = link.pane_id_for_key(key) if link is not None else None
        pane = _find_pane(server, pane_id) if server is not None and pane_id else None
        if pane is None or pane_id is None:
            return
        alerts.attention = reason
        self._publish(
            EVENT_ATTENTION,
            HerdrAttention(
                server_id=server_id,
                pane_id=pane_id,
                reason=reason,
                title=pane.title,
                agent=pane.agent,
            ).model_dump(mode="json"),
        )

    def _note_input(self, server_id: str, key: str) -> None:
        alerts = self._alerts.get((server_id, key))
        if alerts is not None:
            alerts.clear()
            alerts.gate.on_input()

    def _link(self, server_id: str) -> ServerLink:
        link = self._links.get(server_id)
        if link is None:
            raise errors.server_not_found()
        server = link.state()
        if server is None or server.state != "ready":
            raise errors.HerdrError(
                409,
                "server_outdated",
                (server.message if server is not None else None)
                or "This Herdr server is not ready.",
            )
        return link

    async def open_pane(self, server_id: str, pane_id: str) -> str:
        """The terminal session that shows this pane, made when it is missing."""
        link = await self._link_after_restart(server_id)
        # One at a time: two clients that open one pane get one session.
        async with self._open_lock:
            key = link.pane_key(pane_id)
            if key is None:
                raise errors.pane_not_found()
            session_id = pane_session_id(server_id, key)
            existing = self._pane_sessions.get(session_id)
            if existing is not None and existing.is_alive():
                return session_id
            if existing is not None:
                self._drop_session(session_id)
            # A release of the view that had this id can still run. It is a
            # task of its own, so a cancel of this call does not cut it short.
            # Its last step ends the fanout of the id, so the new view must
            # not be made before it is over.
            pending = self._releases.get(session_id)
            if pending is not None:
                await asyncio.wait({pending})
                # The wait can take seconds. A link that ended in that time
                # ended only the views it knew, so this one would stand on a
                # dead link for good: read the link and the pane again.
                link = self._link(server_id)
                if link.pane_key(pane_id) != key:
                    raise errors.pane_not_found()
            session = HerdrPaneSession(
                session_id,
                link,
                key,
                on_input=lambda: self._note_input(server_id, key),
            )
            await session.start()
            await self._session_manager.adopt_session(session)
            self._pane_sessions[session_id] = session
            return session_id

    async def _link_after_restart(self, server_id: str) -> ServerLink:
        """The link of ``server_id``, with a short wait when it is absent.

        A pane view ends the moment its link drops, and the client asks for
        the pane again at once. The link is back only after a discovery pass
        and its first read, so an immediate answer would call a pane closed
        that is only between two links. A server that restarts can also be
        missing from the first pass, so discovery runs again until the wait
        ends for a server whose link dropped a moment ago. An unknown server
        is refused at once.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REOPEN_WAIT_SECONDS
        link = self._links.get(server_id)
        if link is None:
            await self._discover_quietly()
            link = self._links.get(server_id)
        while link is None and self._lost_recently(server_id) and loop.time() < deadline:
            await asyncio.sleep(min(REOPEN_RETRY_SECONDS, deadline - loop.time()))
            await self._discover_quietly()
            link = self._links.get(server_id)
        remaining = deadline - loop.time()
        if link is not None and link.state() is None and remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    # Each link reports its first read through ``_changed``.
                    while link.state() is None and self._links.get(server_id) is link:
                        self._change_seen.clear()
                        await self._change_seen.wait()
            except TimeoutError:
                pass
        return self._link(server_id)

    async def _discover_quietly(self) -> None:
        try:
            await self._discover_once()
        except Exception:
            logger.exception("herdr.discover_failed")

    def _lost_recently(self, server_id: str) -> bool:
        lost_at = self._lost_at.get(server_id)
        return (
            lost_at is not None
            and asyncio.get_running_loop().time() - lost_at < REOPEN_WAIT_SECONDS
        )

    async def set_control(
        self,
        server_id: str,
        pane_id: str,
        control: bool,
        client_id: str = "",
        *,
        actor: str | None = None,
    ) -> None:
        link = self._link(server_id)
        key = link.pane_key(pane_id)
        session = self._pane_sessions.get(pane_session_id(server_id, key)) if key else None
        if session is None or not session.is_alive():
            raise errors.pane_not_found()
        # Only a call that changed who controls the pane: a release from a
        # client that is not the controller does nothing.
        if await session.set_control(control, client_id):
            audit_event(
                "audit.herdr.control",
                actor=actor,
                target=f"{server_id}/pane/{pane_id}",
                control=control,
                client_id=client_id,
            )

    async def read_text(self, server_id: str, pane_id: str, lines: int) -> tuple[str, bool]:
        lines = max(1, min(lines, TEXT_MAX_LINES))
        return await self._link(server_id).read_text(pane_id, lines)

    async def create(
        self, request: HerdrCreateRequest, *, actor: str | None = None
    ) -> tuple[str, str | None]:
        """``(new pane id, agent error)``. The error is set when the pane was
        made and its agent did not start."""
        link = self._link(request.server_id)
        cwd = request.cwd.strip() if request.cwd else None
        if cwd:
            cwd = os.path.expanduser(cwd)
            if not os.path.isabs(cwd) or not await in_thread(os.path.isdir, cwd):
                raise errors.HerdrError(
                    422, "invalid_folder", f"This folder does not exist: {cwd}"
                )
        label = request.label.strip() if request.label else None
        try:
            pane_id = await link.create(
                request.placement,
                workspace_id=request.workspace_id,
                pane_id=request.pane_id,
                cwd=cwd,
                label=label or None,
                agent=request.agent,
            )
        except AgentNotStarted as exc:
            self._audit_created(request, exc.pane_id, agent_started=False, actor=actor)
            return exc.pane_id, f"The terminal is ready, but the agent did not start: {exc.error.message}"
        self._audit_created(request, pane_id, agent_started=bool(request.agent), actor=actor)
        return pane_id, None

    @staticmethod
    def _audit_created(
        request: HerdrCreateRequest, pane_id: str, *, agent_started: bool, actor: str | None
    ) -> None:
        audit_event(
            "audit.herdr.created",
            actor=actor,
            target=f"{request.server_id}/pane/{pane_id}",
            placement=request.placement,
            agent=request.agent,
            agent_started=agent_started,
        )

    async def start_agent(
        self, server_id: str, pane_id: str, agent: str, *, actor: str | None = None
    ) -> None:
        await self._link(server_id).start_agent(pane_id, agent)
        audit_event(
            "audit.herdr.agent_started",
            actor=actor,
            target=f"{server_id}/pane/{pane_id}",
            agent=agent,
        )

    async def rename(self, request: HerdrRenameRequest, *, actor: str | None = None) -> None:
        await self._link(request.server_id).rename(
            request.target, request.id, request.label.strip()
        )
        # Without the label: a name the user chose is not log content.
        audit_event(
            "audit.herdr.renamed",
            actor=actor,
            target=f"{request.server_id}/{request.target}/{request.id}",
        )

    async def close(self, request: HerdrCloseRequest, *, actor: str | None = None) -> None:
        await self._link(request.server_id).close(request.target, request.id)
        # Herdr actions change programs Mobaide does not own, so each leaves a
        # record, as session and git actions do. ``actor`` is the user on the
        # direct route; the relay path has none, as for projects.
        audit_event(
            "audit.herdr.closed",
            actor=actor,
            target=f"{request.server_id}/{request.target}/{request.id}",
        )

    def _drop_session(self, session_id: str) -> None:
        session = self._pane_sessions.pop(session_id, None)
        if session is None:
            return
        task = asyncio.ensure_future(self._session_manager.release_session(session_id, session))
        # Held here until it is done, and named by id so ``open_pane`` can
        # wait for it.
        self._releases[session_id] = task
        task.add_done_callback(lambda done: self._forget_release(session_id, done))

    def _forget_release(self, session_id: str, task: asyncio.Future[Any]) -> None:
        if self._releases.get(session_id) is task:
            del self._releases[session_id]

    def _sweep_idle_sessions(self) -> None:
        now = asyncio.get_running_loop().time()
        for session_id, session in list(self._pane_sessions.items()):
            idle_since = session.idle_since
            if not session.is_alive() or (
                idle_since is not None and now - idle_since > PANE_SESSION_IDLE_SECONDS
            ):
                self._drop_session(session_id)


def pane_session_id(server_id: str, pane_key: str) -> str:
    """The same pane always gets the same session id, so a client that
    reconnects finds its view again."""
    digest = hashlib.sha256(f"{server_id}\0{pane_key}".encode()).hexdigest()
    return f"herdr-{digest[:24]}"


def _panes(server: HerdrServer) -> list[HerdrPane]:
    return [
        pane
        for workspace in server.workspaces
        for tab in workspace.tabs
        for pane in tab.panes
    ]


def _find_pane(server: HerdrServer, pane_id: str) -> HerdrPane | None:
    return next((pane for pane in _panes(server) if pane.pane_id == pane_id), None)


def _installed_agents(launchers: Mapping[str, Any]) -> list[AgentChoice]:
    """The agents whose CLI resolves on this host. Reads only ``PATH`` and
    file stats, as a session launch would."""
    found: list[AgentChoice] = []
    for agent_id, launcher in launchers.items():
        label = AGENT_LABELS.get(agent_id)
        if label is None:
            continue
        try:
            binary = launcher.resolve_binary()
        except Exception:
            continue
        if binary:
            found.append(AgentChoice(id=agent_id, label=label, binary=str(binary)))
    return found


def build_herdr_service(
    mode: str,
    *,
    publish: Callable[[str, dict[str, Any]], None],
    session_manager: Any,
    cli_launchers: Mapping[str, Any] | None,
    herdr_bin: str | None = None,
    herdr_config_dir: str | None = None,
) -> HerdrService | None:
    """The service for ``AgentConfig.herdr_mode``; ``None`` for ``off``.

    ``herdr_bin`` and ``herdr_config_dir`` are the agent settings. They come
    from ``AgentConfig`` and not from ``os.environ``: only the Linux service
    exports the agent's ``.env`` file to the process.
    """
    if mode == "off":
        return None
    if mode == "fake":
        from .fake import FakeBackend

        backends: list[Any] = [FakeBackend]
    else:
        from .core import CoreBackend
        from .tui import TuiBackend

        backends = [
            functools.partial(
                TuiBackend, bin_override=herdr_bin, config_dir_override=herdr_config_dir
            ),
            CoreBackend,
        ]
    return HerdrService(
        publish=publish,
        session_manager=session_manager,
        cli_launchers=cli_launchers,
        backends=backends,
    )

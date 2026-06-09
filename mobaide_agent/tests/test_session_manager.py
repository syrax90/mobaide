from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.core.claude_launcher import AdHocLaunch, ClaudeOptions, LaunchSpec
from mobaide.core.models import SessionConfig, SessionKind, SessionState
from mobaide.core.session_manager import MaxSessionsError, SessionManager
from tests.conftest import IS_WINDOWS, default_shell


def _make_config(session_id: str) -> SessionConfig:
    return SessionConfig(
        session_id=session_id,
        command=default_shell(),
        rows=24,
        cols=80,
        scrollback_size=50,
    )


@pytest.mark.timeout(20)
class TestSessionManager:
    async def test_create_and_list(self) -> None:
        mgr = SessionManager(max_sessions=4)
        try:
            session = await mgr.create_session(_make_config("s1"))
            assert session.state == SessionState.RUNNING

            infos = mgr.list_sessions()
            assert len(infos) == 1
            assert infos[0].session_id == "s1"
        finally:
            await mgr.shutdown()

    async def test_get_session(self) -> None:
        mgr = SessionManager()
        try:
            await mgr.create_session(_make_config("s1"))
            session = await mgr.get_session("s1")
            assert session is not None
            assert session.session_id == "s1"

            missing = await mgr.get_session("nonexistent")
            assert missing is None
        finally:
            await mgr.shutdown()

    async def test_max_sessions_enforcement(self) -> None:
        mgr = SessionManager(max_sessions=2)
        try:
            await mgr.create_session(_make_config("s1"))
            await mgr.create_session(_make_config("s2"))

            with pytest.raises(MaxSessionsError):
                await mgr.create_session(_make_config("s3"))
        finally:
            await mgr.shutdown()

    async def test_duplicate_session_id_rejected(self) -> None:
        mgr = SessionManager()
        try:
            await mgr.create_session(_make_config("dup"))

            with pytest.raises(ValueError, match="already exists"):
                await mgr.create_session(_make_config("dup"))
        finally:
            await mgr.shutdown()

    async def test_stop_session_removes_from_list(self) -> None:
        mgr = SessionManager()
        try:
            await mgr.create_session(_make_config("s1"))
            assert len(mgr.list_sessions()) == 1

            await mgr.stop_session("s1")
            assert len(mgr.list_sessions()) == 0
        finally:
            await mgr.shutdown()

    async def test_stop_nonexistent_raises(self) -> None:
        mgr = SessionManager()
        try:
            with pytest.raises(KeyError):
                await mgr.stop_session("ghost")
        finally:
            await mgr.shutdown()

    async def test_stop_all(self) -> None:
        mgr = SessionManager()
        try:
            await mgr.create_session(_make_config("s1"))
            await mgr.create_session(_make_config("s2"))
            assert len(mgr.list_sessions()) == 2

            await mgr.stop_all()
            assert len(mgr.list_sessions()) == 0
        finally:
            await mgr.shutdown()

    async def test_liveness_monitor_detects_crash(self) -> None:
        """Spawn a short-lived process and verify liveness monitor cleans it up."""
        mgr = SessionManager()
        try:
            if IS_WINDOWS:
                cmd = ["cmd.exe", "/c", "echo bye"]
            else:
                cmd = ["/bin/bash", "-c", "echo bye"]

            config = SessionConfig(
                session_id="crash-test",
                command=cmd,
                rows=24,
                cols=80,
                scrollback_size=50,
            )
            await mgr.create_session(config)

            # Start liveness monitor with short interval
            await mgr.start_liveness_monitor(interval=0.5)

            # Wait for the process to die and liveness to detect it
            deadline = asyncio.get_event_loop().time() + 8.0
            while asyncio.get_event_loop().time() < deadline:
                infos = mgr.list_sessions()
                if not infos:
                    break
                await asyncio.sleep(0.3)

            # Session should have been cleaned up
            assert len(mgr.list_sessions()) == 0
        finally:
            await mgr.shutdown()

    async def test_shutdown_is_clean(self) -> None:
        mgr = SessionManager()
        await mgr.start_liveness_monitor(interval=1.0)
        await mgr.create_session(_make_config("s1"))
        await mgr.shutdown()
        assert len(mgr.list_sessions()) == 0


@pytest.mark.timeout(20)
class TestProjectAwareLaunchBranching:
    """``create_session`` splits on ``claude_options`` when
    ``project_name`` is set. With claude_options → ``_apply_project_launch``
    overwrites command/cwd/env. Without claude_options → ``_resolve_project_cwd``
    only sets cwd, leaving the caller-supplied shell argv intact.
    """

    async def test_claude_options_present_invokes_launcher(self, tmp_path) -> None:
        project = MagicMock()
        project.name = "proj"
        project.path = str(tmp_path)
        store = MagicMock()
        store.get = MagicMock(return_value=project)

        launcher = MagicMock()
        launcher.build = AsyncMock(
            return_value=LaunchSpec(
                argv=["claude", "--effort", "max"],
                cwd=str(tmp_path),
                env={"X": "Y"},
            ),
        )

        mgr = SessionManager(projects_store=store, launcher=launcher)
        try:
            config = SessionConfig(
                session_id="claude-sess",
                command=default_shell(),
                rows=24,
                cols=80,
                scrollback_size=50,
                project_name="proj",
                claude_options=ClaudeOptions(),
            )
            await mgr.create_session(config)
            launcher.build.assert_awaited_once()
            assert config.command == ["claude", "--effort", "max"]
            assert config.env == {"X": "Y"}
        finally:
            await mgr.shutdown()

    async def test_no_claude_options_only_resolves_cwd(self, tmp_path) -> None:
        project = MagicMock()
        project.name = "proj"
        project.path = str(tmp_path)
        store = MagicMock()
        store.get = MagicMock(return_value=project)

        launcher = MagicMock()
        launcher.build = AsyncMock()

        mgr = SessionManager(projects_store=store, launcher=launcher)
        try:
            shell_argv = default_shell()
            config = SessionConfig(
                session_id="shell-sess",
                command=shell_argv,
                rows=24,
                cols=80,
                scrollback_size=50,
                project_name="proj",
                claude_options=None,
                kind=SessionKind.SHELL,
            )
            await mgr.create_session(config)
            launcher.build.assert_not_awaited()
            assert config.command == shell_argv
            assert config.cwd == str(tmp_path)
            assert mgr.list_sessions()[0].kind == SessionKind.SHELL
        finally:
            await mgr.shutdown()

    async def test_no_claude_options_unknown_project_raises_keyerror(self) -> None:
        store = MagicMock()
        store.get = MagicMock(return_value=None)
        mgr = SessionManager(projects_store=store, launcher=MagicMock())
        try:
            config = SessionConfig(
                session_id="missing",
                command=default_shell(),
                rows=24,
                cols=80,
                scrollback_size=50,
                project_name="ghost",
                claude_options=None,
            )
            with pytest.raises(KeyError, match="ghost"):
                await mgr.create_session(config)
        finally:
            await mgr.shutdown()


@pytest.mark.timeout(20)
class TestAdHocClaudeLaunchBranching:
    """``create_session`` dispatches to ``_apply_adhoc_launch`` when
    ``project_name is None`` AND ``claude_options is not None``. The
    launcher receives an :class:`AdHocLaunch` target, and the projects
    store must never be consulted — a custom-path session is by
    definition not a project."""

    async def test_adhoc_dispatch_invokes_launcher_with_adhoc_target(
        self, tmp_path,
    ) -> None:
        """Happy path: ``project_name=None`` plus ``claude_options`` routes
        through ``_apply_adhoc_launch``. The target passed to ``launcher.build``
        is an :class:`AdHocLaunch` carrying ``config.cwd``; ``config.command``,
        ``config.cwd``, and ``config.env`` are overwritten by ``LaunchSpec``."""
        store = MagicMock()
        store.get = MagicMock()

        launcher = MagicMock()
        launcher.build = AsyncMock(
            return_value=LaunchSpec(
                argv=["claude", "--effort", "max"],
                cwd=str(tmp_path),
                env={"FOO": "bar"},
            ),
        )

        mgr = SessionManager(projects_store=store, launcher=launcher)
        try:
            config = SessionConfig(
                session_id="adhoc-sess",
                command=default_shell(),
                cwd=str(tmp_path),
                rows=24,
                cols=80,
                scrollback_size=50,
                project_name=None,
                claude_options=ClaudeOptions(),
            )
            await mgr.create_session(config)

            launcher.build.assert_awaited_once()
            call = launcher.build.await_args
            assert call is not None
            target = call.args[0]
            assert isinstance(target, AdHocLaunch)
            assert target.cwd == str(tmp_path)

            assert config.command == ["claude", "--effort", "max"]
            assert config.cwd == str(tmp_path)
            assert config.env == {"FOO": "bar"}

            store.get.assert_not_called()
        finally:
            await mgr.shutdown()

    async def test_adhoc_dispatch_never_consults_projects_store(
        self, tmp_path,
    ) -> None:
        """Defense-in-depth: even if a ``ProjectsStore`` is wired, the
        ad-hoc branch must never call ``store.get`` — a custom-path session
        cannot be silently re-routed to a saved project that happens to
        share its path."""
        store = MagicMock()
        store.get = MagicMock()

        launcher = MagicMock()
        launcher.build = AsyncMock(
            return_value=LaunchSpec(
                argv=["claude"], cwd=str(tmp_path), env={},
            ),
        )

        mgr = SessionManager(projects_store=store, launcher=launcher)
        try:
            config = SessionConfig(
                session_id="adhoc-no-store",
                command=default_shell(),
                cwd=str(tmp_path),
                rows=24, cols=80, scrollback_size=50,
                project_name=None,
                claude_options=ClaudeOptions(),
            )
            await mgr.create_session(config)
            store.get.assert_not_called()
        finally:
            await mgr.shutdown()

    async def test_adhoc_dispatch_without_launcher_raises_value_error(
        self, tmp_path,
    ) -> None:
        """When ``project_name is None`` and ``claude_options`` is set but
        the manager has no launcher (misconfigured deployment), the
        ``ValueError`` must surface — mirroring ``_apply_project_launch``'s
        own missing-launcher guard so the router error mapping (400) stays
        uniform."""
        mgr = SessionManager(projects_store=None, launcher=None)
        try:
            config = SessionConfig(
                session_id="adhoc-no-launcher",
                command=default_shell(),
                cwd=str(tmp_path),
                rows=24, cols=80, scrollback_size=50,
                project_name=None,
                claude_options=ClaudeOptions(),
            )
            with pytest.raises(ValueError, match="launcher"):
                await mgr.create_session(config)
        finally:
            await mgr.shutdown()

    async def test_adhoc_dispatch_without_cwd_raises_value_error(self) -> None:
        """Defense-in-depth: router/relay pre-validate ``cwd``, but if a
        future caller still slips through with ``cwd=None`` and
        ``claude_options`` set, the manager must reject it with a
        :class:`ValueError` (mapped to 400) — not crash inside
        ``AdHocLaunch(cwd=None)``."""
        launcher = MagicMock()
        launcher.build = AsyncMock()
        mgr = SessionManager(launcher=launcher)
        try:
            config = SessionConfig(
                session_id="adhoc-no-cwd",
                command=default_shell(),
                cwd=None,
                rows=24, cols=80, scrollback_size=50,
                project_name=None,
                claude_options=ClaudeOptions(),
            )
            with pytest.raises(ValueError, match="cwd"):
                await mgr.create_session(config)
            launcher.build.assert_not_awaited()
        finally:
            await mgr.shutdown()

    async def test_no_project_no_claude_options_skips_both_branches(
        self, tmp_path,
    ) -> None:
        """When both ``project_name`` and ``claude_options`` are ``None``,
        the dispatch matrix falls through to the raw Terminal path — no
        launcher build, no project lookup, ``config.command/cwd/env``
        survive as caller-supplied. Pins the dispatch matrix's else-arm
        so a future refactor that adds an accidental side-effect on the
        bare-cwd Terminal path is caught."""
        store = MagicMock()
        store.get = MagicMock()
        launcher = MagicMock()
        launcher.build = AsyncMock()

        mgr = SessionManager(projects_store=store, launcher=launcher)
        try:
            shell_argv = default_shell()
            config = SessionConfig(
                session_id="raw-term",
                command=shell_argv,
                cwd=str(tmp_path),
                rows=24, cols=80, scrollback_size=50,
                project_name=None,
                claude_options=None,
                kind=SessionKind.SHELL,
            )
            await mgr.create_session(config)
            launcher.build.assert_not_awaited()
            store.get.assert_not_called()
            assert config.command == shell_argv
            assert config.cwd == str(tmp_path)
        finally:
            await mgr.shutdown()


class _DeadBackend:
    """Minimal :class:`PtyBackend` stub reporting a dead process.

    ``_check_liveness`` only consults ``is_alive()`` and ``get_pid()`` on the
    backend, so this stub is enough to drive the RUNNING → CRASHED transition
    without spawning a real process.
    """

    def is_alive(self) -> bool:
        return False

    def get_pid(self) -> int | None:
        return 4242


def _crashable_session(session_id: str) -> TerminalSession:
    from mobaide.core.session import TerminalSession

    session = TerminalSession(_make_config(session_id), backend=_DeadBackend())
    session._state = SessionState.RUNNING
    return session


class TestLivenessExitReason:
    """When ``_check_liveness`` flips a dead session to CRASHED it must
    stamp ``_exit_reason`` so the wire snapshot for an unexpected death is
    non-null — without clobbering a reason a SessionEnd hook already set.
    """

    async def test_crash_sets_process_exited_reason(self) -> None:
        mgr = SessionManager()
        session = _crashable_session("s1")
        mgr._sessions["s1"] = session
        try:
            await mgr._check_liveness()
            assert session.state == SessionState.CRASHED
            assert session._exit_reason == "process_exited"
        finally:
            await mgr.shutdown()

    async def test_crash_does_not_overwrite_hook_supplied_reason(self) -> None:
        mgr = SessionManager()
        session = _crashable_session("s1")
        session._exit_reason = "clear"  # e.g. a SessionEnd hook landed first
        mgr._sessions["s1"] = session
        try:
            await mgr._check_liveness()
            assert session.state == SessionState.CRASHED
            assert session._exit_reason == "clear"
        finally:
            await mgr.shutdown()

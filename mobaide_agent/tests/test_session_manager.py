from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.core.claude_launcher import ClaudeOptions, LaunchSpec
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

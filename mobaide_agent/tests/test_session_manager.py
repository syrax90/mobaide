from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.core.antigravity_launcher import AntigravityOptions
from mobaide.core.claude_launcher import AdHocLaunch, ClaudeOptions, LaunchSpec
from mobaide.core.codex_launcher import CodexOptions
from mobaide.core.models import SessionConfig, SessionKind, SessionState
from mobaide.core.opencode_launcher import OpenCodeOptions
from mobaide.core.pty_backend import FakePtyBackend
from mobaide.core.session_manager import MaxSessionsError, SessionManager
from mobaide.linear.worktree import WorktreeResult
from tests.conftest import IS_WINDOWS, default_shell


def _make_config(session_id: str) -> SessionConfig:
    return SessionConfig(
        session_id=session_id,
        command=default_shell(),
        rows=24,
        cols=80,
        scrollback_size=50,
    )


class TestSessionConfigRepr:
    def test_repr_hides_the_launch_env(self) -> None:
        """``create_session`` logs its config; the launch env holds the
        user's secrets."""
        config = SessionConfig(
            session_id="s1",
            command=["claude"],
            env={"ANTHROPIC_API_KEY": "sk-ant-secret"},
        )

        text = repr(config)

        assert "session_id='s1'" in text
        assert "sk-ant-secret" not in text


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

        mgr = SessionManager(projects_store=store, launcher=launcher, backend_factory=FakePtyBackend)
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
class TestCodexLaunchBranching:
    """``create_session`` routes a ``codex_options`` session to the Codex
    launcher (not the Claude one), for both project and ad-hoc targets, and
    raises a clear error when no Codex launcher is wired.
    """

    async def test_project_codex_options_invokes_codex_launcher(self, tmp_path) -> None:
        project = MagicMock()
        project.name = "proj"
        project.path = str(tmp_path)
        store = MagicMock()
        store.get = MagicMock(return_value=project)

        claude_launcher = MagicMock()
        claude_launcher.build = AsyncMock()
        codex_launcher = MagicMock()
        # The returned argv must be a real spawnable command so the PTY start
        # succeeds on a host without ``codex`` installed; the dispatch (which
        # launcher / which options) is what this test pins, not the argv — that
        # is covered by ``test_codex_launcher``.
        codex_launcher.build = AsyncMock(
            return_value=LaunchSpec(
                argv=default_shell(),
                cwd=str(tmp_path),
                env={"X": "Y"},
            ),
        )

        mgr = SessionManager(
            projects_store=store,
            launcher=claude_launcher,
            codex_launcher=codex_launcher,
        )
        try:
            config = SessionConfig(
                session_id="codex-sess",
                command=default_shell(),
                rows=24, cols=80, scrollback_size=50,
                project_name="proj",
                codex_options=CodexOptions(),
                kind=SessionKind.CODEX,
            )
            await mgr.create_session(config)

            codex_launcher.build.assert_awaited_once()
            claude_launcher.build.assert_not_awaited()
            assert config.env == {"X": "Y"}
            # The Codex launcher received the project target + a CodexOptions.
            call = codex_launcher.build.await_args
            assert call.args[0] is project
            assert isinstance(call.args[1], CodexOptions)
            assert mgr.list_sessions()[0].kind == SessionKind.CODEX
        finally:
            await mgr.shutdown()

    async def test_adhoc_codex_dispatch_uses_adhoc_target_and_skips_store(
        self, tmp_path,
    ) -> None:
        store = MagicMock()
        store.get = MagicMock()
        codex_launcher = MagicMock()
        codex_launcher.build = AsyncMock(
            return_value=LaunchSpec(argv=default_shell(), cwd=str(tmp_path), env={}),
        )

        mgr = SessionManager(
            projects_store=store,
            launcher=MagicMock(),
            codex_launcher=codex_launcher,
        )
        try:
            config = SessionConfig(
                session_id="adhoc-codex",
                command=default_shell(),
                cwd=str(tmp_path),
                rows=24, cols=80, scrollback_size=50,
                codex_options=CodexOptions(),
                kind=SessionKind.CODEX,
            )
            await mgr.create_session(config)

            codex_launcher.build.assert_awaited_once()
            store.get.assert_not_called()
            assert isinstance(codex_launcher.build.await_args.args[0], AdHocLaunch)
        finally:
            await mgr.shutdown()


@pytest.mark.timeout(20)
class TestAntigravityLaunchBranching:
    """``create_session`` routes an ``antigravity_options`` session to the
    Antigravity launcher (not the Claude or Codex one), for both project and
    ad-hoc targets, and raises a clear error when no Antigravity launcher is
    wired.
    """

    async def test_project_antigravity_options_invokes_antigravity_launcher(
        self, tmp_path,
    ) -> None:
        project = MagicMock()
        project.name = "proj"
        project.path = str(tmp_path)
        store = MagicMock()
        store.get = MagicMock(return_value=project)

        claude_launcher = MagicMock()
        claude_launcher.build = AsyncMock()
        antigravity_launcher = MagicMock()
        # The returned argv must be a real spawnable command so the PTY start
        # succeeds on a host without ``agy`` installed; the dispatch (which
        # launcher / which options) is what this test pins, not the argv — that
        # is covered by ``test_antigravity_launcher``.
        antigravity_launcher.build = AsyncMock(
            return_value=LaunchSpec(
                argv=default_shell(),
                cwd=str(tmp_path),
                env={"X": "Y"},
            ),
        )

        mgr = SessionManager(
            projects_store=store,
            launcher=claude_launcher,
            antigravity_launcher=antigravity_launcher,
        )
        try:
            config = SessionConfig(
                session_id="antigravity-sess",
                command=default_shell(),
                rows=24, cols=80, scrollback_size=50,
                project_name="proj",
                antigravity_options=AntigravityOptions(),
                kind=SessionKind.ANTIGRAVITY,
            )
            await mgr.create_session(config)

            antigravity_launcher.build.assert_awaited_once()
            claude_launcher.build.assert_not_awaited()
            assert config.env == {"X": "Y"}
            # The Antigravity launcher received the project target + an
            # AntigravityOptions.
            call = antigravity_launcher.build.await_args
            assert call.args[0] is project
            assert isinstance(call.args[1], AntigravityOptions)
            assert mgr.list_sessions()[0].kind == SessionKind.ANTIGRAVITY
        finally:
            await mgr.shutdown()

    async def test_adhoc_antigravity_dispatch_uses_adhoc_target_and_skips_store(
        self, tmp_path,
    ) -> None:
        store = MagicMock()
        store.get = MagicMock()
        antigravity_launcher = MagicMock()
        antigravity_launcher.build = AsyncMock(
            return_value=LaunchSpec(argv=default_shell(), cwd=str(tmp_path), env={}),
        )

        mgr = SessionManager(
            projects_store=store,
            launcher=MagicMock(),
            antigravity_launcher=antigravity_launcher,
        )
        try:
            config = SessionConfig(
                session_id="adhoc-antigravity",
                command=default_shell(),
                cwd=str(tmp_path),
                rows=24, cols=80, scrollback_size=50,
                antigravity_options=AntigravityOptions(),
                kind=SessionKind.ANTIGRAVITY,
            )
            await mgr.create_session(config)

            antigravity_launcher.build.assert_awaited_once()
            store.get.assert_not_called()
            assert isinstance(
                antigravity_launcher.build.await_args.args[0], AdHocLaunch,
            )
        finally:
            await mgr.shutdown()


@pytest.mark.timeout(20)
class TestOpenCodeLaunchBranching:
    """``create_session`` routes an ``opencode_options`` session to the
    OpenCode launcher (not a sibling's), and raises a clear error when no
    OpenCode launcher is wired.
    """

    async def test_project_opencode_options_invokes_opencode_launcher(
        self, tmp_path,
    ) -> None:
        project = MagicMock()
        project.name = "proj"
        project.path = str(tmp_path)
        store = MagicMock()
        store.get = MagicMock(return_value=project)

        claude_launcher = MagicMock()
        claude_launcher.build = AsyncMock()
        antigravity_launcher = MagicMock()
        antigravity_launcher.build = AsyncMock()
        opencode_launcher = MagicMock()
        # A real spawnable command, so the PTY start succeeds on a host
        # without ``opencode``; the argv itself is covered by
        # ``test_opencode_launcher``.
        opencode_launcher.build = AsyncMock(
            return_value=LaunchSpec(
                argv=default_shell(),
                cwd=str(tmp_path),
                env={"X": "Y"},
            ),
        )
        options = OpenCodeOptions(auto_approve=True)

        mgr = SessionManager(
            projects_store=store,
            launcher=claude_launcher,
            antigravity_launcher=antigravity_launcher,
            opencode_launcher=opencode_launcher,
        )
        try:
            config = SessionConfig(
                session_id="opencode-sess",
                command=default_shell(),
                rows=24, cols=80, scrollback_size=50,
                project_name="proj",
                opencode_options=options,
                kind=SessionKind.OPENCODE,
            )
            await mgr.create_session(config)

            opencode_launcher.build.assert_awaited_once()
            claude_launcher.build.assert_not_awaited()
            antigravity_launcher.build.assert_not_awaited()
            call = opencode_launcher.build.await_args
            assert call.args[0] is project
            assert call.args[1] is options
            assert config.env == {"X": "Y"}
            assert mgr.list_sessions()[0].kind == SessionKind.OPENCODE
        finally:
            await mgr.shutdown()

    async def test_no_opencode_launcher_fails_instead_of_running_another_cli(
        self, tmp_path,
    ) -> None:
        claude_launcher = MagicMock()
        claude_launcher.build = AsyncMock()
        mgr = SessionManager(projects_store=MagicMock(), launcher=claude_launcher)
        try:
            config = SessionConfig(
                session_id="opencode-unwired",
                command=default_shell(),
                cwd=str(tmp_path),
                rows=24, cols=80, scrollback_size=50,
                opencode_options=OpenCodeOptions(),
                kind=SessionKind.OPENCODE,
            )
            with pytest.raises(ValueError, match="OpenCode launcher"):
                await mgr.create_session(config)

            claude_launcher.build.assert_not_awaited()
            assert mgr.list_sessions() == []
        finally:
            await mgr.shutdown()

    async def test_connect_is_a_terminal_session_even_when_the_chat_view_is_asked_for(
        self, tmp_path,
    ) -> None:
        """``opencode auth login`` is an interactive terminal program: sent to
        the chat (ACP) path it would start an agent session instead of the
        sign-in."""

        class _ChatSpec:
            display_name = "OpenCode"
            prepared = 0

            async def prepare(self, target, options):
                type(self).prepared += 1
                raise AssertionError("a sign-in must not take the chat path")

        opencode_launcher = MagicMock()
        opencode_launcher.build = AsyncMock(
            return_value=LaunchSpec(argv=["does-not-run"], cwd=str(tmp_path), env={}),
        )
        options = OpenCodeOptions(connect=True)
        mgr = SessionManager(
            opencode_launcher=opencode_launcher,
            chat_agents={"opencode": _ChatSpec()},
            backend_factory=FakePtyBackend,
        )
        try:
            session = await mgr.create_session(
                SessionConfig(
                    session_id="opencode-connect",
                    command=[],
                    cwd=str(tmp_path),
                    opencode_options=options,
                    kind=SessionKind.OPENCODE,
                    ui="chat",
                )
            )

            assert _ChatSpec.prepared == 0
            assert getattr(session, "is_chat", False) is not True
            opencode_launcher.build.assert_awaited_once()
            assert opencode_launcher.build.await_args.args[1] is options
            (info,) = mgr.list_sessions()
            assert (info.kind, info.ui) == (SessionKind.OPENCODE, "terminal")
            # Its own exit is the end of the sign-in, not a crash.
            assert session._config.exits_when_done is True
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

        mgr = SessionManager(projects_store=store, launcher=launcher, backend_factory=FakePtyBackend)
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


@pytest.mark.timeout(20)
class TestTerminalWorktreeResolution:
    """Terminal mode (``claude_options=None`` + ``project_name`` set) creates a
    real git worktree when ``config.worktree`` is requested, mirroring
    ``ClaudeLauncher``'s Standard-mode guards while keeping the session
    ``SessionKind.SHELL``. ``safe_path_exists`` runs the real ``Path.exists``,
    so the git-project cases materialise a ``.git`` entry under ``tmp_path``;
    the ``WorktreeManager`` is an ``AsyncMock`` so no real ``git`` runs.
    """

    @staticmethod
    def _store_for(project_name: str, project_path: str) -> MagicMock:
        project = MagicMock()
        project.name = project_name
        project.path = project_path
        store = MagicMock()
        store.get = MagicMock(return_value=project)
        return store

    @staticmethod
    def _shell_config(session_id: str, project_name: str, **kwargs) -> SessionConfig:
        return SessionConfig(
            session_id=session_id,
            command=default_shell(),
            rows=24,
            cols=80,
            scrollback_size=50,
            project_name=project_name,
            claude_options=None,
            kind=SessionKind.SHELL,
            **kwargs,
        )

    async def test_worktree_on_git_project_resolves_cwd_to_worktree(
        self, tmp_path,
    ) -> None:
        """``worktree=True`` over a git project resolves ``config.cwd`` to the
        worktree path the manager returns — not the project root — and the
        session stays ``SHELL``. The launcher is never consulted."""
        (tmp_path / ".git").mkdir()
        store = self._store_for("proj", str(tmp_path))

        # The resolved cwd must be a real directory: create_session spawns a
        # PTY into config.cwd, so a non-existent worktree path would fail in
        # the backend rather than at cwd resolution.
        worktree_dir = tmp_path / "wt" / "feature-x"
        worktree_dir.mkdir(parents=True)
        worktree_path = str(worktree_dir)
        worktree_mgr = AsyncMock()
        worktree_mgr.create_worktree.return_value = WorktreeResult(
            path=worktree_path, is_new=True, branch="feature/x",
        )

        launcher = MagicMock()
        launcher.build = AsyncMock()

        mgr = SessionManager(
            projects_store=store, launcher=launcher, worktree_mgr=worktree_mgr,
        )
        try:
            config = self._shell_config(
                "wt-on", "proj", worktree=True, branch_name="feature/x",
            )
            await mgr.create_session(config)

            worktree_mgr.create_worktree.assert_awaited_once()
            call = worktree_mgr.create_worktree.await_args
            # create_standard_worktree(mgr, project_root, branch) ->
            # mgr.create_worktree(slug, project_root, branch_name=branch)
            assert call.args[0] == "feature-x"  # convert_to_worktree_slug("", "feature/x")
            assert call.args[1] == str(tmp_path)
            assert call.kwargs["branch_name"] == "feature/x"

            launcher.build.assert_not_awaited()
            assert config.cwd == worktree_path
            assert mgr.list_sessions()[0].kind == SessionKind.SHELL
        finally:
            await mgr.shutdown()

    async def test_worktree_on_git_project_blank_branch_uses_terminal_prefix(
        self, tmp_path,
    ) -> None:
        """A blank/``None`` ``branch_name`` falls back to a unique
        ``terminal-session-<hex>`` branch (the SHELL path passes
        ``fallback_prefix="terminal-session"``), never the Claude default."""
        (tmp_path / ".git").mkdir()
        store = self._store_for("proj", str(tmp_path))

        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        worktree_mgr = AsyncMock()
        worktree_mgr.create_worktree.return_value = WorktreeResult(
            path=str(worktree_dir), is_new=True, branch="terminal-session-abc",
        )

        mgr = SessionManager(
            projects_store=store, launcher=MagicMock(), worktree_mgr=worktree_mgr,
        )
        try:
            config = self._shell_config(
                "wt-blank", "proj", worktree=True, branch_name=None,
            )
            await mgr.create_session(config)

            call = worktree_mgr.create_worktree.await_args
            branch = call.kwargs["branch_name"]
            assert branch.startswith("terminal-session-")
            assert branch != "terminal-session-"  # a hex suffix was appended
            assert config.cwd == str(worktree_dir)
        finally:
            await mgr.shutdown()

    async def test_worktree_on_non_git_project_falls_back_to_project_root(
        self, tmp_path,
    ) -> None:
        """``worktree=True`` for a project with no ``.git`` is downgraded to the
        project root (stale-client guard) and the ``WorktreeManager`` is never
        invoked."""
        # No ``.git`` is created under tmp_path -> safe_path_exists is False.
        store = self._store_for("proj", str(tmp_path))

        worktree_mgr = AsyncMock()

        mgr = SessionManager(
            projects_store=store, launcher=MagicMock(), worktree_mgr=worktree_mgr,
        )
        try:
            config = self._shell_config("wt-nongit", "proj", worktree=True)
            await mgr.create_session(config)

            assert config.cwd == str(tmp_path)
            worktree_mgr.create_worktree.assert_not_awaited()
        finally:
            await mgr.shutdown()

    async def test_worktree_false_resolves_project_root_without_manager(
        self, tmp_path,
    ) -> None:
        """Regression companion to ``test_no_claude_options_only_resolves_cwd``:
        with ``worktree`` defaulted ``False``, even a git project resolves to
        the project root and never touches the (here ``None``) manager."""
        (tmp_path / ".git").mkdir()
        store = self._store_for("proj", str(tmp_path))

        mgr = SessionManager(
            projects_store=store, launcher=MagicMock(), worktree_mgr=None,
        )
        try:
            config = self._shell_config("wt-off", "proj")  # worktree defaults False
            await mgr.create_session(config)
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


class _FakeChatSession:
    """Stands in for ``ChatSession``: registers without spawning an adapter."""

    is_chat = True
    created = 0

    def __init__(
        self, config, plan, *, agent_title, load_session_id=None, attention_dedupe=None,
    ) -> None:
        type(self).created += 1
        self.session_id = config.session_id
        self._kind = config.kind
        self.acp_session_id = load_session_id
        self.attention_dedupe = attention_dedupe

    def to_info(self):
        return MagicMock(kind=self._kind)

    async def start(self) -> None:
        return None

    async def stop(self, force: bool = False) -> None:
        return None


class _SlowChatSpec:
    """A chat agent whose ``prepare`` yields, as the real ones do."""

    display_name = "Claude Code"

    async def prepare(self, target, options):
        from mobaide.acp.agents import ChatLaunchPlan

        await asyncio.sleep(0.01)
        return ChatLaunchPlan(argv=["fake-acp"], env={}, cwd=str(target.cwd))


@pytest.mark.timeout(20)
class TestChatResumeReuse:
    async def test_concurrent_resumes_of_one_stored_session_share_one_session(
        self, tmp_path, monkeypatch
    ) -> None:
        """Both resumes pass the early lookup while ``prepare`` awaits; the
        recheck under the lock hands the second the first one's session."""
        import mobaide.acp.session as acp_session

        _FakeChatSession.created = 0
        monkeypatch.setattr(acp_session, "ChatSession", _FakeChatSession)
        mgr = SessionManager(chat_agents={"claude": _SlowChatSpec()})

        def config(session_id: str) -> SessionConfig:
            return SessionConfig(
                session_id=session_id,
                command=[],
                cwd=str(tmp_path),
                ui="chat",
                chat_resume_id="stored-1",
            )

        try:
            first, second = await asyncio.gather(
                mgr.create_session(config("chat-a")),
                mgr.create_session(config("chat-b")),
            )

            assert first is second
            assert _FakeChatSession.created == 1
            assert len(mgr._sessions) == 1
        finally:
            await mgr.shutdown()


@pytest.mark.timeout(20)
class TestSharedTurnDedupe:
    async def test_terminal_and_chat_sessions_share_one_dedupe(
        self, tmp_path, monkeypatch
    ) -> None:
        """A chat and a terminal showing one live Codex thread can only pair
        their alerts through the same instance."""
        import mobaide.acp.session as acp_session

        monkeypatch.setattr(acp_session, "ChatSession", _FakeChatSession)
        mgr = SessionManager(
            chat_agents={"claude": _SlowChatSpec()}, backend_factory=FakePtyBackend
        )
        try:
            terminal = await mgr.create_session(
                SessionConfig(
                    session_id="term",
                    command=["does-not-run"],
                    cwd=str(tmp_path),
                    kind=SessionKind.SHELL,
                )
            )
            chat = await mgr.create_session(
                SessionConfig(session_id="chat", command=[], cwd=str(tmp_path), ui="chat")
            )

            assert terminal._attention_dedupe is not None
            assert chat.attention_dedupe is terminal._attention_dedupe
        finally:
            await mgr.shutdown()


class TestLivenessFinishedCommand:
    """A session whose command is meant to end (a sign-in) leaves the list
    like one the user stopped: no crash, the removed hook runs, and the
    clients get a session list without its row."""

    async def test_a_finished_sign_in_is_removed_and_announced(self) -> None:
        published: list[list[str]] = []

        class _Bus:
            def publish(self, topic, payload):
                if topic == "sessions.changed":
                    published.append([s["session_id"] for s in payload["sessions"]])

        removed: list[str] = []
        mgr = SessionManager(event_bus=_Bus(), on_session_removed=removed.append)
        session = _crashable_session("signin")
        session._config.exits_when_done = True
        mgr._sessions["signin"] = session
        try:
            await mgr._check_liveness()

            assert session.state == SessionState.STOPPED
            assert session._exit_reason is None
            assert removed == ["signin"]
            assert published == [[]]
        finally:
            await mgr.shutdown()

    async def test_a_crashed_session_is_still_cleaned_up_without_an_announcement(self) -> None:
        removed: list[str] = []
        mgr = SessionManager(on_session_removed=removed.append)
        mgr._sessions["s1"] = _crashable_session("s1")
        try:
            await mgr._check_liveness()

            assert mgr.list_sessions() == []
            assert removed == []
        finally:
            await mgr.shutdown()

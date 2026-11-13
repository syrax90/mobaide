"""Tests for session REST endpoints."""
from __future__ import annotations

from mobaide.core import ClaudeBinaryNotFoundError


class TestCreateSession:
    async def test_create_returns_201(self, client, auth_headers):
        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["state"] == "running"
        assert data["pid"] is not None

    async def test_create_with_custom_command(self, client, auth_headers):
        import sys
        cmd = ["cmd.exe", "/c", "echo hello"] if sys.platform == "win32" else ["/bin/echo", "hello"]
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"command": cmd},
        )
        assert resp.status_code == 201


    async def test_create_exceeds_max_sessions_returns_409(self, client, auth_headers):
        """Creating sessions beyond max_sessions limit returns 409 Conflict."""
        # agent_config fixture sets max_sessions=4
        for _ in range(4):
            resp = await client.post("/api/sessions", headers=auth_headers, json={})
            assert resp.status_code == 201

        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        assert resp.status_code == 409
        assert "limit" in resp.json()["detail"].lower()

    async def test_custom_cli_returns_400_even_with_resolved_command(
        self, client, auth_headers,
    ):
        """The agent-direct REST layer has no registry access, so a custom
        kind is rejected outright — a caller smuggling the relay-only
        ``resolved_command``/``resolved_name`` fields must not be able to
        spawn an arbitrary command through this path."""
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "custom_0123456789ab",
                "resolved_command": "echo pwned",
                "resolved_name": "Pwn",
            },
        )
        assert resp.status_code == 400
        assert "relay" in resp.json()["detail"].lower()


class TestCreateSessionProjectPathErrors:
    async def test_nonexistent_project_path_returns_400_with_clean_detail(
        self, client, auth_headers, tmp_path,
    ):
        """A project whose path does not exist must fail the session-create
        request with 400 and a readable, unescaped path in the detail.

        Regression guard for the reconcile-project-path-format issue: the
        old code let ``OSError`` propagate to Starlette's default 500, and
        the mobile client rendered the path as ``D:\\\\ML\\\\...`` (four
        backslashes per separator) because ``OSError.__str__`` embeds
        ``repr(filename)`` and the JSON body added another escape layer.

        The project is seeded through ``POST /api/projects`` — the same
        ingest boundary a real mobile client uses — so the path is
        normalized to POSIX form before it reaches the launcher.
        """
        missing = tmp_path / "does-not-exist"

        create = await client.post(
            "/api/projects",
            headers=auth_headers,
            json={"name": "missing", "path": str(missing)},
        )
        assert create.status_code == 201

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"project_name": "missing"},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Project path not found" in detail
        # The path must appear only in POSIX form, never as a ``repr``-
        # escaped string with doubled backslashes. We check the raw
        # response bytes so a future regression that re-introduces
        # ``str(exc)`` fails loudly.
        assert "\\\\" not in resp.text

    async def test_non_fnf_oserror_is_mapped_to_400_with_strerror_and_filename(
        self, app, client, auth_headers,
    ):
        """The broader ``except OSError`` branch (distinct from the
        ``FileNotFoundError`` branch) catches sibling ``OSError`` subclasses —
        e.g. ``PermissionError`` from ``pywinpty``/``subprocess`` when the
        cwd exists but cannot be opened — and builds its detail from
        ``exc.strerror`` + ``exc.filename`` rather than ``str(exc)``. This
        guards against the original 4-backslash rendering that ``str(exc)``
        produces via ``repr(filename)``.
        """
        # Use a real ``PermissionError`` (OSError subclass that is NOT
        # FileNotFoundError) to exercise the broader handler. We monkeypatch
        # ``create_session`` on the live manager to raise it — the router's
        # try/except is the unit under test, not the spawn path itself.
        async def _raise_permission_error(*args, **kwargs):
            raise PermissionError(13, "Permission denied", "D:/restricted/dir")

        app.state.session_manager.create_session = _raise_permission_error

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # strerror and filename must both appear, joined by ": " — not as
        # the single ``str(exc)`` blob that would embed ``repr(filename)``.
        assert "Permission denied" in detail
        assert "D:/restricted/dir" in detail
        # Defense against regression to ``str(exc)`` / ``repr(filename)``:
        # the POSIX forward slash must survive the JSON round trip without
        # being replaced by an escaped backslash sequence.
        assert "\\\\" not in resp.text

    async def test_worktree_creation_error_is_mapped_to_400_with_branch_and_stderr(
        self, app, client, auth_headers,
    ):
        """Regression guard for the ``fix-worktree-starting`` bug class.

        When the user explicitly toggled ``worktree=true`` and the
        launcher-owned worktree creation fails (e.g. ``git worktree add``
        errors, no resolvable start point, broken git binary),
        ``WorktreeCreationError`` must surface as HTTP 400 with the branch
        name and git stderr interpolated directly into ``detail`` — not a
        500 from Starlette, and not a silent fallback to project root that
        the user would never see.

        This test pins the router's ``except WorktreeCreationError`` branch:
        the failure bubbles from ``SessionManager.create_session`` through
        ``router.create_session`` and lands as a clean readable 400. It is
        distinct from the ``OSError`` branches (which test project-path
        existence) and the ``FileNotFoundError`` branches (which test the
        cwd-existence gate) — prior to this test nothing exercised the
        worktree-creation failure edge at the HTTP boundary.
        """
        from mobaide.linear.worktree import WorktreeCreationError

        async def _raise_worktree_error(*args, **kwargs):
            raise WorktreeCreationError(
                branch="feature/users-typed-branch",
                path="/tmp/some-worktree",
                stderr="fatal: could not create worktree: permission denied",
            )

        app.state.session_manager.create_session = _raise_worktree_error

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "claude_options": {
                    "worktree": True,
                    "branch_name": "feature/users-typed-branch",
                },
            },
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Could not create worktree" in detail
        assert "feature/users-typed-branch" in detail
        # The raw git stderr must survive so the user sees the actual cause
        # — not a generic "worktree failed" placeholder.
        assert "permission denied" in detail

    async def test_raw_backslash_filename_is_not_repr_escaped_in_detail(
        self, app, client, auth_headers,
    ):
        """Real regression guard for the 4-backslash symptom.

        The other path tests rely on normalization to produce POSIX filenames,
        so ``str(FileNotFoundError(..., "D:/x"))`` and ``f"...{exc.filename}"``
        both happen to produce no backslash — the ``"\\\\" not in resp.text``
        assertion in those tests is vacuously true. This test exercises the
        handler with a raw Windows-backslash filename that bypasses the
        normalizer entirely (injected via monkeypatch of ``create_session``),
        so a future regression that switches the detail back to ``str(exc)``
        would produce the 4-backslash wire form and fail this test.

        The bytes on the wire must be ``D:\\raw\\backslash\\dir`` (2 backslash
        characters per separator, i.e. one JSON-escape of one literal
        backslash) — NOT ``D:\\\\raw\\\\backslash\\\\dir`` (4 backslashes
        per separator), which is what ``str(exc)`` would emit because Python
        embeds ``repr(filename)`` with its own ``\\`` doubling before JSON
        encodes.
        """
        async def _raise_raw_backslash_fnf(*args, **kwargs):
            # Construct the exception with a filename that literally contains
            # backslash characters (raw-string r"" so Python doesn't interpret
            # them as escapes at the source level).
            raise FileNotFoundError(
                2, "No such file or directory", r"D:\raw\backslash\dir",
            )

        app.state.session_manager.create_session = _raise_raw_backslash_fnf

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # After JSON parsing, the filename in the detail is the raw string
        # with single backslashes — exactly what the mobile client should
        # render to the user, no extra escape layer.
        assert detail == r"Project path not found: D:\raw\backslash\dir"

        # And the raw wire payload must NOT contain the 4-backslash
        # sequence that ``str(FileNotFoundError)`` would emit via
        # ``repr(filename)``. The JSON source legitimately has 2 backslash
        # characters per separator (one JSON-escape per single literal
        # backslash); 4 indicates ``repr`` doubled them before JSON.
        assert "\\\\\\\\" not in resp.text
        # Positive check that one level of JSON escaping IS present (we are
        # reading the raw response bytes, so one literal backslash in the
        # detail string must appear as ``\\`` in the wire source).
        assert "D:\\\\raw\\\\backslash\\\\dir" in resp.text

    async def test_claude_cli_missing_returns_503_with_clean_detail(
        self, app, client, auth_headers,
    ):
        """When the Claude CLI is not installed (or not on PATH), the router
        must distinguish that from a missing project path: it returns 503
        Service Unavailable with a detail starting ``Claude CLI not found:``
        — never the misleading 400 ``Project path not found: claude``."""
        async def _raise_cli_missing(*args, **kwargs):
            raise ClaudeBinaryNotFoundError(
                searched_paths=[
                    "PATH:claude",
                    "/home/test/.npm-global/bin/claude",
                ]
            )

        app.state.session_manager.create_session = _raise_cli_missing

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={},
        )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail.startswith("Claude CLI not found:")
        assert "npm install -g @anthropic-ai/claude-code" in detail
        assert "MOBAIDE_CLAUDE_BIN" in detail
        assert "\\\\\\\\" not in resp.text


class TestCreateSessionCodex:
    """``POST /api/sessions`` with ``cli="codex"`` threads ``codex_options``
    through to a CODEX session and maps a missing Codex binary to a clean 503.
    """

    async def test_cli_codex_threads_options_and_kind(
        self, app, client, auth_headers,
    ):
        """The router must build a CODEX ``SessionConfig`` carrying the parsed
        ``codex_options`` (and no ``claude_options``), and surface ``kind`` as
        ``"codex"`` on the wire. ``create_session`` is captured so the test is
        independent of whether a real ``codex`` binary is installed."""
        from datetime import datetime, timezone

        from mobaide.core.models import (
            SessionInfo,
            SessionKind,
            SessionState,
        )

        captured: dict = {}

        class _FakeSession:
            def __init__(self, config):
                self.session_id = config.session_id
                self._config = config

            def to_info(self):
                return SessionInfo(
                    session_id=self.session_id,
                    state=SessionState.RUNNING,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    pid=123,
                    command=self._config.command,
                    cwd=self._config.cwd,
                    kind=self._config.kind,
                )

        async def _capture(config):
            captured["config"] = config
            return _FakeSession(config)

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "codex",
                "codex_options": {"model": "gpt-5.4-codex", "sandbox": "read-only"},
            },
        )

        assert resp.status_code == 201
        assert resp.json()["kind"] == "codex"

        cfg = captured["config"]
        assert cfg.kind is SessionKind.CODEX
        assert cfg.claude_options is None
        assert cfg.codex_options is not None
        assert cfg.codex_options.model == "gpt-5.4-codex"
        assert cfg.codex_options.sandbox == "read-only"

    async def test_codex_cli_missing_returns_503_with_clean_detail(
        self, app, client, auth_headers,
    ):
        """A missing Codex binary mirrors the Claude path: 503 with a detail
        starting ``codex CLI not found:`` and the Codex-specific install hint."""
        from mobaide.core import CodexBinaryNotFoundError

        async def _raise_cli_missing(*args, **kwargs):
            raise CodexBinaryNotFoundError(searched_paths=["PATH:codex"])

        app.state.session_manager.create_session = _raise_cli_missing

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cli": "codex", "codex_options": {}},
        )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail.startswith("Codex CLI not found:")
        assert "npm install -g @openai/codex" in detail
        assert "MOBAIDE_CODEX_BIN" in detail

    async def test_codex_unauthenticated_returns_409_with_auth_marker(
        self, app, client, auth_headers,
    ):
        """A Codex present-but-unauthenticated spawn (``CodexAuthRequiredError``)
        is client-actionable: 409 with a dict detail carrying
        ``codex_auth_required: true`` so the app shows a login prompt instead of
        the generic toast. 409 (not 401) so the Android AuthInterceptor's
        refresh-retry-on-401 can't swallow the marker. Distinct from the 503
        missing-binary case above (string detail), whose shape is asserted there."""
        from mobaide.core import CodexAuthRequiredError

        async def _raise_auth_required(*args, **kwargs):
            raise CodexAuthRequiredError()

        app.state.session_manager.create_session = _raise_auth_required

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cli": "codex", "codex_options": {}},
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["codex_auth_required"] is True
        assert isinstance(detail["detail"], str) and detail["detail"]


class TestCreateSessionAntigravity:
    """``POST /api/sessions`` with ``cli="antigravity"`` threads
    ``antigravity_options`` through to an ANTIGRAVITY session, maps a missing
    ``agy`` binary to a clean 503, and rejects the mutually exclusive
    ``skip_permissions`` + ``sandbox`` pair with a 422. No 409 auth case:
    ``agy`` has no pre-spawn auth probe, so the ``<cli>_auth_required`` path
    can never fire for Antigravity — an unauthenticated CLI renders its login
    prompt inside the PTY instead.
    """

    async def test_cli_antigravity_threads_options_and_kind(
        self, app, client, auth_headers,
    ):
        """The router must build an ANTIGRAVITY ``SessionConfig`` carrying the
        parsed ``antigravity_options`` (and no ``claude_options`` /
        ``codex_options``), and surface ``kind`` as ``"antigravity"`` on the
        wire. ``create_session`` is captured so the test is independent of
        whether a real ``agy`` binary is installed."""
        from datetime import datetime, timezone

        from mobaide.core.models import (
            SessionInfo,
            SessionKind,
            SessionState,
        )

        captured: dict = {}

        class _FakeSession:
            def __init__(self, config):
                self.session_id = config.session_id
                self._config = config

            def to_info(self):
                return SessionInfo(
                    session_id=self.session_id,
                    state=SessionState.RUNNING,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    pid=123,
                    command=self._config.command,
                    cwd=self._config.cwd,
                    kind=self._config.kind,
                )

        async def _capture(config):
            captured["config"] = config
            return _FakeSession(config)

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "antigravity",
                "antigravity_options": {
                    "model": "Gemini 3.1 Pro (Low)",
                    "sandbox": True,
                    "add_dirs": ["/extra/dir"],
                },
            },
        )

        assert resp.status_code == 201
        assert resp.json()["kind"] == "antigravity"

        cfg = captured["config"]
        assert cfg.kind is SessionKind.ANTIGRAVITY
        assert cfg.claude_options is None
        assert cfg.codex_options is None
        assert cfg.antigravity_options is not None
        assert cfg.antigravity_options.model == "Gemini 3.1 Pro (Low)"
        assert cfg.antigravity_options.sandbox is True
        assert cfg.antigravity_options.add_dirs == ("/extra/dir",)

    async def test_skip_permissions_with_sandbox_returns_422(
        self, app, client, auth_headers,
    ):
        """``skip_permissions`` and ``sandbox`` together are rejected by the
        ``AntigravityOptionsModel`` validator at the FastAPI body boundary —
        422, and no session is ever created."""
        captured: dict = {}

        async def _capture(config):
            captured["config"] = config

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "antigravity",
                "antigravity_options": {
                    "skip_permissions": True,
                    "sandbox": True,
                },
            },
        )

        assert resp.status_code == 422
        assert "mutually exclusive" in resp.text
        assert not captured


class TestCreateSessionOpenCode:
    """``POST /api/sessions`` with ``cli="opencode"`` threads
    ``opencode_options`` through to an OPENCODE session and caps the first
    prompt at the body boundary. No 409 auth case: OpenCode starts without an
    account, so the ``<cli>_auth_required`` path can never fire for it.
    """

    async def test_cli_opencode_threads_options_and_kind(
        self, app, client, auth_headers,
    ):
        """The router must build an OPENCODE ``SessionConfig`` carrying the
        parsed ``opencode_options`` (and no other CLI's block), and surface
        ``kind`` as ``"opencode"`` on the wire. ``create_session`` is captured
        so the test is independent of whether ``opencode`` is installed."""
        from datetime import datetime, timezone

        from mobaide.core.models import (
            SessionInfo,
            SessionKind,
            SessionState,
        )
        from mobaide.core.opencode_launcher import OpenCodeOptions

        captured: dict = {}

        class _FakeSession:
            def __init__(self, config):
                self.session_id = config.session_id
                self._config = config

            def to_info(self):
                return SessionInfo(
                    session_id=self.session_id,
                    state=SessionState.RUNNING,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    pid=123,
                    command=self._config.command,
                    cwd=self._config.cwd,
                    kind=self._config.kind,
                )

        async def _capture(config):
            captured["config"] = config
            return _FakeSession(config)

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "opencode",
                "opencode_options": {
                    "auto_approve": True,
                    "initial_prompt": "--auto",
                    "worktree": True,
                    "branch_name": "feature/x",
                    "base_branch": "origin/main",
                },
            },
        )

        assert resp.status_code == 201
        assert resp.json()["kind"] == "opencode"

        cfg = captured["config"]
        assert cfg.kind is SessionKind.OPENCODE
        assert cfg.claude_options is None
        assert cfg.codex_options is None
        assert cfg.antigravity_options is None
        assert cfg.opencode_options == OpenCodeOptions(
            auto_approve=True,
            initial_prompt="--auto",
            worktree=True,
            branch_name="feature/x",
            base_branch="origin/main",
        )

    async def test_a_prompt_over_the_cap_returns_422(
        self, app, client, auth_headers,
    ):
        """The first prompt becomes one argv element, so an over-long one is
        rejected at the FastAPI body boundary — 422, and no session is ever
        created."""
        from mobaide.core.opencode_launcher import INITIAL_PROMPT_MAX

        captured: dict = {}

        async def _capture(config):
            captured["config"] = config

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "opencode",
                "opencode_options": {"initial_prompt": "x" * (INITIAL_PROMPT_MAX + 1)},
            },
        )

        assert resp.status_code == 422
        assert not captured


class TestCreateSessionCopilot:
    """``POST /api/sessions`` with ``cli="copilot"`` threads ``copilot_options``
    through to a COPILOT session. No 409 auth case: Copilot has no sign-in
    probe, so a terminal session that is not signed in shows Copilot's own
    login prompt.
    """

    async def test_cli_copilot_threads_options_and_kind(
        self, app, client, auth_headers,
    ):
        """The router must build a COPILOT ``SessionConfig`` that carries the
        parsed ``copilot_options`` and no other options block, and report
        ``kind`` as ``"copilot"`` on the wire. ``create_session`` is captured,
        so the test does not need a real ``copilot`` binary."""
        from datetime import datetime, timezone

        from mobaide.core.copilot_launcher import CopilotOptions
        from mobaide.core.models import (
            SessionInfo,
            SessionKind,
            SessionState,
        )

        captured: dict = {}

        class _FakeSession:
            def __init__(self, config):
                self.session_id = config.session_id
                self._config = config

            def to_info(self):
                return SessionInfo(
                    session_id=self.session_id,
                    state=SessionState.RUNNING,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    pid=123,
                    command=self._config.command,
                    cwd=self._config.cwd,
                    kind=self._config.kind,
                )

        async def _capture(config):
            captured["config"] = config
            return _FakeSession(config)

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "copilot",
                "copilot_options": {
                    "allow_all": True,
                    "worktree": True,
                    "branch_name": "feature/x",
                    "base_branch": "origin/dev",
                },
            },
        )

        assert resp.status_code == 201
        assert resp.json()["kind"] == "copilot"

        cfg = captured["config"]
        assert cfg.kind is SessionKind.COPILOT
        assert cfg.claude_options is None
        assert cfg.codex_options is None
        assert cfg.antigravity_options is None
        assert cfg.copilot_options == CopilotOptions(
            allow_all=True,
            worktree=True,
            branch_name="feature/x",
            base_branch="origin/dev",
        )


class TestCreateSessionCursor:
    """``POST /api/sessions`` with ``cli="cursor"`` threads ``cursor_options``
    through to a CURSOR session. No 409 auth case: Cursor has no sign-in
    probe, so a terminal session that is not signed in shows Cursor's own
    login prompt.
    """

    async def test_cli_cursor_threads_options_and_kind(
        self, app, client, auth_headers,
    ):
        """The router must build a CURSOR ``SessionConfig`` that carries the
        parsed ``cursor_options`` and no other options block, and report
        ``kind`` as ``"cursor"`` on the wire. ``create_session`` is captured,
        so the test does not need a real ``cursor-agent`` binary."""
        from datetime import datetime, timezone

        from mobaide.core.cursor_launcher import CursorOptions
        from mobaide.core.models import (
            SessionInfo,
            SessionKind,
            SessionState,
        )

        captured: dict = {}

        class _FakeSession:
            def __init__(self, config):
                self.session_id = config.session_id
                self._config = config

            def to_info(self):
                return SessionInfo(
                    session_id=self.session_id,
                    state=SessionState.RUNNING,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    pid=123,
                    command=self._config.command,
                    cwd=self._config.cwd,
                    kind=self._config.kind,
                )

        async def _capture(config):
            captured["config"] = config
            return _FakeSession(config)

        app.state.session_manager.create_session = _capture

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "cli": "cursor",
                "cursor_options": {
                    "force": True,
                    "worktree": True,
                    "branch_name": "feature/x",
                    "base_branch": "origin/dev",
                },
            },
        )

        assert resp.status_code == 201
        assert resp.json()["kind"] == "cursor"

        cfg = captured["config"]
        assert cfg.kind is SessionKind.CURSOR
        assert cfg.claude_options is None
        assert cfg.codex_options is None
        assert cfg.antigravity_options is None
        assert cfg.opencode_options is None
        assert cfg.copilot_options is None
        assert cfg.cursor_options == CursorOptions(
            force=True,
            worktree=True,
            branch_name="feature/x",
            base_branch="origin/dev",
        )


class TestCreateSessionAdHoc:
    """``POST /api/sessions`` with ``project_name=None`` and a
    user-supplied ``cwd``: the new ad-hoc (custom-path) launch path.

    The router's :func:`_validate_adhoc_cwd` rejects empty / non-absolute /
    non-existent / non-directory paths with a clean 400 _before_ the
    request reaches ``ClaudeLauncher``. Sessions are still creatable when
    the cwd is a valid absolute directory (both Terminal and Claude
    modes), and the projects store stays untouched throughout.
    """

    async def test_terminal_mode_with_valid_cwd_returns_201(
        self, client, auth_headers, tmp_path,
    ):
        """``project_name=None``, ``cwd=<existing dir>``, no
        ``claude_options`` → raw Terminal session at the chosen cwd.
        Pre-existing behavior, but worth pinning as a regression guard:
        the new ad-hoc validation must not break this path."""
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cwd": str(tmp_path)},
        )
        assert resp.status_code == 201

    async def test_empty_cwd_returns_400_with_clean_detail(
        self, client, auth_headers,
    ):
        """An empty string ``cwd`` is rejected before the launcher runs
        — surfaces as 400, not 500, with a human-readable detail."""
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cwd": ""},
        )
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    async def test_relative_cwd_returns_400_with_clean_detail(
        self, client, auth_headers,
    ):
        """A relative path is rejected — agents have no defined notion
        of "current working directory" for custom-path launches; the
        client must send an absolute path."""
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cwd": "relative/path"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "absolute" in detail
        assert "relative/path" in detail

    async def test_nonexistent_cwd_returns_400_with_clean_detail(
        self, client, auth_headers, tmp_path,
    ):
        """An absolute-but-missing path is rejected by the existence
        check, not by ``check_cwd_exists`` further down —
        the 400 carries "does not exist" wording, distinguishing it
        from the "is not a directory" case below."""
        missing = tmp_path / "ghost"
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cwd": str(missing)},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "does not exist" in detail
        assert str(missing) in detail

    async def test_cwd_pointing_to_file_returns_400_with_clean_detail(
        self, client, auth_headers, tmp_path,
    ):
        """An existing-but-not-a-directory path (a file) is rejected —
        ``PtyBackend.spawn`` would otherwise emit a less readable
        platform-specific error."""
        a_file = tmp_path / "file.txt"
        a_file.write_text("hello")
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cwd": str(a_file)},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "not a directory" in detail
        assert str(a_file) in detail

    async def test_adhoc_validation_skipped_when_project_name_set(
        self, client, auth_headers, tmp_path,
    ):
        """When ``project_name`` is set the server derives ``cwd`` from
        the project — any supplied ``body.cwd`` is ignored, so the
        ad-hoc validator must NOT run. Concretely: a relative ``cwd``
        alongside a valid ``project_name`` must NOT trip the
        "absolute path" 400."""
        real_project = tmp_path / "prj"
        real_project.mkdir()
        create = await client.post(
            "/api/projects",
            headers=auth_headers,
            json={"name": "good-proj", "path": str(real_project)},
        )
        assert create.status_code == 201

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={
                "project_name": "good-proj",
                # Intentionally a relative path. The validator must be
                # skipped because ``project_name`` is present; the server
                # derives cwd from the project record instead.
                "cwd": "ignored/relative/path",
            },
        )
        assert resp.status_code == 201, (
            f"adhoc validator must not run when project_name is set; "
            f"got {resp.status_code}: {resp.text}"
        )

    async def test_adhoc_launch_does_not_create_project_in_store(
        self, app, client, auth_headers, tmp_path,
    ):
        """Beyond a clean 201, the ad-hoc path must NOT silently create
        a ``ProjectConfig`` in ``ProjectsStore`` keyed by the custom
        cwd — the whole point of the ad-hoc path is "run once, don't save"."""
        before_count = len(app.state.projects_store)
        before_names = sorted(app.state.projects_store.list_public(), key=lambda p: p["name"])
        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"cwd": str(tmp_path)},
        )
        assert resp.status_code == 201
        after_count = len(app.state.projects_store)
        after_names = sorted(app.state.projects_store.list_public(), key=lambda p: p["name"])
        assert after_count == before_count, (
            "ad-hoc launches must never mutate ProjectsStore — "
            f"len changed from {before_count} to {after_count}"
        )
        assert after_names == before_names, (
            "ad-hoc launches must never add an entry to ProjectsStore"
        )


class TestListSessions:

    async def test_list_after_create(self, client, auth_headers):
        await client.post("/api/sessions", headers=auth_headers, json={})
        resp = await client.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 1


class TestStopSession:
    async def test_stop_existing(self, client, auth_headers):
        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        sid = resp.json()["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 204

    async def test_stop_nonexistent_returns_404(self, client, auth_headers):
        resp = await client.delete("/api/sessions/nope", headers=auth_headers)
        assert resp.status_code == 404


class TestScrollback:
    async def test_scrollback_existing(self, client, auth_headers):
        resp = await client.post("/api/sessions", headers=auth_headers, json={})
        sid = resp.json()["session_id"]

        resp = await client.get(
            f"/api/sessions/{sid}/scrollback",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert isinstance(data["lines"], list)
        assert isinstance(data["total"], int)


class TestHealth:
    async def test_health_no_auth_required(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "sessions" in data
        assert "max_sessions" in data

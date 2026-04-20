"""Tests for session REST endpoints."""
from __future__ import annotations

import pytest


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

    async def test_existing_project_path_in_posix_form_launches(
        self, client, auth_headers, tmp_path,
    ):
        """Sanity: a project with a valid path (stored in POSIX form after
        normalization) starts a session successfully — confirms the new
        existence gate does not regress the happy path."""
        real_project = tmp_path / "proj"
        real_project.mkdir()

        create = await client.post(
            "/api/projects",
            headers=auth_headers,
            json={"name": "good", "path": str(real_project)},
        )
        assert create.status_code == 201

        resp = await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"project_name": "good"},
        )

        assert resp.status_code == 201

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


class TestListSessions:
    async def test_empty_list(self, client, auth_headers):
        resp = await client.get("/api/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

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

    async def test_scrollback_nonexistent_returns_404(self, client, auth_headers):
        resp = await client.get(
            "/api/sessions/nope/scrollback",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestHealth:
    async def test_health_no_auth_required(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "sessions" in data
        assert "max_sessions" in data

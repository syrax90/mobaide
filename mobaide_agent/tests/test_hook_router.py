"""Tests for the local-only ``/internal/hook`` endpoint.

The endpoint turns Claude Code ``http`` hooks into per-session attention
state. It is reachable only from loopback and only with the per-agent
``app.state.hook_token``; it routes by the ``session_id`` query param baked
into each session's hook URL.

These tests inject lightweight :class:`_FakeSession` stand-ins straight into
``app.state.session_manager._sessions`` (the same dict ``get_session`` reads)
so nothing spawns a real PTY — the contract under test is routing, guards,
and ``hook_event_name`` mapping, not the PTY lifecycle.
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient


class _FakeSession:
    """Records ``set_attention`` calls without owning a real PTY.

    ``app.state.session_manager.get_session`` only does a dict lookup, and the
    hook handler only calls ``set_attention`` on the result, so this is a
    faithful stand-in for the routing/guard behaviour under test.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.calls: list[tuple[str, str | None]] = []

    def set_attention(self, attention: str, exit_reason: str | None = None) -> None:
        self.calls.append((attention, exit_reason))

    async def stop(self, force: bool = False) -> None:
        # The ``app`` fixture's teardown runs ``session_manager.shutdown()``,
        # which calls ``stop(force=True)`` on every registered session. A
        # no-op keeps these injected stand-ins compatible with that path.
        return None


def _register(app, *session_ids: str) -> dict[str, _FakeSession]:
    sessions = {sid: _FakeSession(sid) for sid in session_ids}
    app.state.session_manager._sessions.update(sessions)
    return sessions


def _hook_token(app) -> str:
    return app.state.hook_token


async def _loopback_client(app, host: str = "127.0.0.1") -> AsyncClient:
    """An ``AsyncClient`` whose ASGI ``request.client.host`` is ``host``.

    httpx's ``ASGITransport`` defaults to ``("127.0.0.1", 123)`` which already
    passes the loopback guard; the explicit ``client`` arg lets the
    non-loopback test drive the 403 path.
    """
    transport = ASGITransport(app=app, client=(host, 12345))
    return AsyncClient(transport=transport, base_url="http://test")


class TestHookGuards:
    async def test_non_loopback_source_is_forbidden(self, app):
        _register(app, "s1")
        async with await _loopback_client(app, host="10.0.0.5") as c:
            resp = await c.post(
                "/internal/hook",
                params={"session_id": "s1", "token": _hook_token(app)},
                json={"hook_event_name": "Stop"},
            )
        assert resp.status_code == 403

    async def test_wrong_token_is_forbidden(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": "not-the-real-token"},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 403
        # The guard must reject before mutating any session state.
        assert sessions["s1"].calls == []

    async def test_empty_token_is_forbidden(self, app, client):
        _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": ""},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 403

    async def test_loopback_with_valid_token_is_authorized(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == [("turn_finished", None)]


class TestHookEventMapping:
    async def test_notification_sets_awaiting_permission(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "Notification"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == [("awaiting_permission", None)]

    async def test_stop_sets_turn_finished(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == [("turn_finished", None)]

    async def test_session_end_sets_ended_with_reason(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "SessionEnd", "reason": "clear"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == [("ended", "clear")]

    async def test_session_end_missing_reason_falls_back_to_other(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "SessionEnd"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == [("ended", "other")]

    async def test_session_end_empty_reason_falls_back_to_other(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "SessionEnd", "reason": ""},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == [("ended", "other")]


class TestHookNoOps:
    async def test_unknown_session_id_is_200_noop(self, app, client):
        # No session registered under this id — a late hook after cleanup is
        # normal and must not error.
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "ghost", "token": _hook_token(app)},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 200

    async def test_unknown_event_name_is_200_noop(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": "PreToolUse"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == []

    async def test_missing_event_name_is_200_noop(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"some": "other"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == []

    async def test_empty_session_id_is_200_noop(self, app, client):
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "", "token": _hook_token(app)},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 200

    async def test_non_json_body_does_not_500(self, app, client):
        # A malformed/empty body must be treated as an unknown event (no
        # hook_event_name) → 200 no-op, never a 500 that would block Claude.
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].calls == []


class TestMultiSessionCorrelation:
    """A hook addressed to session A must mutate A only.

    Session correlation is solved by baking the literal ``session_id`` into
    each session's hook URL (here, the query param). With two concurrent
    sessions registered, the endpoint must route purely on that id and never
    touch the other session.
    """

    async def test_hook_for_a_sets_attention_on_a_only(self, app, client):
        sessions = _register(app, "session-A", "session-B")

        resp = await client.post(
            "/internal/hook",
            params={"session_id": "session-A", "token": _hook_token(app)},
            json={"hook_event_name": "Notification"},
        )

        assert resp.status_code == 200
        assert sessions["session-A"].calls == [("awaiting_permission", None)]
        # The crux: session B must be completely untouched.
        assert sessions["session-B"].calls == []

    async def test_interleaved_hooks_route_independently(self, app, client):
        sessions = _register(app, "session-A", "session-B")
        token = _hook_token(app)

        await client.post(
            "/internal/hook",
            params={"session_id": "session-A", "token": token},
            json={"hook_event_name": "Stop"},
        )
        await client.post(
            "/internal/hook",
            params={"session_id": "session-B", "token": token},
            json={"hook_event_name": "SessionEnd", "reason": "logout"},
        )
        await client.post(
            "/internal/hook",
            params={"session_id": "session-A", "token": token},
            json={"hook_event_name": "Notification"},
        )

        assert sessions["session-A"].calls == [
            ("turn_finished", None),
            ("awaiting_permission", None),
        ]
        assert sessions["session-B"].calls == [("ended", "logout")]

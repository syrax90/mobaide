"""Tests for the local-only ``/internal/hook`` endpoint.

The endpoint hands Claude Code ``http`` hooks to the addressed session's
attention gate. It is reachable only from loopback and only with the per-agent
``app.state.hook_token``; it routes by the ``session_id`` query param and
classifies by the ``hook`` query param, both baked into each session's hook URL
at spawn.

Routing and classification are all this layer does — whether a hook becomes a
user-visible alert is the gate's decision, covered in ``test_attention_gate.py``.
These tests inject lightweight :class:`_FakeSession` stand-ins straight into
``app.state.session_manager._sessions`` (the same dict ``get_session`` reads) so
nothing spawns a real PTY.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from mobaide.core.attention_gate import HookKind


class _FakeSession:
    """Records ``on_claude_hook`` calls without owning a real PTY.

    ``app.state.session_manager.get_session`` only does a dict lookup, and the
    hook handler only calls ``on_claude_hook`` on the result, so this is a
    faithful stand-in for the routing/guard behaviour under test.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.hooks: list[tuple[HookKind, dict[str, Any]]] = []

    def on_claude_hook(self, kind: HookKind, payload: dict[str, Any]) -> None:
        self.hooks.append((kind, payload))

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


def _kinds(session: _FakeSession) -> list[HookKind]:
    return [kind for kind, _ in session.hooks]


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
                params={"session_id": "s1", "token": _hook_token(app), "hook": "stop"},
                json={"hook_event_name": "Stop"},
            )
        assert resp.status_code == 403

    async def test_wrong_token_is_forbidden(self, app, client):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={
                "session_id": "s1", "token": "not-the-real-token", "hook": "stop",
            },
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 403
        # The guard must reject before mutating any session state.
        assert sessions["s1"].hooks == []

    async def test_empty_token_is_forbidden(self, app, client):
        _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": "", "hook": "stop"},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 403


class TestHookKindRouting:
    @pytest.mark.parametrize("kind", list(HookKind))
    async def test_every_baked_kind_routes(self, app, client, kind):
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={
                "session_id": "s1", "token": _hook_token(app), "hook": kind.value,
            },
            json={"hook_event_name": "Notification"},
        )
        assert resp.status_code == 200
        assert _kinds(sessions["s1"]) == [kind]


    async def test_payload_reaches_the_session_verbatim(self, app, client):
        # The gate's whole decision is payload fields, so the handler must not
        # filter or reshape them.
        sessions = _register(app, "s1")
        body = {
            "hook_event_name": "Stop",
            "prompt_id": "p-1",
            "stop_hook_active": False,
            "background_tasks": [{"status": "running"}],
        }
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app), "hook": "stop"},
            json=body,
        )
        assert resp.status_code == 200
        assert sessions["s1"].hooks == [(HookKind.STOP, body)]

    @pytest.mark.parametrize(
        ("event_name", "kind"),
        [
            ("Notification", HookKind.PERMISSION),
            ("Stop", HookKind.STOP),
            ("UserPromptSubmit", HookKind.PROMPT_SUBMIT),
            ("SessionEnd", HookKind.END),
        ],
    )
    async def test_settings_without_the_param_fall_back_to_the_event_name(
        self, app, client, event_name, kind,
    ):
        # A session spawned before the agent upgraded has no ``hook`` param in
        # its URLs and must keep working for the rest of its life.
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            json={"hook_event_name": event_name},
        )
        assert resp.status_code == 200
        assert _kinds(sessions["s1"]) == [kind]


class TestHookNoOps:
    async def test_unknown_session_id_is_200_noop(self, app, client):
        # No session registered under this id — a late hook after cleanup is
        # normal and must not error.
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "ghost", "token": _hook_token(app), "hook": "stop"},
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 200

    async def test_unknown_hook_param_is_200_noop(self, app, client):
        # An unroutable kind must not silently fall back to the event name: a
        # future hook we deliberately do not handle would then be mis-alerted.
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={
                "session_id": "s1", "token": _hook_token(app), "hook": "subagent_stop",
            },
            json={"hook_event_name": "Stop"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].hooks == []


    async def test_non_json_body_does_not_500(self, app, client):
        # A malformed body must be treated as an unknown event (no
        # hook_event_name) → 200 no-op, never a 500 that would block Claude.
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app)},
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert sessions["s1"].hooks == []

    @pytest.mark.parametrize("body", [["Stop"], None])
    async def test_non_object_json_body_routes_with_an_empty_payload(
        self, app, client, body,
    ):
        # The kind comes from the URL, so a body that is valid JSON but not an
        # object still routes — normalized to ``{}`` rather than blowing up on
        # the first ``.get``.
        sessions = _register(app, "s1")
        resp = await client.post(
            "/internal/hook",
            params={"session_id": "s1", "token": _hook_token(app), "hook": "stop"},
            json=body,
        )
        assert resp.status_code == 200
        assert sessions["s1"].hooks == [(HookKind.STOP, {})]


class TestMultiSessionCorrelation:
    """A hook addressed to session A must reach A only.

    Session correlation is solved by baking the literal ``session_id`` into
    each session's hook URL (here, the query param). With two concurrent
    sessions registered, the endpoint must route purely on that id and never
    touch the other session.
    """

    async def test_hook_for_a_reaches_a_only(self, app, client):
        sessions = _register(app, "session-A", "session-B")

        resp = await client.post(
            "/internal/hook",
            params={
                "session_id": "session-A",
                "token": _hook_token(app),
                "hook": "permission",
            },
            json={"hook_event_name": "Notification"},
        )

        assert resp.status_code == 200
        assert _kinds(sessions["session-A"]) == [HookKind.PERMISSION]
        # The crux: session B must be completely untouched.
        assert sessions["session-B"].hooks == []

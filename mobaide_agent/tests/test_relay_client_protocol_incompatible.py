"""Tests for the ``ProtocolIncompatibleError`` self-heal path in
``mobaide.relay.client``.

Three behaviours land in this file:

* ``ProtocolIncompatibleError`` is a :class:`ConnectionError` subclass —
  the existing reconnect loop's ``except Exception`` keeps catching it
  via the new dedicated branch, and external callers that rely on the
  legacy ``ConnectionError`` contract are unaffected.
* ``_authenticate`` raises it (carrying the optional ``manifest_url``
  hint from the rejection payload) when the relay replies with
  ``msg_type == "incompatible"``.
* ``_connect_loop`` catches it, fires the registered callback with the
  hint, and clamps the next-sleep delay to ``reconnect_max_delay`` so
  the 60s-warning churn the user is seeing stops.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.relay.client import (
    ProtocolIncompatibleError,
    RelayClient,
)


@pytest.fixture
def client(
    relay_config,
    mock_session_manager,
    mock_event_bus,
    mock_broadcaster,
    mock_app_state,
):
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


class TestProtocolIncompatibleErrorShape:
    """``ProtocolIncompatibleError`` extends :class:`ConnectionError` so the
    pre-existing ``except ConnectionError`` / generic ``except Exception``
    branches in the reconnect loop keep catching it — only the dedicated
    handling is new."""

    def test_is_connection_error(self):
        assert issubclass(ProtocolIncompatibleError, ConnectionError)

    def test_message_includes_reason(self):
        exc = ProtocolIncompatibleError("Server speaks [3], agent sent 2")
        assert "Server speaks [3], agent sent 2" in str(exc)
        # Match the prefix used in the production message so the loop's
        # legacy string-matching guard doesn't accidentally start
        # mis-classifying these.
        assert str(exc).startswith("Relay rejected protocol version")

    def test_manifest_url_hint_defaults_none(self):
        exc = ProtocolIncompatibleError("any reason")
        assert exc.manifest_url_hint is None
        assert exc.reason == "any reason"

    def test_manifest_url_hint_stored(self):
        exc = ProtocolIncompatibleError(
            "any", manifest_url_hint="/api/v1/agent/manifest.json",
        )
        assert exc.manifest_url_hint == "/api/v1/agent/manifest.json"


class TestAuthenticateProtocolIncompatible:
    """Replaces the legacy string-match path: ``_authenticate`` must raise
    the dedicated subclass on ``incompatible``, carrying the optional
    server-supplied hint."""

    async def test_incompatible_raises_protocol_subclass(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "incompatible",
            "reason": "Server speaks [3], agent sent 2",
            "min_client_version": 3,
            "server_protocol_versions": [3],
        }))
        with pytest.raises(ProtocolIncompatibleError) as exc_info:
            await client._authenticate(ws)

        # The dedicated subclass must surface even though the legacy
        # tests catch it via ``ConnectionError``.
        assert isinstance(exc_info.value, ConnectionError)
        assert exc_info.value.reason == "Server speaks [3], agent sent 2"
        assert exc_info.value.manifest_url_hint is None

    async def test_incompatible_with_manifest_url_hint(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "incompatible",
            "reason": "outdated",
            "manifest_url": "/api/v1/agent/manifest.json",
        }))
        with pytest.raises(ProtocolIncompatibleError) as exc_info:
            await client._authenticate(ws)

        assert exc_info.value.manifest_url_hint == "/api/v1/agent/manifest.json"

    async def test_non_string_hint_is_dropped(self, client):
        # The hint must be a string path — anything else (None, dict,
        # int) is dropped so a misbehaving server can't smuggle a typed
        # payload through and trip downstream string-handling code.
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "incompatible",
            "reason": "outdated",
            "manifest_url": 42,
        }))
        with pytest.raises(ProtocolIncompatibleError) as exc_info:
            await client._authenticate(ws)

        assert exc_info.value.manifest_url_hint is None

    async def test_empty_string_hint_is_dropped(self, client):
        # ``""`` is technically a string but useless as a URL fragment.
        # Treat it as absent so callers can `if hint:` without an extra
        # `hint and hint.strip()` dance.
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "incompatible",
            "reason": "outdated",
            "manifest_url": "",
        }))
        with pytest.raises(ProtocolIncompatibleError) as exc_info:
            await client._authenticate(ws)

        assert exc_info.value.manifest_url_hint is None


class TestSetProtocolIncompatibleCallback:
    """The setter is a thin wrapper but the wire it exposes is part of
    the lifespan's contract — verify it stores the callback without
    invoking it."""

    def test_default_callback_is_none(self, client):
        assert client._on_protocol_incompatible is None

    def test_set_callback_stores_reference(self, client):
        async def cb(_hint):
            pass

        client.set_protocol_incompatible_callback(cb)
        assert client._on_protocol_incompatible is cb


class TestConnectLoopProtocolIncompatibleBranch:
    """When ``_run_connection`` raises :class:`ProtocolIncompatibleError`,
    the loop must (1) fire ``_on_protocol_incompatible(hint)``,
    (2) clamp the next-sleep delay to ``reconnect_max_delay`` instead of
    doubling from the base delay, and (3) survive callback exceptions
    without disturbing the reconnect rhythm."""

    async def test_callback_invoked_with_hint(self, client, monkeypatch):
        # The hint comes from the rejection payload via the subclass'
        # ``manifest_url_hint`` attribute and must reach the callback.
        captured: list[str | None] = []

        async def cb(hint):
            captured.append(hint)
            # Stop the loop so the test doesn't keep reconnecting.
            client._stopping = True

        client.set_protocol_incompatible_callback(cb)

        async def fail_with_incompat():
            raise ProtocolIncompatibleError(
                "outdated", manifest_url_hint="/manifest.json",
            )

        monkeypatch.setattr(client, "_run_connection", fail_with_incompat)
        # Drive the loop with zero sleep so it terminates instantly once
        # ``_stopping`` flips.
        monkeypatch.setattr(
            "mobaide.relay.client.asyncio.sleep", AsyncMock(),
        )

        await client._connect_loop()

        assert captured == ["/manifest.json"]

    async def test_callback_invoked_with_none_when_no_hint(
        self, client, monkeypatch,
    ):
        captured: list[str | None] = []

        async def cb(hint):
            captured.append(hint)
            client._stopping = True

        client.set_protocol_incompatible_callback(cb)

        async def fail_with_incompat():
            raise ProtocolIncompatibleError("outdated")  # no hint

        monkeypatch.setattr(client, "_run_connection", fail_with_incompat)
        monkeypatch.setattr(
            "mobaide.relay.client.asyncio.sleep", AsyncMock(),
        )

        await client._connect_loop()

        assert captured == [None]

    async def test_callback_exception_does_not_break_loop(
        self, client, monkeypatch,
    ):
        # If the callback explodes (e.g. HTTPS bootstrap itself hit a
        # transient DNS failure), the reconnect loop must keep going.
        # Otherwise a single hiccup would permanently stall the agent.
        cycle_count = {"n": 0}

        async def broken_cb(_hint):
            cycle_count["n"] += 1
            if cycle_count["n"] >= 2:
                client._stopping = True
            raise RuntimeError("HTTPS bootstrap blew up")

        client.set_protocol_incompatible_callback(broken_cb)

        async def fail_with_incompat():
            raise ProtocolIncompatibleError("outdated")

        monkeypatch.setattr(client, "_run_connection", fail_with_incompat)
        monkeypatch.setattr(
            "mobaide.relay.client.asyncio.sleep", AsyncMock(),
        )

        # Must not raise the inner RuntimeError.
        await client._connect_loop()

        # The loop completed at least one full iteration despite the
        # callback raising — proves the per-iteration ``except`` swallows
        # callback failures.
        assert cycle_count["n"] >= 1

    async def test_delay_clamps_to_reconnect_max(self, client, monkeypatch):
        # Without the dedicated branch, the loop would log
        # ``relay.reconnecting delay=1.0s`` on the first incompatible
        # rejection (base delay = 0.01s in the fixture, doubled to 0.02s
        # → still tiny). The new branch must instead clamp to
        # ``reconnect_max_delay`` (0.05s in the fixture). We capture the
        # delay passed to ``asyncio.sleep`` to prove the clamp fired.
        sleep_calls: list[float] = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)
            client._stopping = True  # exit after the first cycle's sleep

        async def fail_with_incompat():
            raise ProtocolIncompatibleError("outdated")

        # No callback wired (None branch); just verify the delay clamp.
        monkeypatch.setattr(client, "_run_connection", fail_with_incompat)
        monkeypatch.setattr(
            "mobaide.relay.client.asyncio.sleep", fake_sleep,
        )

        await client._connect_loop()

        assert sleep_calls, "loop never reached the sleep call"
        # The fixture sets reconnect_max_delay=0.05 — the clamp must
        # have set the first sleep to that value (not the base 0.01s).
        assert sleep_calls[0] == client._config.reconnect_max_delay

    async def test_no_callback_set_does_not_crash(self, client, monkeypatch):
        # The callback is optional — when the lifespan ran in a
        # developer checkout without a trust key, no updater is built
        # and ``_on_protocol_incompatible`` stays ``None``. The loop
        # must still clamp the delay and continue.
        assert client._on_protocol_incompatible is None

        sleep_calls: list[float] = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)
            client._stopping = True

        async def fail_with_incompat():
            raise ProtocolIncompatibleError("outdated")

        monkeypatch.setattr(client, "_run_connection", fail_with_incompat)
        monkeypatch.setattr(
            "mobaide.relay.client.asyncio.sleep", fake_sleep,
        )

        # Must not raise even though no callback is registered.
        await client._connect_loop()

        assert sleep_calls[0] == client._config.reconnect_max_delay

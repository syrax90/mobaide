"""RelayClient size-arbitration paths (proto-v8).

Covers the conn_id-keyed client registry the relay attach/detach lifecycle
drives, the CH_SESSION_IO_TARGETED handshake (and its legacy broadcast
fallback for a v7 relay that sends no conn_id), the targeted unknown-session
``stopped`` frame, and the tunnel-loss cleanup that unregisters every relay
token. Harness mirrors ``test_relay_client.py`` — conftest mock collaborators,
``_handle_control`` dispatch, AsyncMock websocket.
"""
from __future__ import annotations

import json
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.relay.client import (
    FRAME_OUTPUT,
    FRAME_REPLAY,
    FRAME_REPLAY_END,
    FRAME_STATE,
    FRAME_WINSIZE,
    RelayClient,
)
from mobaide.relay.envelope import (
    CH_SESSION_IO,
    CH_SESSION_IO_TARGETED,
    decode,
    split_targeted,
)
from mobaide.core.models import SessionState

WINSIZE_PAYLOAD = struct.pack(">HHH", 24, 80, 2)


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


def _mock_session(sid: str = "s1") -> MagicMock:
    session = MagicMock()
    session.session_id = sid
    session.state = SessionState.RUNNING
    session.pid = 42
    session.iter_ring_chunks = MagicMock(side_effect=lambda *a, **k: iter([]))
    session.winsize_payload = MagicMock(return_value=WINSIZE_PAYLOAD)
    return session


def _attach(sid: str, conn_id: str | None = None) -> bytes:
    ctrl: dict[str, str] = {"type": "mobile_session_attach", "session_id": sid}
    if conn_id is not None:
        ctrl["conn_id"] = conn_id
    return json.dumps(ctrl).encode()


def _detach(sid: str, conn_id: str | None = None) -> bytes:
    ctrl: dict[str, str] = {"type": "mobile_session_detach", "session_id": sid}
    if conn_id is not None:
        ctrl["conn_id"] = conn_id
    return json.dumps(ctrl).encode()


class TestTargetedAttachHandshake:
    async def test_attach_with_conn_id_wraps_every_frame_targeted(
        self, client, mock_session_manager
    ):
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()

        await client._handle_control(_attach("s1", conn_id="c1"), ws)

        # state -> winsize -> replay -> output(empty ring) -> replay_end,
        # each addressed at the one attaching client — a broadcast handshake
        # would wipe and re-replay every already-attached client's terminal.
        assert ws.send.await_count == 5
        inners = []
        for call in ws.send.call_args_list:
            channel, sid, payload = decode(call[0][0])
            assert channel == CH_SESSION_IO_TARGETED
            assert sid == "s1"
            cid, inner = split_targeted(payload)
            assert cid == "c1"
            inners.append(inner)

        assert inners[0][:1] == FRAME_STATE
        assert inners[1] == FRAME_WINSIZE + WINSIZE_PAYLOAD
        assert inners[2] == FRAME_REPLAY
        assert inners[3] == FRAME_OUTPUT
        assert inners[4] == FRAME_REPLAY_END

    async def test_attach_registers_conn_token_before_the_handshake(
        self, client, mock_session_manager
    ):
        # Register-before-send is what makes the handshake's own ``w`` frame
        # already count the newcomer. Attach now batches through
        # ``register_clients`` (a single-client batch here, no ``conn_ids``
        # peer list on this ctrl frame).
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        order: list[str] = []
        session.register_clients = MagicMock(
            side_effect=lambda toks: [order.append(f"register:{tok}") for tok in toks]
        )
        ws = AsyncMock()
        ws.send = AsyncMock(side_effect=lambda _data: order.append("send"))

        await client._handle_control(_attach("s1", conn_id="c1"), ws)

        assert order[0] == "register:relay:c1"
        assert order.count("send") == 5

    async def test_attach_without_conn_id_broadcasts_legacy_handshake(
        self, client, mock_session_manager
    ):
        # A v7 relay sends no conn_id: one legacy token stands in for its
        # clients and the handshake stays a CH_SESSION_IO broadcast — but the
        # ``w`` frame still rides it for v8 clients behind that relay.
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()

        await client._handle_control(_attach("s1"), ws)

        session.register_clients.assert_called_once_with({"relay:legacy:s1"})
        assert ws.send.await_count == 5
        decoded = [decode(c[0][0]) for c in ws.send.call_args_list]
        assert all(ch == CH_SESSION_IO for ch, _, _ in decoded)
        assert decoded[1][2] == FRAME_WINSIZE + WINSIZE_PAYLOAD

    async def test_oversized_conn_id_falls_back_to_legacy(
        self, client, mock_session_manager
    ):
        # A conn_id that cannot fit the 1-byte cid-length prefix is treated as
        # absent rather than crashing encode_targeted mid-handshake.
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()

        await client._handle_control(_attach("s1", conn_id="c" * 256), ws)

        session.register_clients.assert_called_once_with({"relay:legacy:s1"})
        channel, _, _ = decode(ws.send.call_args_list[0][0][0])
        assert channel == CH_SESSION_IO

    async def test_unknown_session_attach_targets_the_stopped_frame(
        self, client, mock_session_manager
    ):
        mock_session_manager.get_session = AsyncMock(return_value=None)
        ws = AsyncMock()

        await client._handle_control(_attach("ghost", conn_id="c9"), ws)

        ws.send.assert_awaited_once()
        channel, sid, payload = decode(ws.send.call_args[0][0])
        assert channel == CH_SESSION_IO_TARGETED
        assert sid == "ghost"
        cid, inner = split_targeted(payload)
        assert cid == "c9"
        assert inner[:1] == FRAME_STATE
        assert json.loads(inner[1:]) == {"status": "stopped"}


class TestDetachUnregisters:
    async def test_detach_unregisters_the_matching_token(
        self, client, mock_session_manager
    ):
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()
        await client._handle_control(_attach("s1", conn_id="c1"), ws)

        await client._handle_control(_detach("s1", conn_id="c1"), ws)

        session.unregister_client.assert_called_once_with("relay:c1")
        assert client._relay_attachments == {}

    async def test_detach_of_one_client_keeps_the_other_registered(
        self, client, mock_session_manager
    ):
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()
        await client._handle_control(_attach("s1", conn_id="c1"), ws)
        await client._handle_control(_attach("s1", conn_id="c2"), ws)

        await client._handle_control(_detach("s1", conn_id="c1"), ws)

        session.unregister_client.assert_called_once_with("relay:c1")
        assert "s1" in client._relay_attachments
        assert client._relay_attachments["s1"][1] == {"relay:c2"}

    async def test_detach_without_prior_attach_is_a_noop(
        self, client, mock_session_manager
    ):
        ws = AsyncMock()

        await client._handle_control(_detach("s1", conn_id="c1"), ws)

        ws.send.assert_not_called()
        assert client._relay_attachments == {}


class TestConnIdNormalization:
    async def test_multibyte_conn_id_over_the_byte_cap_normalizes_to_legacy_on_both_paths(
        self, client, mock_session_manager
    ):
        # 100 code points but 300 UTF-8 bytes: a cap measured in code points
        # would accept this id and ``encode_targeted`` would then reject it
        # mid-handshake. Attach and detach must agree it is absent — a
        # validation drift between them would register the token under one
        # name and unregister it under another, leaking a phantom client.
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()
        conn_id = "€" * 100  # 3 UTF-8 bytes each

        await client._handle_control(_attach("s1", conn_id=conn_id), ws)

        session.register_clients.assert_called_once_with({"relay:legacy:s1"})
        channel, _, _ = decode(ws.send.call_args_list[0][0][0])
        assert channel == CH_SESSION_IO

        await client._handle_control(_detach("s1", conn_id=conn_id), ws)

        session.unregister_client.assert_called_once_with("relay:legacy:s1")
        assert client._relay_attachments == {}


class TestTunnelLossCleanup:
    async def test_cancel_all_subtasks_unregisters_every_relay_token(
        self, client, mock_session_manager
    ):
        # Tunnel gone means every relay-attached client is gone: their tokens
        # must drop so surviving direct-WS clients see the count fall and a
        # solo one reclaims its grid.
        session = _mock_session()
        mock_session_manager.get_session = AsyncMock(return_value=session)
        ws = AsyncMock()
        await client._handle_control(_attach("s1", conn_id="c1"), ws)
        await client._handle_control(_attach("s1", conn_id="c2"), ws)

        client._cancel_all_subtasks()

        unregistered = {c.args[0] for c in session.unregister_client.call_args_list}
        assert unregistered == {"relay:c1", "relay:c2"}
        assert client._relay_attachments == {}

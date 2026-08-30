"""Lockstep test for the duplicated ``relay/envelope.py`` modules.

Both ``mobaide_agent.mobaide.relay.envelope`` and
``central_server.central_server.relay.envelope`` are intentionally duplicated
copies of the same wire-protocol codec. Any change that drifts the constants
between them is a wire-contract violation; this test runs in CI on every PR
to enforce equality and detect missed mirror updates.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


GOLDEN_PATH = Path(__file__).parent / "protocol_golden.json"


def _import_agent():
    from mobaide.relay import envelope as agent_envelope

    return agent_envelope


def _import_central():
    from central_server.relay import envelope as central_envelope

    return central_envelope


def test_protocol_version_constants_match() -> None:
    agent = _import_agent()
    central = _import_central()
    assert agent.PROTOCOL_VERSION == central.PROTOCOL_VERSION, (
        "PROTOCOL_VERSION drift between mobaide_agent and central_server "
        f"(agent={agent.PROTOCOL_VERSION}, central={central.PROTOCOL_VERSION}). "
        "Both envelope modules must move in lockstep."
    )
    assert agent.MIN_CLIENT_PROTOCOL_VERSION == central.MIN_CLIENT_PROTOCOL_VERSION
    assert agent.SUPPORTED_PROTOCOL_VERSIONS == central.SUPPORTED_PROTOCOL_VERSIONS


def test_channel_constants_match() -> None:
    agent = _import_agent()
    central = _import_central()
    for name in ("CH_SESSION_IO", "CH_SESSIONS_LIST", "CH_EVENTS", "CH_AUTOSCAN", "CH_CONTROL"):
        assert getattr(agent, name) == getattr(central, name), (
            f"channel constant {name} drifted between agent and central"
        )


def test_protocol_incompatible_close_code_matches() -> None:
    agent = _import_agent()
    central = _import_central()
    assert agent.PROTOCOL_INCOMPATIBLE_CLOSE_CODE == central.PROTOCOL_INCOMPATIBLE_CLOSE_CODE


def test_stream_desync_close_code_matches() -> None:
    agent = _import_agent()
    central = _import_central()
    assert agent.WS_CLOSE_STREAM_DESYNC == central.WS_CLOSE_STREAM_DESYNC


def test_golden_file_consistency() -> None:
    record = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    agent = _import_agent()
    assert record["protocol_version"] == agent.PROTOCOL_VERSION
    assert record["min_client_protocol_version"] == agent.MIN_CLIENT_PROTOCOL_VERSION
    assert record["supported_protocol_versions"] == list(agent.SUPPORTED_PROTOCOL_VERSIONS)
    assert record["close_codes"]["protocol_incompatible"] == agent.PROTOCOL_INCOMPATIBLE_CLOSE_CODE


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

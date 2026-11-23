"""Python arm of the agent settings golden fixtures shared with the iOS,
Android and web clients.

Every case survives ``model_validate`` → ``model_dump(mode="json")`` unchanged,
and the version golden's ``capabilities.agent_settings`` must match what the
agent advertises.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from mobaide.agent.settings.keep_awake import KeepAwakeState
from mobaide.agent.settings.models import (
    AGENT_SETTINGS_CAPABILITIES,
    AgentSettingsResponse,
    KeepAwakeSettingsRequest,
)

# Resolved from this file, never the CWD.
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "agent"
)
_MODELS = {
    "AgentSettingsResponse": AgentSettingsResponse,
    "KeepAwakeSettingsRequest": KeepAwakeSettingsRequest,
}


def _load(name: str) -> dict:
    doc = json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert doc["type"] == name
    assert doc["cases"], f"{name} has no cases — the golden would assert nothing"
    return doc


def _cases():
    for name, model in _MODELS.items():
        for case in _load(name)["cases"]:
            yield pytest.param(model, case, id=f"{name}:{case['name']}")


def _strip_nulls(value):
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_nulls(v) for v in value]
    return value


@pytest.mark.parametrize(("model", "case"), list(_cases()))
def test_wire_round_trips_through_model(model, case):
    decoded = model.model_validate(case["wire"])

    assert decoded.model_dump(mode="json") == case["wire"]


@pytest.mark.parametrize(("model", "case"), list(_cases()))
def test_decoded_canonical_is_wire_without_nulls(model, case):
    expected = _strip_nulls(case["wire"])

    if "decoded_canonical" in case:
        assert case["decoded_canonical"] == expected
    else:
        assert expected == case["wire"], "a wire with nulls needs decoded_canonical"


def test_settings_fixture_covers_every_state_the_agent_reports():
    states = {case["wire"]["keep_awake"]["state"] for case in _load("AgentSettingsResponse")["cases"]}

    assert set(get_args(KeepAwakeState)) <= states


def test_version_fixture_agent_settings_capability_is_what_the_agent_advertises():
    cases = {case["name"]: case for case in _load("VersionInfoResponse")["cases"]}

    assert cases["agent_settings_capability"]["wire"]["capabilities"]["agent_settings"] == (
        AGENT_SETTINGS_CAPABILITIES
    )

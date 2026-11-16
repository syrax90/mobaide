"""Python arm of the Herdr golden fixtures shared with the iOS, Android and
web clients.

This arm compares the agent model's dump, minus nulls, with
``decoded_canonical`` (or the wire minus nulls when a case has none).
``HerdrSnapshot:defaults_materialize`` stays client-only, and ``agent_id`` in
``HerdrAttention`` is stamped by the central server, so it is left out.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mobaide.herdr.models import HerdrAttention, HerdrSnapshot

# Resolved from this file, never the CWD: pytest is run from ``mobaide_agent/``.
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "agent"
)
_MODELS = {"HerdrSnapshot": HerdrSnapshot, "HerdrAttention": HerdrAttention}
_CLIENT_ONLY = {("HerdrSnapshot", "defaults_materialize")}
_SERVER_STAMPED = ("agent_id",)


def _load(name: str) -> dict:
    doc = json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert doc["type"] == name
    assert doc["cases"], f"{name} has no cases — the golden would assert nothing"
    return doc


def _cases():
    for name, model in _MODELS.items():
        for case in _load(name)["cases"]:
            if (name, case["name"]) not in _CLIENT_ONLY:
                yield pytest.param(model, case, id=f"{name}:{case['name']}")


def _strip_nulls(value):
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_nulls(v) for v in value]
    return value


@pytest.mark.parametrize(("model", "case"), list(_cases()))
def test_model_dump_matches_what_the_clients_decode(model, case):
    expected = dict(case.get("decoded_canonical", _strip_nulls(case["wire"])))
    for key in _SERVER_STAMPED:
        expected.pop(key, None)

    decoded = model.model_validate(case["wire"])

    assert _strip_nulls(decoded.model_dump(mode="json")) == expected


def test_the_client_only_case_is_a_wire_the_agent_never_sends():
    cases = {case["name"]: case for case in _load("HerdrSnapshot")["cases"]}

    with pytest.raises(ValidationError):
        HerdrSnapshot.model_validate(cases["defaults_materialize"]["wire"])

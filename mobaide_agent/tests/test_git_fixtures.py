"""Python arm of the git golden fixtures shared with the iOS, Android and
web clients.

The session git shapes (status, diff, the stage/discard/commit request and
response shapes and the fetch/pull/push job shapes) follow the strict rule:
every case must survive ``model_validate`` → ``model_dump(mode="json")``
unchanged, and ``decoded_canonical`` (the shape Swift's ``JSONEncoder``
re-emits, with nil optionals dropped) must equal the wire with null-valued
keys recursively stripped.

The project git shapes (the items list, delete batch and the branch create /
checkout bodies) predate that rule and were written the Swift way — a
minimal wire whose ``decoded_canonical`` materialises the model's defaults —
so their arm compares the dump minus nulls with ``decoded_canonical`` (or
the wire minus nulls when a case has none). ``BusyHolder`` /
``BusyByResponse`` stay client-only: their ``minimal`` case carries an
unknown ``kind`` on purpose, to prove the clients decode it as an open
string, which the agent's closed literal cannot.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mobaide.agent.git.changes_models import (
    GitCommitContextResponse,
    GitCommitRequest,
    GitCommitResponse,
    GitDiffResponse,
    GitDiscardRequest,
    GitJobResponse,
    GitMutationResponse,
    GitPathsRequest,
    GitPullRequest,
    GitPushRequest,
    GitStatusResponse,
)
from mobaide.agent.git.models import (
    GitBranchCreateRequest,
    GitBranchResponse,
    GitCheckoutRequest,
    GitDeleteItem,
    GitDeleteRequest,
    GitDeleteResponse,
    GitDeleteResultItem,
    GitItemRead,
    GitItemsResponse,
)

# Resolved from this file, never the CWD: pytest is run from ``mobaide_agent/``
# so ``import mobaide`` picks up these sources rather than the installed
# checkout, which leaves the repo root two levels up.
_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "agent"
)
_MODELS = {
    "GitStatusResponse": GitStatusResponse,
    "GitDiffResponse": GitDiffResponse,
    "GitPathsRequest": GitPathsRequest,
    "GitDiscardRequest": GitDiscardRequest,
    "GitMutationResponse": GitMutationResponse,
    "GitCommitRequest": GitCommitRequest,
    "GitCommitResponse": GitCommitResponse,
    "GitCommitContextResponse": GitCommitContextResponse,
    "GitPullRequest": GitPullRequest,
    "GitPushRequest": GitPushRequest,
    "GitJobResponse": GitJobResponse,
}
_MATERIALISING_MODELS = {
    "GitItem": GitItemRead,
    "GitItemsResponse": GitItemsResponse,
    "GitDeleteItem": GitDeleteItem,
    "GitDeleteRequest": GitDeleteRequest,
    "GitDeleteResultItem": GitDeleteResultItem,
    "GitDeleteResponse": GitDeleteResponse,
    "GitBranchCreateRequest": GitBranchCreateRequest,
    "GitCheckoutRequest": GitCheckoutRequest,
    "GitBranchResponse": GitBranchResponse,
}


def _cases(models=_MODELS):
    for name, model in models.items():
        doc = json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert doc["type"] == name
        assert doc["cases"], f"{name} has no cases — the golden would assert nothing"
        for case in doc["cases"]:
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


@pytest.mark.parametrize(("model", "case"), list(_cases(_MATERIALISING_MODELS)))
def test_materialising_wire_decodes_to_canonical(model, case):
    decoded = model.model_validate(case["wire"])

    expected = case.get("decoded_canonical", _strip_nulls(case["wire"]))
    assert _strip_nulls(decoded.model_dump(mode="json")) == expected

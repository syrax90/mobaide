"""Tests for the session request/response Pydantic models.

Validates that the API contract between the Android client and the Python
agent round-trips correctly for the ``skip_permissions`` field added to
``ClaudeOptionsModel``.
"""

from __future__ import annotations

import pytest

from mobaide.agent.sessions.models import ClaudeOptionsModel
from mobaide.core.claude_launcher import ClaudeOptions


def test_skip_permissions_defaults_to_true():
    """An empty options object must default skip_permissions to True — the
    mobile client cannot mediate approval dialogs, so permissions are
    skipped unless the user explicitly opts out."""
    opts = ClaudeOptionsModel()
    assert opts.skip_permissions is True


def test_skip_permissions_explicit_false_round_trips():
    """When the Android client sends ``skip_permissions: false``, the
    Pydantic model must parse and preserve it."""
    payload = {"skip_permissions": False, "plan_mode": True}
    opts = ClaudeOptionsModel.model_validate(payload)
    assert opts.skip_permissions is False
    assert opts.plan_mode is True
    # Round-trip through dict
    dumped = opts.model_dump()
    assert dumped["skip_permissions"] is False


def test_options_model_omits_skip_permissions_uses_default():
    """JSON body that does not mention skip_permissions at all must still
    deserialize with the True default intact."""
    payload = {"plan_mode": False, "no_memory": True}
    opts = ClaudeOptionsModel.model_validate(payload)
    assert opts.skip_permissions is True
    assert opts.no_memory is True


# ClaudeOptionsModel.to_options — API-DTO → domain-dataclass conversion.
#
# This method is the single source of truth for DTO→dataclass translation
# used by every relay and REST handler. A drift between ``ClaudeOptionsModel``
# fields and ``ClaudeOptions`` fields — or a silently-dropped field — would
# reintroduce the exact latent bug that let the relay ``POST /api/sessions``
# handler forget ``claude_options`` entirely. The round-trip test locks all
# nine fields down field-by-field so any future addition can't silently slip.


class TestToOptions:
    """``ClaudeOptionsModel.to_options()`` must return a real ``ClaudeOptions``
    dataclass carrying every field verbatim from the model."""

    def test_round_trip_all_fields_populated(self):
        """A fully-populated model round-trips every field into the frozen
        dataclass, returns an ``isinstance(ClaudeOptions, ...)``, and each
        value equals the source."""
        model = ClaudeOptionsModel(
            plan_mode=True,
            no_memory=True,
            skip_permissions=False,
            initial_prompt="hello world",
            worktree=True,
            branch_name="feature/abc",
            identifier="CLA-77",
            title="Some title",
            max_effort=False,
        )

        opts = model.to_options()

        assert isinstance(opts, ClaudeOptions)
        assert opts.plan_mode is True
        assert opts.no_memory is True
        assert opts.skip_permissions is False
        assert opts.initial_prompt == "hello world"
        assert opts.worktree is True
        assert opts.branch_name == "feature/abc"
        assert opts.identifier == "CLA-77"
        assert opts.title == "Some title"
        assert opts.max_effort is False

    def test_round_trip_defaults(self):
        """A bare model (all defaults) must round-trip to the dataclass
        defaults — proving the conversion path does not silently override
        falsy values with other falsy values (e.g. ``None`` vs ``False``)."""
        model = ClaudeOptionsModel()

        opts = model.to_options()

        assert isinstance(opts, ClaudeOptions)
        assert opts.plan_mode is False
        assert opts.no_memory is False
        assert opts.skip_permissions is True
        assert opts.initial_prompt is None
        assert opts.worktree is False
        assert opts.branch_name is None
        assert opts.identifier is None
        assert opts.title is None
        assert opts.max_effort is True

    def test_returned_dataclass_is_frozen(self):
        """``ClaudeOptions`` is ``@dataclass(frozen=True)``; mutating the
        result of ``to_options()`` must raise. This is a guard against a
        future refactor that accidentally drops the frozen decorator and
        reopens the door to mutable sharing between handlers."""
        import dataclasses

        opts = ClaudeOptionsModel(plan_mode=True).to_options()

        with pytest.raises(dataclasses.FrozenInstanceError):
            opts.plan_mode = False  # type: ignore[misc]

    def test_model_mutation_after_conversion_does_not_affect_dataclass(self):
        """Because ``to_options()`` builds a frozen dataclass from
        ``model_dump()``, mutating the source model afterwards must not
        bleed through into the previously-returned ``ClaudeOptions``."""
        model = ClaudeOptionsModel(plan_mode=False, no_memory=False)
        opts = model.to_options()

        model.plan_mode = True
        model.no_memory = True

        assert opts.plan_mode is False
        assert opts.no_memory is False

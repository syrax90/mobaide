"""Tests for the session request/response Pydantic models.

Validates that the API contract between the Android client and the Python
agent round-trips correctly for the ``skip_permissions`` field added to
``ClaudeOptionsModel``.
"""

from __future__ import annotations

from claude_mobile.agent.sessions.models import ClaudeOptionsModel


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

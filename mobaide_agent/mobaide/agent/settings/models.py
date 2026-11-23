"""Pydantic wire models of ``/api/agent/settings``.

Every key is always emitted (nulls included). ``keep_awake.state`` is an
open vocabulary: clients read a value they do not know as "not active" and
show ``message``.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# Advertised as ``capabilities.agent_settings`` on ``GET /api/version`` (both
# transports): the settings this agent build stores and applies. Whether
# this computer can actually stay awake is in ``keep_awake.state``.
AGENT_SETTINGS_CAPABILITIES: dict[str, object] = {
    "keep_awake": True,
}


class KeepAwakeStatus(BaseModel):
    """Whether the agent keeps this computer from idle-sleeping.

    ``enabled`` is the stored choice. ``state``: ``off`` (the choice is
    off), ``active`` (the computer stays awake now), ``failed`` (on, but the
    OS refused; ``message`` says why) or ``unsupported`` (this computer has
    no way to stay awake, whatever the choice; ``message`` says why).
    """

    enabled: bool
    state: str
    message: str | None


class AgentSettingsResponse(BaseModel):
    """``GET /api/agent/settings``, every settings write, and the
    ``agent_settings.changed`` event payload."""

    keep_awake: KeepAwakeStatus


class KeepAwakeSettingsRequest(BaseModel):
    """``PUT /api/agent/settings/keep_awake``: keep this computer awake, or
    let it sleep again, for every client of this agent."""

    enabled: bool = Field(strict=True)

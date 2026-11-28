"""Forking a chat: the parameters of ACP ``session/fork`` at a chosen answer.

ACP v1 has no fork point of its own yet (session cursors are unstable and
the pinned adapters announce them on ACP v2 only). claude-agent-acp and
codex-acp both read JetBrains' ``_meta.jetbrains.air.fork``: claude-agent-acp
copies the transcript up to that message, codex-acp up to the end of the turn
that holds it. A chat offers only the final answer of a finished turn as a
fork point, so both keep the same turns. When the message id does not match a
stored one, both fall back to the text fingerprint and its occurrence.

This module is the one place that knows the vendor field: clients name a
fork point by its Mobaide item id, never by the ACP id.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Below the clients' 30 s command timeout: the answer must reach them.
FORK_TIMEOUT = 20.0
# How long a fork that outlived FORK_TIMEOUT is still awaited, so that its copy
# can be closed in the source chat's process.
FORK_LATE_TIMEOUT = 180.0
STALE_POINT = "This answer can no longer be used as a fork point. Pick the last answer of a finished turn."


def answer_fingerprint(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def fork_point_meta(message_id: str, fingerprint: str, occurrence: int) -> dict[str, Any]:
    """``_meta`` of a ``session/fork`` that keeps the conversation up to the
    agent message ``message_id``. ``occurrence`` counts the agent messages
    with the same text up to this one, itself included (1-based)."""
    return {
        "jetbrains": {
            "air": {
                "fork": {
                    "version": 1,
                    "messageId": message_id,
                    "messageFingerprint": fingerprint,
                    "messageOccurrence": occurrence,
                }
            }
        }
    }

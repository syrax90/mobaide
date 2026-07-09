"""Attention reason → alert copy.

Port of the iOS ``AttentionContent.swift`` map (itself a port of Android's
``AttentionNotifier.contentFor``): a remote alert must read exactly like the
local notification the app would have posted foregrounded, so the strings
below mirror the clients byte-for-byte.
"""
from __future__ import annotations

FALLBACK_LABEL = "Claude session"


def _label(title: str | None, project_name: str | None) -> str:
    if title and title.strip():
        return title
    if project_name and project_name.strip():
        return project_name
    return FALLBACK_LABEL


def attention_content(
    reason: str | None,
    title: str | None,
    project_name: str | None,
    exit_reason: str | None,
) -> tuple[str, str] | None:
    """Return ``(title, body)`` for an attention reason, or ``None`` when the
    reason is absent/unrecognized — in which case no push is sent (parity
    with the clients posting no notification)."""
    label = _label(title, project_name)
    if reason == "awaiting_permission":
        return ("Permission needed", f"{label} is waiting for your approval")
    if reason == "awaiting_os_permission":
        return (
            "Action needed on your Mac",
            f"{label} is paused — your Mac is asking to allow file access. "
            "Approve it on the Mac to continue.",
        )
    if reason == "turn_finished":
        return ("Your move", f"{label} finished its turn — tap to continue")
    if reason == "ended":
        suffix = (
            f" ({exit_reason})" if exit_reason and exit_reason.strip() else ""
        )
        return (f"Session ended{suffix}", label)
    return None

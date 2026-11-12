"""Attention reason → alert copy.

Port of the iOS ``AttentionContent.swift`` map (itself a port of Android's
``AttentionNotifier.contentFor``): a remote alert must read exactly like the
local notification the app would have posted foregrounded, so the strings
below mirror the clients byte-for-byte.

One deliberate exception: a custom agent type is named "Agent session" here,
never by its user-chosen name. This text goes to Apple, and the privacy policy
names only the CLI's title and the project name as what an alert carries.
"""
from __future__ import annotations

# The label when the session has neither a title nor a project. An agent too
# old to send ``kind`` only alerted for Claude sessions.
FALLBACK_LABEL = "Claude session"

_KIND_FALLBACK_LABELS = {
    "claude": "Claude session",
    "codex": "Codex session",
    "antigravity": "Antigravity session",
    "copilot": "GitHub Copilot session",
    "auto_scan": "Auto Scan session",
    "shell": "Terminal session",
}


def _fallback_label(kind: str | None) -> str:
    if not kind:
        return FALLBACK_LABEL
    return _KIND_FALLBACK_LABELS.get(kind, "Agent session")


def _label(title: str | None, project_name: str | None, kind: str | None = None) -> str:
    if title and title.strip():
        return title
    if project_name and project_name.strip():
        return project_name
    return _fallback_label(kind)


def attention_content(
    reason: str | None,
    title: str | None,
    project_name: str | None,
    exit_reason: str | None,
    kind: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(title, body)`` for an attention reason, or ``None`` when the
    reason is absent/unrecognized — in which case no push is sent (parity
    with the clients posting no notification)."""
    label = _label(title, project_name, kind)
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
    if reason == "turn_failed":
        return ("Turn failed", f"{label} hit an error — tap to see what happened")
    if reason == "ended":
        suffix = (
            f" ({exit_reason})" if exit_reason and exit_reason.strip() else ""
        )
        return (f"Session ended{suffix}", label)
    return None

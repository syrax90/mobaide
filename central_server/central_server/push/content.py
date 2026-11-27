"""Attention reason → alert copy.

Port of the iOS ``AttentionContent.swift`` map (itself a port of Android's
``AttentionNotifier.contentFor``): a remote alert must read exactly like the
local notification the app would have posted foregrounded, so the strings
below mirror the clients byte-for-byte.

Two deliberate exceptions, both because this text goes to Apple and the
privacy policy names only the CLI's title, the workspace name and the kind of
action as what an alert carries: a custom agent type is named "Agent session"
here, never by its user-chosen name; and a chat permission alert names only
the kind of action, while the local alert also shows the request's title.
"""
from __future__ import annotations

# The label when the session has neither a title nor a workspace. An agent too
# old to send ``kind`` only alerted for Claude sessions.
FALLBACK_LABEL = "Claude session"

_KIND_FALLBACK_LABELS = {
    "claude": "Claude session",
    "codex": "Codex session",
    "antigravity": "Antigravity session",
    "opencode": "OpenCode session",
    "copilot": "GitHub Copilot session",
    "cursor": "Cursor session",
    "junie": "Junie session",
    "pi": "Pi session",
    "auto_scan": "Auto Scan session",
    "shell": "Terminal session",
}


# What a chat request a notification can answer asks for, by its ACP tool
# kind. The alert names only the kind of action, never the request's title:
# that can hold the command itself, and this text goes to Apple.
_PERMISSION_PHRASES = {
    "execute": "wants to run a command",
    "edit": "wants to edit files",
    "delete": "wants to delete files",
    "move": "wants to move files",
    "read": "wants to read files",
    "search": "wants to search files",
    "fetch": "wants to fetch from the web",
}
_PERMISSION_DEFAULT = "is waiting for your approval"


def _fallback_label(kind: str | None) -> str:
    if not kind:
        return FALLBACK_LABEL
    return _KIND_FALLBACK_LABELS.get(kind, "Agent session")


def _label(title: str | None, workspace_name: str | None, kind: str | None = None) -> str:
    if title and title.strip():
        return title
    if workspace_name and workspace_name.strip():
        return workspace_name
    return _fallback_label(kind)


def attention_content(
    reason: str | None,
    title: str | None,
    workspace_name: str | None,
    exit_reason: str | None,
    kind: str | None = None,
    request_tool_kind: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(title, body)`` for an attention reason, or ``None`` when the
    reason is absent/unrecognized — in which case no push is sent (parity
    with the clients posting no notification). ``request_tool_kind``: the
    kind of the chat request the alert can answer, if any."""
    label = _label(title, workspace_name, kind)
    if reason == "awaiting_permission":
        phrase = _PERMISSION_PHRASES.get(request_tool_kind or "", _PERMISSION_DEFAULT)
        return ("Permission needed", f"{label} {phrase}")
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

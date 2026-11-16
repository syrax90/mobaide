"""The coding agents that Mobaide can start in a herdr pane, and the names
herdr uses for the agents it finds."""
from __future__ import annotations

from dataclasses import dataclass

# Mobaide agent kind → display name (the same names the session list uses).
AGENT_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
    "opencode": "OpenCode",
    "copilot": "GitHub Copilot",
    "cursor": "Cursor",
    "pi": "Pi",
}

# Names herdr reports (a detection label, an OSC 7501 ``app`` or a program
# name) that mean one of Mobaide's kinds.
_ALIASES: dict[str, str] = {
    "agy": "antigravity",
    "claude-code": "claude",
    "claude_code": "claude",
    "cursor-agent": "cursor",
    "copilot-cli": "copilot",
    "github-copilot": "copilot",
}


@dataclass(frozen=True, slots=True)
class AgentChoice:
    """An agent whose CLI is installed on this computer."""

    id: str
    label: str
    binary: str


def normalize_agent(name: object) -> str | None:
    """Mobaide's kind for a herdr agent name; an unknown name stays as it is."""
    if not isinstance(name, str):
        return None
    cleaned = name.strip().lower()
    if not cleaned:
        return None
    return _ALIASES.get(cleaned, cleaned)

"""One alert when a chat and a terminal session show the same Codex turn.

The Codex TUI talks to Codex's shared app-server daemon, and a Codex chat can
join a thread that TUI has open (live attach). Both then see every turn on that
thread: the chat reports its own turn ending, and the TUI reports the same turn
through OSC 7501 — two sessions, two alerts for one event. Neither side knows
the other's thread, so the agent pairs them by folder: within
:data:`SHARED_TURN_WINDOW_SECONDS`, a chat and a terminal session of a
shared-thread CLI in the same folder that raise the same reason count once.

Only sessions of *different* UIs pair up. Two chats never double (each alerts
for its own turns only), and two terminals in one folder are far more often two
independent conversations than one thread opened twice. Callers also leave out
the sessions that cannot be on the shared daemon at all (a chat on a private
app server, a Codex TUI launched with ``-c`` overrides), so the folder match
only ever pairs sessions that could really be showing one thread.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

# CLIs whose chat and terminal sessions can share one live thread. Claude Code
# has no live attach, and Antigravity's presence lock keeps a conversation in a
# single process.
SHARED_THREAD_APPS = frozenset({"codex"})

# Both sessions learn of a turn's end from the same daemon notification, so
# their alerts land well under a second apart; the margin covers the terminal
# side's quiet window (``PROGRAM_STATUS_QUIET_SECONDS``).
SHARED_TURN_WINDOW_SECONDS = 6.0

# The reasons both sides of a shared thread report for the same event, by the
# event they describe. A turn's end is one event even when one side reports it
# as finished and the other as failed.
_EVENT_BY_REASON = {
    "turn_finished": "turn_end",
    "turn_failed": "turn_end",
    "awaiting_permission": "awaiting_permission",
}


@dataclass(slots=True)
class _Claim:
    session_id: str
    ui: str
    at: float


class SharedTurnDedupe:
    def __init__(
        self,
        *,
        window_seconds: float = SHARED_TURN_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._claims: dict[tuple[str, str, str], _Claim] = {}

    def admit(
        self, *, session_id: str, ui: str, app: str | None, cwd: str | None, reason: str,
    ) -> bool:
        """Whether this session may raise ``reason`` now.

        ``False`` means a session of the other UI already raised it for the
        same thread moments ago. ``cwd`` should be the session's real path.
        """
        event = _EVENT_BY_REASON.get(reason)
        if app not in SHARED_THREAD_APPS or not cwd or event is None:
            return True
        now = self._clock()
        for key in [k for k, c in self._claims.items() if now - c.at >= self._window]:
            del self._claims[key]
        key = (app, cwd, event)
        claim = self._claims.get(key)
        if claim is not None and claim.session_id != session_id and claim.ui != ui:
            return False
        self._claims[key] = _Claim(session_id, ui, now)
        return True


def real_cwd(cwd: str | None) -> str | None:
    """The folder key two sessions on one thread agree on."""
    if not cwd:
        return None
    try:
        return os.path.realpath(cwd)
    except (OSError, ValueError):
        return cwd

"""Forward one Junie CLI hook to the agent's ``/internal/hook`` endpoint.

Junie runs only ``command`` hooks, so a Junie terminal session's hook config
(see :func:`mobaide.core.hook_settings.write_junie_session_hooks`) runs this
script as ``python -I junie_hook_post.py <port> <session_id> <hook kind>``. It
reads the hook's JSON from stdin and posts a trimmed copy, with the per-agent
token from ``MOBAIDE_HOOK_TOKEN`` in a header, so the token is never in argv
or in the config file.

Stdlib only, and silent: Junie prepends a hook's non-JSON stdout to the next
prompt and shows a TUI error for a non-zero exit, so every failure is swallowed
and the script always exits 0 with no output.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

# Copies of ``hook_settings.HOOK_TOKEN_ENV`` / ``HOOK_TOKEN_HEADER`` and of the
# ``HookKind`` values: this script runs with ``-I`` and imports only the stdlib.
_TOKEN_ENV = "MOBAIDE_HOOK_TOKEN"
_TOKEN_HEADER = "X-Mobaide-Hook-Token"
_TIMEOUT_SECONDS = 2.0
_STDIN_LIMIT_BYTES = 4 << 20

# The payload fields the attention gate reads. The prompt, tool input and
# answer text are the user's work and can be large, so they are not sent.
_FORWARDED_FIELDS = (
    "hook_event_name", "session_id", "tool_name", "reason", "source",
    "stop_hook_active", "error",
)

# Junie's "ask the user" tool shows a form and waits; no PermissionRequest fires
# for it, so its PreToolUse is the only sign the session is blocked on the user.
_QUESTION_TOOL = "AskUserQuestion"

# Junie appends a ``CancelAgentEvent`` line to this file when the user
# interrupts a turn, which fires no hook (see ``interrupt_watch``).
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _events_path(junie_session_id: object) -> str | None:
    if not isinstance(junie_session_id, str) or not _SAFE_SESSION_ID.fullmatch(junie_session_id):
        return None
    home = os.environ.get("JUNIE_HOME") or os.path.join(os.path.expanduser("~"), ".junie")
    path = os.path.join(os.path.abspath(home), "sessions", junie_session_id, "events.jsonl")
    return path if os.path.isfile(path) else None


def build_request(port: str, session_id: str, kind: str, raw: bytes) -> tuple[str, bytes]:
    """The URL and body to post for one hook."""
    try:
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    body = {key: payload[key] for key in _FORWARDED_FIELDS if key in payload}
    if kind == "tool" and payload.get("tool_name") == _QUESTION_TOOL:
        kind = "permission"
    events = _events_path(payload.get("session_id"))
    if events is not None:
        body["transcript_path"] = events
    query = urllib.parse.urlencode({"session_id": session_id, "hook": kind})
    url = f"http://127.0.0.1:{int(port)}/internal/hook?{query}"
    return url, json.dumps(body).encode("utf-8")


def main(argv: list[str]) -> None:
    port, session_id, kind = argv[1:4]
    raw = sys.stdin.buffer.read(_STDIN_LIMIT_BYTES)
    url, body = build_request(port, session_id, kind, raw)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            _TOKEN_HEADER: os.environ.get(_TOKEN_ENV, ""),
        },
    )
    # No proxy: an ``HTTP_PROXY`` in the user's environment must not route a
    # loopback call (and the token) through another host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
        response.read(0)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except BaseException:
        pass
    os._exit(0)

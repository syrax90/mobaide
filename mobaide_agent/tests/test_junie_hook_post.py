"""Tests for the stdlib script a Junie command hook runs.

Junie prepends a hook's stdout to the next prompt and shows a TUI error for a
non-zero exit, so the script must stay silent and exit 0 whatever happens. It
runs here as Junie runs it, ``python -I <script> <port> <session> <kind>``,
against a loopback server in a thread; nothing leaves the host.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from mobaide.core import junie_hook_post
from mobaide.core.attention_gate import HookKind
from mobaide.core.hook_settings import HOOK_TOKEN_ENV, HOOK_TOKEN_HEADER, JUNIE_HOOK_POST_SCRIPT

_TOKEN = "tok-secret"


class _HookServer:
    """Records each POST and answers 401 to a wrong token, as the agent does."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
                length = int(self.headers.get("Content-Length", "0"))
                server.requests.append({
                    "query": parse_qs(urlsplit(self.path).query),
                    "token": self.headers.get(HOOK_TOKEN_HEADER),
                    "body": json.loads(self.rfile.read(length)),
                })
                ok = self.headers.get(HOOK_TOKEN_HEADER) == _TOKEN
                self.send_response(204 if ok else 401)
                self.end_headers()

            def log_message(self, *_args) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def hook_server() -> Iterator[_HookServer]:
    server = _HookServer()
    yield server
    server.close()


def _free_port() -> int:
    """A loopback port nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(port: int, stdin: bytes, *, token: str = _TOKEN, kind: str = "stop", **env: str):
    return subprocess.run(
        [sys.executable, "-I", JUNIE_HOOK_POST_SCRIPT, str(port), "sid-1", kind],
        input=stdin,
        capture_output=True,
        # Only what Windows needs to start Python; no host proxy settings.
        env={
            **{k: os.environ[k] for k in ("SYSTEMROOT",) if k in os.environ},
            HOOK_TOKEN_ENV: token, **env,
        },
        timeout=30,
    )


class TestPosterProcess:
    def test_posts_the_hook_with_the_token_in_a_header(self, hook_server) -> None:
        # A proxy in the user's env must not carry a loopback call, and the
        # token with it, to another host.
        dead_proxy = f"http://127.0.0.1:{_free_port()}"
        stdin = json.dumps({"hook_event_name": "Stop", "session_id": "j-1"}).encode()

        result = _run(hook_server.port, stdin, HTTP_PROXY=dead_proxy, http_proxy=dead_proxy)

        assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
        [request] = hook_server.requests
        assert request["query"] == {"session_id": ["sid-1"], "hook": ["stop"]}
        assert request["token"] == _TOKEN
        assert "token" not in request["query"]
        assert request["body"] == {"hook_event_name": "Stop", "session_id": "j-1"}

    @pytest.mark.parametrize(
        ("reachable", "token", "stdin"),
        [
            (False, _TOKEN, b'{"hook_event_name": "Stop"}'),
            (True, "wrong", b'{"hook_event_name": "Stop"}'),
            (True, _TOKEN, b""),
            (True, _TOKEN, b"not json {"),
            (True, _TOKEN, b"[1, 2]"),
        ],
        ids=["agent-unreachable", "wrong-token", "empty-stdin", "not-json", "not-an-object"],
    )
    def test_every_failure_is_silent_and_exits_zero(
        self, hook_server, reachable, token, stdin,
    ) -> None:
        port = hook_server.port if reachable else _free_port()

        result = _run(port, stdin, token=token)

        assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


class TestBuildRequest:
    def test_only_the_fields_the_gate_reads_are_forwarded(self) -> None:
        # The prompt and tool input are the user's work: they stay on the
        # Junie side.
        raw = json.dumps({
            "hook_event_name": "PreToolUse", "session_id": "j-1", "tool_name": "Bash",
            "prompt": "my secret plan", "tool_input": {"command": "rm -rf build"},
            "error": "rate_limit",
        }).encode()

        url, body = junie_hook_post.build_request("9100", "a&b c", "tool", raw)

        parts = urlsplit(url)
        assert (parts.hostname, parts.port, parts.path) == ("127.0.0.1", 9100, "/internal/hook")
        assert parse_qs(parts.query) == {"session_id": ["a&b c"], "hook": ["tool"]}
        assert json.loads(body) == {
            "hook_event_name": "PreToolUse", "session_id": "j-1", "tool_name": "Bash",
            "error": "rate_limit",
        }

    @pytest.mark.parametrize(
        ("tool_name", "hook"),
        [("AskUserQuestion", HookKind.PERMISSION.value), ("Bash", HookKind.TOOL.value)],
        ids=["question-blocks-on-the-user", "other-tool-is-a-heartbeat"],
    )
    def test_the_question_tool_is_a_permission(self, tool_name, hook) -> None:
        # Junie fires no PermissionRequest for its question form.
        raw = json.dumps({"tool_name": tool_name}).encode()

        url, _ = junie_hook_post.build_request("9100", "sid-1", HookKind.TOOL.value, raw)

        assert parse_qs(urlsplit(url).query)["hook"] == [hook]

    @pytest.mark.parametrize(
        ("junie_session_id", "write_events", "expected"),
        [("j-1", True, True), ("j-1", False, False), ("../j-1", True, False)],
        ids=["events-file-exists", "no-events-file", "path-traversal"],
    )
    def test_the_events_file_becomes_the_transcript_path(
        self, tmp_path, monkeypatch, junie_session_id, write_events, expected,
    ) -> None:
        # The interrupt watch reads Junie's CancelAgentEvent from this file.
        monkeypatch.setenv("JUNIE_HOME", str(tmp_path / "home"))
        events = tmp_path / "home" / "sessions" / junie_session_id / "events.jsonl"
        if write_events:
            events.parent.mkdir(parents=True)
            events.write_text("{}\n", encoding="utf-8")
        raw = json.dumps({"session_id": junie_session_id}).encode()

        _, body = junie_hook_post.build_request("9100", "sid-1", "stop", raw)

        transcript = json.loads(body).get("transcript_path")
        assert transcript == (str(events) if expected else None)


def test_the_poster_copies_match_the_agent_constants() -> None:
    # The poster runs with ``-I`` and cannot import them. A renamed env var or
    # header on one side would get every Junie hook a silent 401.
    assert junie_hook_post._TOKEN_ENV == HOOK_TOKEN_ENV
    assert junie_hook_post._TOKEN_HEADER == HOOK_TOKEN_HEADER

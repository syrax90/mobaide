"""Tests for the ``init-env`` and ``print-template`` CLI verbs.

The CLI lives at the bottom of ``mobaide.agent.config`` and is invoked
by install scripts via ``python -m mobaide.agent.config``. We drive the
``main(argv)`` entry point directly so each test runs in-process; the
verb itself returns an exit code rather than calling ``sys.exit`` mid-flow.
"""
from __future__ import annotations

import re
import sys

import pytest

from mobaide.agent._env_writer import env_path_for_prefix, parse_env_file
from mobaide.agent.config import main as config_main


_DEFAULT_RELAY_URL = "wss://mobaide.com"
_URLSAFE_30_PLUS = re.compile(r"^[A-Za-z0-9_\-]{30,}$")


def _run(argv: list[str]) -> int:
    return config_main(argv)


class TestInitEnvFreshWrite:
    def test_jwt_secret_key_is_urlsafe_30_plus_chars(self, tmp_path):
        _run(["init-env", "--prefix", str(tmp_path)])

        values = parse_env_file(env_path_for_prefix(tmp_path))
        secret = values["JWT_SECRET_KEY"]

        # secrets.token_urlsafe(32) yields ~43 chars; 30 is a safe lower bound.
        assert _URLSAFE_30_PLUS.match(secret), (
            f"JWT_SECRET_KEY did not look URL-safe / 30+ chars: {secret!r}"
        )

    def test_creates_parent_directory(self, tmp_path):
        prefix = tmp_path / "fresh_install_prefix"
        # Prefix directory does not exist yet — atomic_write must create it.
        assert not prefix.exists()

        rc = _run(["init-env", "--prefix", str(prefix)])

        assert rc == 0
        assert env_path_for_prefix(prefix).exists()


class TestInitEnvIdempotent:
    def test_second_run_without_force_is_noop(self, tmp_path, capsys):
        _run(["init-env", "--prefix", str(tmp_path)])
        target = env_path_for_prefix(tmp_path)
        first_content = target.read_bytes()
        first_mtime = target.stat().st_mtime_ns
        capsys.readouterr()  # discard first-run output

        rc = _run(["init-env", "--prefix", str(tmp_path)])

        assert rc == 0
        assert target.read_bytes() == first_content
        assert target.stat().st_mtime_ns == first_mtime
        captured = capsys.readouterr()
        # The "skip" message names the existing path so users know what to do.
        assert "already exists" in captured.out or "skipping" in captured.out


class TestInitEnvForce:
    def test_force_preserves_existing_jwt_secret(self, tmp_path):
        _run(["init-env", "--prefix", str(tmp_path)])
        original_secret = parse_env_file(
            env_path_for_prefix(tmp_path)
        )["JWT_SECRET_KEY"]

        rc = _run(["init-env", "--prefix", str(tmp_path), "--force"])

        assert rc == 0
        new_secret = parse_env_file(
            env_path_for_prefix(tmp_path)
        )["JWT_SECRET_KEY"]
        # Plan: by default --force keeps the JWT secret (rotating would
        # invalidate every live mobile JWT).
        assert new_secret == original_secret

    def test_force_preserves_relay_agent_id_and_token(self, tmp_path):
        _run(["init-env", "--prefix", str(tmp_path)])
        target = env_path_for_prefix(tmp_path)

        # Simulate a paired agent: hand-edit RELAY_AGENT_ID/TOKEN.
        text = target.read_text(encoding="utf-8")
        text = text.replace("RELAY_AGENT_ID=\n", "RELAY_AGENT_ID=paired-id\n")
        text = text.replace(
            "RELAY_AGENT_TOKEN=\n", "RELAY_AGENT_TOKEN=paired-tok\n"
        )
        target.write_text(text, encoding="utf-8")

        rc = _run(["init-env", "--prefix", str(tmp_path), "--force"])

        assert rc == 0
        values = parse_env_file(target)
        assert values["RELAY_AGENT_ID"] == "paired-id"
        assert values["RELAY_AGENT_TOKEN"] == "paired-tok"

    def test_force_preserves_user_set_relay_server_url(self, tmp_path):
        _run(["init-env", "--prefix", str(tmp_path)])
        target = env_path_for_prefix(tmp_path)

        text = target.read_text(encoding="utf-8")
        text = text.replace(
            f"RELAY_SERVER_URL={_DEFAULT_RELAY_URL}",
            "RELAY_SERVER_URL=wss://self-hosted.example",
        )
        target.write_text(text, encoding="utf-8")

        _run(["init-env", "--prefix", str(tmp_path), "--force"])

        values = parse_env_file(target)
        assert values["RELAY_SERVER_URL"] == "wss://self-hosted.example"


class TestPrintTemplate:
    def test_prints_template_to_stdout(self, capsys):
        rc = _run(["print-template"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "JWT_SECRET_KEY=" in captured.out
        # The wheel-packaged template ships with an uncommented relay URL.
        assert f"RELAY_SERVER_URL={_DEFAULT_RELAY_URL}" in captured.out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics only")
class TestInitEnvPermsPosix:
    def test_written_env_is_0o600(self, tmp_path):
        import stat

        _run(["init-env", "--prefix", str(tmp_path)])

        target = env_path_for_prefix(tmp_path)
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600


class TestInitEnvClaudeBin:
    """``--claude-bin`` writes ``MOBAIDE_CLAUDE_BIN`` so the agent
    always resolves ``claude.cmd`` even when PATH is non-standard.

    The Windows installer captures the user's
    ``%APPDATA%\\npm\\claude.cmd`` before UAC elevation (under the
    originating user's env), then re-invokes
    ``init-env --claude-bin <abs-path>`` from the elevated session.
    The flag must land an uncommented ``MOBAIDE_CLAUDE_BIN=<path>``
    line — the template ships the key commented out
    (``env.template:40``) so ``render_template``'s
    ``^\\s*#?\\s*KEY=`` matcher must uncomment it.
    """

    def test_writes_claude_bin_uncomments_template_line(self, tmp_path):
        # The template ships ``# MOBAIDE_CLAUDE_BIN=`` commented (see
        # env.template:40). ``render_template`` must rewrite that line as
        # an uncommented ``MOBAIDE_CLAUDE_BIN=<value>`` — not append a
        # duplicate alongside the commented form (which would then be
        # ambiguous: pydantic-settings reads the LAST key wins, and the
        # commented form would be skipped, but the duplicate is still
        # cosmetic noise that confuses operators auditing the file).
        _run([
            "init-env", "--prefix", str(tmp_path),
            "--claude-bin", "/abs/claude",
        ])

        text = env_path_for_prefix(tmp_path).read_text(encoding="utf-8")
        # Exactly one uncommented line.
        assert "\nMOBAIDE_CLAUDE_BIN=/abs/claude\n" in text
        # The commented placeholder is gone — not preserved alongside.
        assert "# MOBAIDE_CLAUDE_BIN=" not in text

    def test_force_preserves_existing_claude_bin(self, tmp_path):
        # Re-install path: ``init-env --force`` must preserve a
        # previously-set ``MOBAIDE_CLAUDE_BIN`` exactly like it preserves
        # ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` /
        # ``RELAY_SERVER_URL``. Operators who hand-edited the path
        # should never lose it on a stock re-run.
        _run([
            "init-env", "--prefix", str(tmp_path),
            "--claude-bin", "/originally/written",
        ])
        original = parse_env_file(
            env_path_for_prefix(tmp_path)
        )["MOBAIDE_CLAUDE_BIN"]
        assert original == "/originally/written"

        rc = _run(["init-env", "--prefix", str(tmp_path), "--force"])

        assert rc == 0
        preserved = parse_env_file(
            env_path_for_prefix(tmp_path)
        )["MOBAIDE_CLAUDE_BIN"]
        assert preserved == "/originally/written"

    def test_force_with_new_claude_bin_overrides_existing(self, tmp_path):
        # Re-install where the operator just moved the claude package to
        # a new prefix: passing ``--claude-bin /new`` to a ``--force``
        # run must win over the preserved old value. This is the
        # "operator manually updates and re-runs the installer" path —
        # the installer always discovers the current location and
        # passes it through.
        _run([
            "init-env", "--prefix", str(tmp_path),
            "--claude-bin", "/old/location/claude",
        ])

        rc = _run([
            "init-env", "--prefix", str(tmp_path), "--force",
            "--claude-bin", "/new/location/claude",
        ])

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["MOBAIDE_CLAUDE_BIN"] == "/new/location/claude"


class TestInitEnvOpenCodeBin:
    def test_force_preserves_a_hand_set_opencode_bin(self, tmp_path):
        # No installer flag writes ``MOBAIDE_OPENCODE_BIN``: the operator sets
        # it by hand, and a stock re-install must not drop it.
        _run(["init-env", "--prefix", str(tmp_path)])
        target = env_path_for_prefix(tmp_path)
        text = target.read_text(encoding="utf-8")
        assert "# MOBAIDE_OPENCODE_BIN=\n" in text
        target.write_text(
            text.replace(
                "# MOBAIDE_OPENCODE_BIN=\n", "MOBAIDE_OPENCODE_BIN=/opt/oc/bin/opencode\n",
            ),
            encoding="utf-8",
        )

        rc = _run(["init-env", "--prefix", str(tmp_path), "--force"])

        assert rc == 0
        rewritten = target.read_text(encoding="utf-8")
        assert parse_env_file(target)["MOBAIDE_OPENCODE_BIN"] == "/opt/oc/bin/opencode"
        assert "# MOBAIDE_OPENCODE_BIN=" not in rewritten


class TestInitEnvCopilotBin:
    """``MOBAIDE_COPILOT_BIN`` has no ``init-env`` flag: the Windows installer
    writes it with ``set-env`` (other users set it by hand) when Copilot is
    not on the agent's PATH. A
    re-install runs ``init-env --force``, which rewrites every templated
    line, so it must carry the value over, and the agent must read it back.
    """

    def test_force_preserves_existing_copilot_bin_and_the_agent_reads_it(
        self, tmp_path, monkeypatch
    ):
        from mobaide.agent.config import AgentConfig

        for var in (
            "JWT_SECRET_KEY",
            "MOBAIDE_COPILOT_BIN",
            "RELAY_SERVER_URL",
            "RELAY_AGENT_ID",
            "RELAY_AGENT_TOKEN",
            "PROJECTS",
        ):
            monkeypatch.delenv(var, raising=False)
        _run(["init-env", "--prefix", str(tmp_path)])
        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_COPILOT_BIN", "--value", "/opt/gh/bin/copilot",
        ])

        rc = _run(["init-env", "--prefix", str(tmp_path), "--force"])

        assert rc == 0
        target = env_path_for_prefix(tmp_path)
        text = target.read_text(encoding="utf-8")
        assert text.count("MOBAIDE_COPILOT_BIN=") == 1
        assert parse_env_file(target)["MOBAIDE_COPILOT_BIN"] == "/opt/gh/bin/copilot"
        assert AgentConfig.from_env(env_file=target).copilot_bin == "/opt/gh/bin/copilot"


class TestInitEnvCursorBin:
    """``MOBAIDE_CURSOR_BIN`` has no ``init-env`` flag: the Windows installer
    writes it with ``set-env`` (other users set it by hand) when Cursor is
    not on the agent's PATH. A
    re-install runs ``init-env --force``, which rewrites every templated
    line, so it must carry the value over, and the agent must read it back.
    """

    def test_force_preserves_existing_cursor_bin_and_the_agent_reads_it(
        self, tmp_path, monkeypatch
    ):
        from mobaide.agent.config import AgentConfig

        for var in (
            "JWT_SECRET_KEY",
            "MOBAIDE_CURSOR_BIN",
            "RELAY_SERVER_URL",
            "RELAY_AGENT_ID",
            "RELAY_AGENT_TOKEN",
            "PROJECTS",
        ):
            monkeypatch.delenv(var, raising=False)
        _run(["init-env", "--prefix", str(tmp_path)])
        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CURSOR_BIN", "--value", "/opt/cursor/bin/cursor-agent",
        ])

        rc = _run(["init-env", "--prefix", str(tmp_path), "--force"])

        assert rc == 0
        target = env_path_for_prefix(tmp_path)
        text = target.read_text(encoding="utf-8")
        assert text.count("MOBAIDE_CURSOR_BIN=") == 1
        assert parse_env_file(target)["MOBAIDE_CURSOR_BIN"] == "/opt/cursor/bin/cursor-agent"
        assert AgentConfig.from_env(env_file=target).cursor_bin == "/opt/cursor/bin/cursor-agent"

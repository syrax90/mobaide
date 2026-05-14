"""Tests for the ``set-env`` CLI verb.

Pin the contract for the surgical single-key updater introduced for
the Windows-service ``claude_bin`` backfill. ``install.ps1`` invokes
``set-env --key MOBAIDE_CLAUDE_BIN --value <abs-path>`` on a re-install
where ``.env`` already exists — preserving the operator's
``JWT_SECRET_KEY`` and ``RELAY_AGENT_*`` while landing a single new key.

``init-env --force`` could achieve the same outcome but it's heavier
(rewrites every templated line and gates on a list of preserved keys);
``set-env`` is the smallest operation per the plan's "Surgical set-env
only" policy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mobaide.agent._env_writer import env_path_for_prefix, parse_env_file
from mobaide.agent.config import main as config_main


def _run(argv: list[str]) -> int:
    return config_main(argv)


def _seed_env(tmp_path: Path, *, claude_bin: str | None = None) -> Path:
    """Lay down a .env via init-env (the realistic install precondition).

    Using ``init-env`` rather than hand-writing a file matches the
    actual production sequence: ``init-env`` runs on fresh install, then
    ``set-env`` lands a new key. Hand-writing would diverge from the
    template's exact comment / blank-line layout and cause irrelevant
    rendering differences to surface as test failures.
    """
    argv = ["init-env", "--prefix", str(tmp_path)]
    if claude_bin is not None:
        argv += ["--claude-bin", claude_bin]
    _run(argv)
    return env_path_for_prefix(tmp_path)


class TestSetEnvMissingEnv:
    def test_returns_1_when_env_missing(self, tmp_path, capsys):
        # No .env yet: set-env must exit non-zero with a clear message
        # pointing the operator at init-env. install.ps1 only invokes
        # set-env on the re-install (already-paired) path, so a missing
        # .env here is genuinely an installer bug worth surfacing loudly.
        assert not env_path_for_prefix(tmp_path).exists()

        rc = _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        assert rc == 1
        captured = capsys.readouterr()
        # The message names the missing file and points at init-env so
        # an operator pasting the command from docs can self-recover.
        assert "init-env" in captured.err

    def test_does_not_create_env_when_missing(self, tmp_path):
        # Sanity: a failing set-env must not write a partial / empty
        # file — fail-closed and let the operator run init-env. A
        # silently-created file would later confuse pydantic-settings
        # (missing JWT_SECRET_KEY raises RuntimeError) and a follow-up
        # init-env would either skip (no --force) or rewrite from
        # scratch, defeating the whole point of surgical mode.
        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        assert not env_path_for_prefix(tmp_path).exists()


class TestSetEnvInsertsNewKey:
    def test_inserts_claude_bin_when_template_has_only_comment(self, tmp_path):
        # The template ships ``# MOBAIDE_CLAUDE_BIN=`` commented
        # (env.template:40). init-env without ``--claude-bin`` preserves
        # the commented form. set-env must then uncomment it (the
        # render_template ``_KEY_LINE`` matches commented keys too —
        # ``r"^(?P<lead>\s*#?\s*)(?P<key>...)=..."``  at
        # _env_writer.py:20).
        target = _seed_env(tmp_path)
        # Sanity precondition: only the commented form is present.
        assert "MOBAIDE_CLAUDE_BIN" not in parse_env_file(target)

        rc = _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", r"C:\Users\u\AppData\Roaming\npm\claude.cmd",
        ])

        assert rc == 0
        values = parse_env_file(target)
        assert values["MOBAIDE_CLAUDE_BIN"] == (
            r"C:\Users\u\AppData\Roaming\npm\claude.cmd"
        )

    def test_uncomments_commented_template_line(self, tmp_path):
        # Stricter shape check: after set-env, the commented placeholder
        # must be GONE — not duplicated alongside a new uncommented
        # line. A regression to append-only behaviour would leave both
        # forms visible and confuse operators auditing the file.
        target = _seed_env(tmp_path)
        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        text = target.read_text(encoding="utf-8")
        assert "# MOBAIDE_CLAUDE_BIN=" not in text
        assert "\nMOBAIDE_CLAUDE_BIN=/abs/claude\n" in text


class TestSetEnvReplacesExistingKey:
    def test_replaces_in_place(self, tmp_path):
        # Re-running set-env over a previously-set value must update
        # the existing line in place (not append a second occurrence).
        # pydantic-settings would read the last occurrence and the
        # second one would win regardless — but a duplicated key is
        # cosmetic noise the operator should not have to clean up.
        target = _seed_env(tmp_path, claude_bin="/old/claude")
        assert parse_env_file(target)["MOBAIDE_CLAUDE_BIN"] == "/old/claude"

        rc = _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/new/claude",
        ])

        assert rc == 0
        text = target.read_text(encoding="utf-8")
        # Count uncommented occurrences — exactly one.
        lines = [
            line for line in text.splitlines()
            if line.startswith("MOBAIDE_CLAUDE_BIN=")
        ]
        assert lines == ["MOBAIDE_CLAUDE_BIN=/new/claude"]


class TestSetEnvPreservesOtherKeys:
    """The whole point of set-env vs init-env --force: nothing else moves."""

    def test_jwt_secret_unchanged(self, tmp_path):
        target = _seed_env(tmp_path)
        original = parse_env_file(target)
        original_jwt = original["JWT_SECRET_KEY"]
        assert original_jwt  # init-env always generates one

        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        after = parse_env_file(target)
        assert after["JWT_SECRET_KEY"] == original_jwt

    def test_relay_agent_credentials_unchanged(self, tmp_path):
        target = _seed_env(tmp_path)
        # Simulate a paired agent: hand-edit RELAY_AGENT_ID/TOKEN (the
        # same shape ``write_credentials_to_env`` would produce post-
        # pairing). set-env must leave both keys exactly as-is.
        text = target.read_text(encoding="utf-8")
        text = text.replace(
            "RELAY_AGENT_ID=\n", "RELAY_AGENT_ID=paired-id-xyz\n"
        )
        text = text.replace(
            "RELAY_AGENT_TOKEN=\n",
            "RELAY_AGENT_TOKEN=paired-tok-secret\n",
        )
        target.write_text(text, encoding="utf-8")

        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        values = parse_env_file(target)
        assert values["RELAY_AGENT_ID"] == "paired-id-xyz"
        assert values["RELAY_AGENT_TOKEN"] == "paired-tok-secret"

    def test_comments_and_blank_lines_preserved(self, tmp_path):
        target = _seed_env(tmp_path)
        original = target.read_text(encoding="utf-8")
        # Capture the structural lines (comments + blanks) so we can
        # verify they survive byte-for-byte. The header comment block
        # documents the file's origin; an operator-facing regression
        # that drops these would erode trust in the surgical contract.
        original_structural = [
            line for line in original.splitlines()
            if line.startswith("#") or line == ""
        ]

        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        after = target.read_text(encoding="utf-8")
        after_structural = [
            line for line in after.splitlines()
            if line.startswith("#") or line == ""
        ]
        # The only structural line allowed to disappear is the commented
        # ``# MOBAIDE_CLAUDE_BIN=`` placeholder (it gets uncommented
        # into a real key). Every other comment & blank must survive.
        assert "# MOBAIDE_CLAUDE_BIN=" in original_structural
        assert "# MOBAIDE_CLAUDE_BIN=" not in after_structural
        # Subtract just that one expected delta and compare the rest.
        expected_after = [
            line for line in original_structural
            if line != "# MOBAIDE_CLAUDE_BIN="
        ]
        assert after_structural == expected_after


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission semantics only"
)
class TestSetEnvPermsPosix:
    def test_written_env_remains_0o600(self, tmp_path):
        import stat

        target = _seed_env(tmp_path)
        # Sanity: init-env leaves 0o600.
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

        _run([
            "set-env", "--prefix", str(tmp_path),
            "--key", "MOBAIDE_CLAUDE_BIN",
            "--value", "/abs/claude",
        ])

        # After set-env: still 0o600. atomic_write + restrict_perms must
        # not regress the mode — otherwise a re-write would leak the
        # JWT secret to the local 'other' permission class.
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

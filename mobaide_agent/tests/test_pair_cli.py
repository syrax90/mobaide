"""Tests for the ``mobaide-agent pair`` CLI verb.

We drive ``mobaide.__main__._run_pair`` directly with a hand-built
``argparse.Namespace``. This avoids the subprocess overhead, keeps the
service-restart code path off (via ``--no-restart``), and lets each test
target a per-test temporary install prefix.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from mobaide.__main__ import _build_parser, _resolve_token, _run_pair
from mobaide.agent._env_writer import env_path_for_prefix, parse_env_file
from mobaide.agent.config import main as config_main


_DEFAULT_RELAY_URL = "wss://mobaide.com"


def _ns(**overrides: object) -> argparse.Namespace:
    """Build the Namespace shape ``_run_pair`` expects."""
    defaults: dict[str, object] = {
        "id": "agent-id-default",
        "token": "agent-token-default",
        "prefix": None,
        "no_restart": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _seed_env(tmp_path: Path) -> Path:
    """Seed a freshly-prepared ``.env`` (matches what install scripts produce)."""
    config_main(["init-env", "--prefix", str(tmp_path)])
    target = env_path_for_prefix(tmp_path)
    assert target.exists()
    return target


class TestPairRefusesIfMissing:
    def test_returns_2_and_does_not_create_env(self, tmp_path, capsys):
        # No init-env was run, so the .env does not exist yet.
        target = env_path_for_prefix(tmp_path)
        assert not target.exists()

        rc = _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        assert rc == 2
        # The .env file is NOT created — pair refuses to write a fresh one,
        # users must run init-env (or the installer) first.
        assert not target.exists()
        err = capsys.readouterr().err
        assert "does not exist" in err
        assert "init-env" in err


class TestPairWritesCredentials:
    def test_populates_id_and_token(self, tmp_path):
        _seed_env(tmp_path)

        rc = _run_pair(
            _ns(id="my-agent-id", token="my-agent-tok", prefix=str(tmp_path))
        )

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["RELAY_AGENT_ID"] == "my-agent-id"
        assert values["RELAY_AGENT_TOKEN"] == "my-agent-tok"

    def test_does_not_clobber_jwt_secret(self, tmp_path):
        _seed_env(tmp_path)
        original_secret = parse_env_file(
            env_path_for_prefix(tmp_path)
        )["JWT_SECRET_KEY"]

        _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        new_secret = parse_env_file(
            env_path_for_prefix(tmp_path)
        )["JWT_SECRET_KEY"]
        assert new_secret == original_secret

    def test_no_stale_tmp_left_behind(self, tmp_path):
        _seed_env(tmp_path)

        _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        env_dir = env_path_for_prefix(tmp_path).parent
        leftovers = [p for p in env_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestPairServerUrlBackfill:
    def test_populates_server_url_when_empty(self, tmp_path):
        # Simulate a hand-rolled .env with RELAY_SERVER_URL empty (bypassing
        # init-env). The pair flow must backfill the default.
        target = env_path_for_prefix(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "JWT_SECRET_KEY=existing-secret\n"
            "RELAY_SERVER_URL=\n"
            "RELAY_AGENT_ID=\n"
            "RELAY_AGENT_TOKEN=\n",
            encoding="utf-8",
        )

        rc = _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        assert rc == 0
        values = parse_env_file(target)
        assert values["RELAY_SERVER_URL"] == _DEFAULT_RELAY_URL
        assert values["RELAY_AGENT_ID"] == "A"
        assert values["RELAY_AGENT_TOKEN"] == "B"

    def test_preserves_user_set_server_url(self, tmp_path):
        target = env_path_for_prefix(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "JWT_SECRET_KEY=existing\n"
            "RELAY_SERVER_URL=wss://self-hosted.example\n"
            "RELAY_AGENT_ID=\n"
            "RELAY_AGENT_TOKEN=\n",
            encoding="utf-8",
        )

        _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        values = parse_env_file(target)
        # Pair must not overwrite a user's chosen self-hosted URL.
        assert values["RELAY_SERVER_URL"] == "wss://self-hosted.example"


class TestPairPreservesStructure:
    def test_preserves_comments_and_ordering(self, tmp_path):
        target = _seed_env(tmp_path)
        before = target.read_text(encoding="utf-8")

        _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        after = target.read_text(encoding="utf-8")

        # Every comment line in the seeded template must still be present.
        for line in before.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                assert line in after, (
                    f"Comment line lost during pair: {line!r}"
                )

        # Block ordering: JWT settings appear before the relay block.
        jwt_pos = after.index("JWT_ALGORITHM=")
        relay_pos = after.index("RELAY_AGENT_ID=")
        assert jwt_pos < relay_pos

    def test_only_relay_keys_change(self, tmp_path):
        target = _seed_env(tmp_path)
        before_values = parse_env_file(target)

        _run_pair(_ns(id="A", token="B", prefix=str(tmp_path)))

        after_values = parse_env_file(target)

        # Every non-relay-pairing key keeps its prior value.
        unchanged_keys = set(before_values) - {
            "RELAY_AGENT_ID", "RELAY_AGENT_TOKEN",
        }
        for key in unchanged_keys:
            assert after_values.get(key) == before_values.get(key), (
                f"Unrelated key changed during pair: {key}"
            )


class TestPairNoRestartFlag:
    def test_no_restart_flag_returns_0_and_skips_restart(self, tmp_path, capsys):
        _seed_env(tmp_path)

        rc = _run_pair(
            _ns(id="A", token="B", prefix=str(tmp_path), no_restart=True)
        )

        assert rc == 0
        out = capsys.readouterr().out
        # The "Skipping restart" hint is part of the user-facing UX.
        assert "Skipping restart" in out or "--no-restart" in out


class TestPairCredentialValidation:
    """Validate that --id / --token are rejected when they contain characters
    that would corrupt the .env on write or smuggle additional KEY=VALUE
    lines (``\\n``, ``\\r``, ``\\x00``) or shell metacharacters (``$``,
    backticks, ``;``, etc.). Exit code 4 is reserved for credential
    validation failures so the caller can distinguish from "missing file"
    (exit 2) or "unreadable token file" (exit 3).
    """

    @pytest.mark.parametrize(
        "bad_id",
        [
            "id\nwith-newline",
            "id\rwith-cr",
            "id\x00with-nul",
            "id with spaces",
            "id;rm -rf /",
            "id$(whoami)",
            "id`pwd`",
            "id&echo",
            "id|cat",
            'id"injected',
            "id'injected",
            "id<redirect",
            "id>redirect",
            "",  # empty string also fails the regex (1+ chars required).
        ],
    )
    def test_invalid_id_rejected_with_exit_4(
        self, tmp_path, capsys, bad_id,
    ):
        _seed_env(tmp_path)

        rc = _run_pair(
            _ns(id=bad_id, token="valid-token", prefix=str(tmp_path)),
        )

        assert rc == 4
        err = capsys.readouterr().err
        assert "--id" in err

    @pytest.mark.parametrize(
        "bad_token",
        [
            "token\nwith-newline",
            "token\rwith-cr",
            "token\x00with-nul",
            "token with spaces",
            "token;rm -rf /",
            "token$(whoami)",
            "token`pwd`",
            "token&echo",
            "token|cat",
            'token"injected',
            "token'injected",
        ],
    )
    def test_invalid_token_rejected_with_exit_4(
        self, tmp_path, capsys, bad_token,
    ):
        _seed_env(tmp_path)

        rc = _run_pair(
            _ns(id="valid-id", token=bad_token, prefix=str(tmp_path)),
        )

        assert rc == 4
        err = capsys.readouterr().err
        # The error message names "token" so the user knows which arg failed.
        assert "token" in err.lower()

    def test_invalid_id_does_not_modify_env_file(self, tmp_path):
        target = _seed_env(tmp_path)
        before = target.read_bytes()

        rc = _run_pair(
            _ns(id="bad\nid", token="ok-token", prefix=str(tmp_path)),
        )

        assert rc == 4
        # The .env must be untouched on validation failure.
        assert target.read_bytes() == before

    def test_invalid_token_does_not_modify_env_file(self, tmp_path):
        target = _seed_env(tmp_path)
        before = target.read_bytes()

        rc = _run_pair(
            _ns(id="ok-id", token="bad\ntoken", prefix=str(tmp_path)),
        )

        assert rc == 4
        assert target.read_bytes() == before

    @pytest.mark.parametrize(
        "ok_value",
        [
            "abc",
            "abc-DEF_123",
            "ABC.DEF",
            "agent:tag",
            "base64-token+pad/data=",
            # Long-but-bounded token (under 1024 chars).
            "x" * 256,
        ],
    )
    def test_legitimate_creds_accepted(self, tmp_path, ok_value):
        _seed_env(tmp_path)

        rc = _run_pair(
            _ns(id=ok_value, token=ok_value, prefix=str(tmp_path)),
        )

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["RELAY_AGENT_ID"] == ok_value
        assert values["RELAY_AGENT_TOKEN"] == ok_value


class TestPairTokenFile:
    """``--token-file`` reads the token from a file (whitespace stripped),
    avoiding argv leakage via ``/proc/<pid>/cmdline`` and shell history.
    """

    def test_reads_token_from_file(self, tmp_path):
        _seed_env(tmp_path)
        token_file = tmp_path / "token.txt"
        token_file.write_text("file-token-value", encoding="utf-8")
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=str(token_file),
            token_stdin=False,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["RELAY_AGENT_TOKEN"] == "file-token-value"

    def test_strips_trailing_whitespace_from_file(self, tmp_path):
        _seed_env(tmp_path)
        token_file = tmp_path / "token.txt"
        # Common case: ``echo "$TOKEN" > token.txt`` produces a trailing \n.
        token_file.write_text("file-token  \n  \n", encoding="utf-8")
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=str(token_file),
            token_stdin=False,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["RELAY_AGENT_TOKEN"] == "file-token"

    def test_missing_file_returns_exit_3(self, tmp_path, capsys):
        _seed_env(tmp_path)
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=str(tmp_path / "nonexistent.txt"),
            token_stdin=False,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        # Exit 3 is reserved for "token-file unreadable or empty".
        assert rc == 3

    def test_empty_file_returns_exit_3(self, tmp_path, capsys):
        _seed_env(tmp_path)
        token_file = tmp_path / "empty.txt"
        # File exists but is whitespace-only — strip() yields empty string.
        token_file.write_text("   \n\n  ", encoding="utf-8")
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=str(token_file),
            token_stdin=False,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        assert rc == 3


class TestPairTokenStdin:
    """``--token-stdin`` reads the token from stdin (whitespace stripped)."""

    def test_reads_token_from_stdin(self, tmp_path, monkeypatch):
        _seed_env(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO("stdin-token-value\n"))
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=None,
            token_stdin=True,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["RELAY_AGENT_TOKEN"] == "stdin-token-value"

    def test_strips_whitespace_from_stdin(self, tmp_path, monkeypatch):
        _seed_env(tmp_path)
        monkeypatch.setattr(
            "sys.stdin", io.StringIO("  padded-token  \n\n"),
        )
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=None,
            token_stdin=True,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        assert rc == 0
        values = parse_env_file(env_path_for_prefix(tmp_path))
        assert values["RELAY_AGENT_TOKEN"] == "padded-token"

    def test_empty_stdin_returns_exit_3(self, tmp_path, monkeypatch):
        _seed_env(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
        ns = argparse.Namespace(
            id="A",
            token=None,
            token_file=None,
            token_stdin=True,
            prefix=str(tmp_path),
            no_restart=True,
        )

        rc = _run_pair(ns)

        # Empty stdin (after strip) → token absent → exit 3.
        assert rc == 3


class TestPairTokenSourceMutualExclusion:
    """The argparse mutually-exclusive group enforces exactly one token
    source. Verified at the parser level, not in ``_run_pair`` itself.
    """

    def test_token_and_token_file_rejected(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["pair", "--id", "A", "--token", "x", "--token-file", "y"],
            )

    def test_token_and_token_stdin_rejected(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["pair", "--id", "A", "--token", "x", "--token-stdin"],
            )

    def test_token_file_and_token_stdin_rejected(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["pair", "--id", "A", "--token-file", "y", "--token-stdin"],
            )

    def test_no_token_source_rejected(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            # The mutually-exclusive group is required=True.
            parser.parse_args(["pair", "--id", "A"])

    @pytest.mark.parametrize(
        "argv_extra",
        [
            ["--token", "tok"],
            ["--token-file", "/tmp/whatever"],
            ["--token-stdin"],
        ],
    )
    def test_each_individual_source_accepted(self, argv_extra):
        parser = _build_parser()
        args = parser.parse_args(["pair", "--id", "A", *argv_extra])
        assert args.id == "A"


class TestResolveTokenHelper:
    """Direct tests for the ``_resolve_token`` helper, isolated from
    file-existence and other ``_run_pair`` side effects.
    """

    def test_token_argv_takes_precedence_over_file(self, tmp_path, monkeypatch):
        # When ``--token`` is set, ``--token-file`` and ``--token-stdin``
        # are not consulted (argparse already enforces mutual exclusion,
        # but the helper's source-priority must be deterministic too).
        ns = argparse.Namespace(
            token="argv-token",
            token_file=str(tmp_path / "ignored.txt"),
            token_stdin=False,
        )

        result = _resolve_token(ns)

        assert result == "argv-token"

    def test_token_file_strip_removes_outer_whitespace(self, tmp_path):
        token_file = tmp_path / "tok.txt"
        token_file.write_text("\t  inner-token  \r\n", encoding="utf-8")
        ns = argparse.Namespace(
            token=None, token_file=str(token_file), token_stdin=False,
        )

        result = _resolve_token(ns)

        assert result == "inner-token"

    def test_token_file_unreadable_returns_none(self, tmp_path, capsys):
        ns = argparse.Namespace(
            token=None,
            token_file=str(tmp_path / "missing.txt"),
            token_stdin=False,
        )

        result = _resolve_token(ns)

        assert result is None
        err = capsys.readouterr().err
        assert "--token-file" in err

    def test_no_source_returns_none(self):
        ns = argparse.Namespace(
            token=None, token_file=None, token_stdin=False,
        )

        result = _resolve_token(ns)

        assert result is None

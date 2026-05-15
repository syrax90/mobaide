"""Tests for ``mobaide.agent._env_writer``.

Covers the four primitives shared by the install-time ``init-env`` verb
and the end-user ``mobaide-agent pair`` verb: template rendering, env-file
parsing, atomic write, and the best-effort permission tightener.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mobaide.agent import _env_writer
from mobaide.agent._env_writer import (
    atomic_write,
    env_path_for_prefix,
    parse_env_file,
    render_template,
    restrict_perms,
)


_BASIC_TEMPLATE = """\
# Header comment
JWT_SECRET_KEY=

# JWT block
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=0

# Relay block
RELAY_SERVER_URL=wss://default.example
RELAY_AGENT_ID=
RELAY_AGENT_TOKEN=
# RELAY_HEARTBEAT_INTERVAL=15
"""


class TestRenderTemplateSubstitution:
    def test_substitutes_named_keys_only(self):
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={"JWT_SECRET_KEY": "abc123", "RELAY_AGENT_ID": "agent-1"},
        )

        assert "JWT_SECRET_KEY=abc123\n" in rendered
        assert "RELAY_AGENT_ID=agent-1\n" in rendered
        # Untouched keys must keep their original value verbatim.
        assert "JWT_ALGORITHM=HS256\n" in rendered
        assert "RELAY_SERVER_URL=wss://default.example\n" in rendered
        assert "RELAY_AGENT_TOKEN=\n" in rendered

    def test_uncomments_commented_key_when_in_overrides(self):
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={"RELAY_HEARTBEAT_INTERVAL": "30"},
        )

        assert "RELAY_HEARTBEAT_INTERVAL=30" in rendered
        # The original commented form is replaced, not duplicated.
        assert "# RELAY_HEARTBEAT_INTERVAL=15" not in rendered

    def test_no_overrides_returns_template_unchanged(self):
        rendered = render_template(_BASIC_TEMPLATE, overrides={})

        # Only difference allowed is a single trailing newline normalization.
        assert rendered.rstrip("\n") == _BASIC_TEMPLATE.rstrip("\n")

    def test_overrides_default_to_empty_dict(self):
        rendered = render_template(_BASIC_TEMPLATE)

        assert rendered.rstrip("\n") == _BASIC_TEMPLATE.rstrip("\n")


class TestRenderTemplatePreservation:
    def test_preserves_comments_blank_lines_and_ordering(self):
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={"JWT_SECRET_KEY": "x"},
        )
        lines = rendered.splitlines()

        # Comments and blank lines survive unchanged in the same positions.
        assert lines[0] == "# Header comment"
        assert lines[2] == ""
        assert lines[3] == "# JWT block"
        # Ordering: JWT block before Relay block.
        jwt_idx = lines.index("JWT_ALGORITHM=HS256")
        relay_idx = lines.index("RELAY_SERVER_URL=wss://default.example")
        assert jwt_idx < relay_idx

    def test_output_always_ends_with_newline(self):
        no_trailing = "JWT_SECRET_KEY="
        rendered = render_template(no_trailing, overrides={"JWT_SECRET_KEY": "x"})
        assert rendered.endswith("\n")


class TestRenderTemplateExtras:
    def test_unknown_keys_appended_at_end(self):
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={"BRAND_NEW_KEY": "value"},
        )

        # The new key lives after the last template line.
        assert "BRAND_NEW_KEY=value\n" in rendered
        last_template_line = "# RELAY_HEARTBEAT_INTERVAL=15"
        assert rendered.index(last_template_line) < rendered.index(
            "BRAND_NEW_KEY=value"
        )

    def test_extras_separated_by_blank_line_when_template_lacks_trailing_blank(self):
        template = "FOO=bar"
        rendered = render_template(template, overrides={"BAZ": "qux"})

        lines = rendered.splitlines()
        assert lines == ["FOO=bar", "", "BAZ=qux"]

    def test_extras_not_double_added_when_key_present_in_template(self):
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={"JWT_SECRET_KEY": "x"},
        )

        # JWT_SECRET_KEY appears exactly once after rendering.
        assert rendered.count("JWT_SECRET_KEY=") == 1


class TestParseEnvFile:
    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = parse_env_file(tmp_path / "does-not-exist.env")
        assert result == {}

    def test_reads_basic_key_value_pairs(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "JWT_SECRET_KEY=topsecret\nRELAY_AGENT_ID=a-1\n",
            encoding="utf-8",
        )

        result = parse_env_file(env_file)

        assert result == {"JWT_SECRET_KEY": "topsecret", "RELAY_AGENT_ID": "a-1"}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# A comment\n"
            "\n"
            "FOO=bar\n"
            "  # indented comment\n"
            "\n",
            encoding="utf-8",
        )

        result = parse_env_file(env_file)

        assert result == {"FOO": "bar"}

    def test_strips_wrapping_quotes_from_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            'DOUBLE="hello"\n'
            "SINGLE='world'\n"
            'INNER="contains \'quote\'"\n',
            encoding="utf-8",
        )

        result = parse_env_file(env_file)

        assert result["DOUBLE"] == "hello"
        assert result["SINGLE"] == "world"
        assert result["INNER"] == "contains 'quote'"

    def test_drops_unparseable_lines_silently(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "VALID=ok\n"
            "no_equals_here\n"
            "=missing_key\n",
            encoding="utf-8",
        )

        result = parse_env_file(env_file)

        assert result == {"VALID": "ok"}


class TestAtomicWrite:
    def test_writes_content_to_target(self, tmp_path):
        target = tmp_path / "subdir" / ".env"
        atomic_write(target, "hello=world\n")

        assert target.read_text(encoding="utf-8") == "hello=world\n"

    def test_creates_parent_directory_if_missing(self, tmp_path):
        target = tmp_path / "deeper" / "still" / ".env"
        atomic_write(target, "x=y\n")

        assert target.parent.is_dir()
        assert target.exists()

    def test_no_stale_tmp_file_left_on_success(self, tmp_path):
        target = tmp_path / ".env"
        atomic_write(target, "hello=world\n")

        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
        # And the only file present is the final target.
        assert [p.name for p in tmp_path.iterdir()] == [".env"]

    def test_overwrites_existing_file_atomically(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("OLD=1\n", encoding="utf-8")

        atomic_write(target, "NEW=2\n")

        assert target.read_text(encoding="utf-8") == "NEW=2\n"
        # Still no stale .tmp left around.
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_failed_write_cleans_up_tmp_file(self, tmp_path, monkeypatch):
        """If the rename step fails, the temp file must not be left behind."""
        target = tmp_path / ".env"

        def _boom(_src, _dst):
            raise RuntimeError("simulated rename failure")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(RuntimeError, match="simulated rename"):
            atomic_write(target, "x=y\n")

        # Final target must not exist (write didn't complete).
        assert not target.exists()
        # And no stragglers left in the directory.
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics only")
class TestRestrictPermsPosix:
    def test_chmods_to_0o600(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        target.chmod(0o644)

        restrict_perms(target)

        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600

    def test_missing_file_is_a_noop(self, tmp_path):
        # Should not raise when the file does not exist.
        restrict_perms(tmp_path / "absent")


class TestRestrictPermsCrossPlatform:
    def test_does_not_raise_on_missing_file(self, tmp_path):
        # The function is documented as "logs but never raises".
        restrict_perms(tmp_path / "no-such-file")

    @pytest.mark.skipif(
        sys.platform != "win32", reason="Windows-only icacls path"
    )
    def test_windows_path_does_not_raise(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")

        # Even when icacls is unhappy or USERNAME is unset, the call must
        # complete cleanly — perms tightening is best-effort.
        restrict_perms(target)
        assert target.exists()


class TestEnvPathForPrefix:
    def test_returns_canonical_subpath(self, tmp_path):
        result = env_path_for_prefix(tmp_path)
        assert result == tmp_path / "mobaide_agent" / ".env"

    def test_does_not_create_directory(self, tmp_path):
        # Pure path math — should not touch the filesystem.
        env_path_for_prefix(tmp_path)
        assert not (tmp_path / "mobaide_agent").exists()


class TestRenderTemplateInjectionGuard:
    """An override containing CR/LF/NUL would smuggle additional KEY=VALUE
    lines into the rendered file, overriding security-critical settings
    such as ``JWT_SECRET_KEY``. The guard must raise ValueError instead.
    """

    @pytest.mark.parametrize(
        "forbidden",
        ["\n", "\r", "\x00", "value-with-\nnewline", "value\rwith-cr",
         "trailing\x00nul"],
    )
    def test_newline_or_nul_in_value_raises(self, forbidden):
        with pytest.raises(ValueError, match="forbidden control character"):
            render_template(
                _BASIC_TEMPLATE,
                overrides={"RELAY_AGENT_TOKEN": forbidden},
            )

    def test_error_message_names_offending_key(self):
        with pytest.raises(ValueError, match="RELAY_AGENT_TOKEN"):
            render_template(
                _BASIC_TEMPLATE,
                overrides={"RELAY_AGENT_TOKEN": "good\ninjected=evil"},
            )

    def test_clean_value_does_not_raise(self):
        # Sanity check: ordinary base64-url and URL values are never blocked.
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={
                "RELAY_AGENT_TOKEN": "abc-_DEF.123:456",
                "RELAY_SERVER_URL": "wss://example.com:443/path?x=y",
            },
        )
        assert "RELAY_AGENT_TOKEN=abc-_DEF.123:456" in rendered

    def test_guard_runs_before_any_substitution(self, tmp_path):
        # Even one bad value among many must abort the whole render — no
        # partial output, no half-rendered template returned to the caller.
        with pytest.raises(ValueError):
            render_template(
                _BASIC_TEMPLATE,
                overrides={
                    "JWT_SECRET_KEY": "good-secret",
                    "RELAY_AGENT_TOKEN": "evil\nJWT_SECRET_KEY=overridden",
                },
            )

    def test_tab_and_space_are_allowed(self):
        # Other whitespace must NOT trigger the guard — only CR, LF, NUL.
        rendered = render_template(
            _BASIC_TEMPLATE,
            overrides={"RELAY_AGENT_TOKEN": "tab\tand spaces"},
        )
        assert "RELAY_AGENT_TOKEN=tab\tand spaces" in rendered


class TestParseEnvFileStringOverload:
    """``parse_env_file`` accepts a pre-read string in addition to a Path.

    Lets callers that have already loaded the file (e.g. ``mobaide-agent
    pair`` reads the existing .env once and passes the text downstream)
    skip a syscall and avoid the TOCTOU window.
    """

    def test_accepts_string_directly(self):
        text = "JWT_SECRET_KEY=secret\nRELAY_AGENT_ID=a-1\n"

        result = parse_env_file(text)

        assert result == {"JWT_SECRET_KEY": "secret", "RELAY_AGENT_ID": "a-1"}

    def test_string_overload_skips_comments_and_blanks(self):
        text = "# header\n\nFOO=bar\n  # indented\nBAZ=qux\n"

        result = parse_env_file(text)

        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_empty_string_returns_empty_dict(self):
        assert parse_env_file("") == {}

    def test_string_overload_treats_input_as_content_not_path(self):
        # A string that LOOKS like a missing file path must be parsed as
        # content, not opened from disk. The str overload skips the
        # ``Path.exists`` check entirely.
        text = "JWT_SECRET_KEY=from-string\n"

        result = parse_env_file(text)

        # Confirms: the text was parsed (not interpreted as a missing path
        # which would have returned ``{}`` like the Path overload does).
        assert result == {"JWT_SECRET_KEY": "from-string"}

    def test_path_and_string_yield_equivalent_results(self, tmp_path):
        # Cross-check: writing the same content to a file vs. passing it
        # as a string must produce byte-identical parse output.
        content = (
            "JWT_SECRET_KEY=topsecret\n"
            "RELAY_SERVER_URL=wss://example.com\n"
            "# a comment\n"
            "RELAY_AGENT_ID=a-1\n"
        )
        env_file = tmp_path / ".env"
        env_file.write_text(content, encoding="utf-8")

        from_path = parse_env_file(env_file)
        from_string = parse_env_file(content)

        assert from_path == from_string


class TestRestrictPermsWindowsAclArgv:
    """Verify the Windows ACL hardening invokes icacls with the correct
    Administrators:F + USERNAME:F grants.

    The agent runs in the installing user's session (Scheduled Task with
    ``-LogonType Interactive`` — see ``deploy/install.ps1:Install-Service``),
    so the user grant alone covers the runtime read/write path
    (including ``pairing_monitor.write_credentials_to_env``'s
    atomic-replace which needs ``DELETE``/``WRITE_DAC``, both contained
    in ``F``). ``Administrators:F`` is kept so the elevated installer
    can re-run ``init-env --force`` to rotate credentials.

    The historic ``SYSTEM:F`` grant was for a LocalSystem-hosted SCM
    service that has been removed; a regression guard below pins that
    SYSTEM no longer appears in the icacls argv.

    Tested by monkeypatching ``subprocess.run`` so the assertions work on
    every host. We also force ``sys.platform = 'win32'`` so the
    Windows-only branch is exercised on POSIX CI runners.
    """

    def _capture_run(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(_env_writer.subprocess, "run", fake_run)
        return captured

    def test_invokes_icacls_with_strip_inheritance(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        captured = self._capture_run(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "alice")

        restrict_perms(target)

        cmd = captured["cmd"]
        assert cmd[0] == "icacls"
        assert str(target) in cmd
        # /inheritance:r is required to drop broad-readers like Users.
        assert "/inheritance:r" in cmd

    def test_does_not_grant_system(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        captured = self._capture_run(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "alice")

        restrict_perms(target)

        cmd = captured["cmd"]
        # Regression guard against accidentally restoring the SYSTEM
        # grant — the agent no longer runs as LocalSystem, so SYSTEM
        # has no operational need for write/read access here. NT
        # AUTHORITY\SYSTEM still has unconstrained kernel access if
        # an admin tool needs it; the icacls ACL just stops being a
        # discovery channel for that principal.
        for variant in ("SYSTEM:F", "SYSTEM:R", "SYSTEM:M", "SYSTEM:RX"):
            assert variant not in cmd, (
                f"unexpected SYSTEM grant {variant} in icacls argv: {cmd}"
            )

    def test_grants_administrators_full(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        captured = self._capture_run(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "alice")

        restrict_perms(target)

        cmd = captured["cmd"]
        assert "Administrators:F" in cmd
        admin_idx = cmd.index("Administrators:F")
        assert cmd[admin_idx - 1] == "/grant"

    def test_grants_current_user_full(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        captured = self._capture_run(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "alice")

        restrict_perms(target)

        cmd = captured["cmd"]
        assert "alice:F" in cmd
        user_idx = cmd.index("alice:F")
        assert cmd[user_idx - 1] == "/grant"

    def test_skips_user_grant_when_username_unset(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        captured = self._capture_run(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("USERNAME", raising=False)

        restrict_perms(target)

        cmd = captured["cmd"]
        # Administrators grant must still be present even without an
        # interactive USERNAME.
        assert "Administrators:F" in cmd
        # Exactly one ``:F`` grant — Administrators:F — when there is
        # no interactive USERNAME. A second ``:F`` would indicate the
        # empty username was f-stringed in (producing a malformed
        # ``:F`` grant); the production code skips the
        # ``/grant {user}:F`` pair entirely when ``user`` is empty.
        f_grants = [p for p in cmd if p.endswith(":F")]
        assert f_grants == ["Administrators:F"]

    def test_subprocess_failure_does_not_raise(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")

        def fake_run(_cmd, **_kwargs):
            raise OSError("simulated icacls failure")

        monkeypatch.setattr(_env_writer.subprocess, "run", fake_run)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "alice")

        # Documented contract: restrict_perms is best-effort and must
        # never raise even when icacls is broken/missing.
        restrict_perms(target)
        assert target.exists()

    def test_icacls_invocation_passes_create_no_window_flag(
        self, tmp_path, monkeypatch
    ):
        """``subprocess.run`` for ``icacls`` MUST pass
        ``creationflags=_WINDOWS_NO_WINDOW``.

        The agent runs under ``pythonw.exe`` via the per-user Scheduled
        Task (no parent console). Without ``CREATE_NO_WINDOW``, every
        env-file write (``init-env``, ``pair``, post-pair credential
        atomic-replace) would pop a transient ``icacls.exe`` console
        window into the user's session. ``CREATE_NO_WINDOW`` keeps
        ``capture_output=True``'s stdio inheritance intact (unlike
        ``DETACHED_PROCESS`` which severs handle inheritance).

        We assert the flag matches the production constant (rather
        than the raw 0x08000000 literal) so the test runs identically
        on both win32 and non-win32 hosts: the constant resolves to
        ``CREATE_NO_WINDOW`` on win32 and ``0`` elsewhere, and the
        production code uses exactly that constant. The companion
        ``test_windows_no_window_constant_on_win32`` test pins the
        literal value when running on Windows.
        """
        target = tmp_path / ".env"
        target.write_text("x=1\n", encoding="utf-8")
        captured = self._capture_run(monkeypatch)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("USERNAME", "alice")

        restrict_perms(target)

        kwargs = captured["kwargs"]
        assert "creationflags" in kwargs, (
            "icacls subprocess.run kwargs must include the literal "
            "`creationflags` key -- a missing key (defaulting to 0) "
            "means a console window pops up under pythonw.exe on "
            f"every env-file write; got kwargs={kwargs!r}"
        )
        flag = kwargs["creationflags"]
        assert flag == _env_writer._WINDOWS_NO_WINDOW, (
            "icacls subprocess.run must pass "
            "creationflags=_WINDOWS_NO_WINDOW (the production "
            "constant) so the flag value matches the documented "
            f"platform-conditional shape; got creationflags={flag!r}"
        )

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Win32 literal value pin",
    )
    def test_windows_no_window_constant_on_win32(self) -> None:
        """On win32, ``_env_writer._WINDOWS_NO_WINDOW`` MUST equal
        ``0x08000000`` (subprocess.CREATE_NO_WINDOW).

        Pinning the literal here proves the module-level conditional
        ``CREATE_NO_WINDOW if sys.platform == "win32" else 0``
        resolves to the documented value at import time on Windows.
        A regression that flips the conditional or drops the import
        would surface as ``flag=0`` here.
        """
        import subprocess as _sp

        assert _env_writer._WINDOWS_NO_WINDOW == _sp.CREATE_NO_WINDOW
        assert _env_writer._WINDOWS_NO_WINDOW == 0x08000000

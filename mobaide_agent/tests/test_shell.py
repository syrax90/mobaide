"""Tests for the login-shell resolver (``mobaide.core.shell``).

Pins the resolution ladder ``$SHELL`` → ``pw_shell`` → ``/bin/sh`` with its
basename allowlist, and the two argv shapes it feeds every spawn path:
plain terminal (``[shell, "-l"]`` / ``["cmd.exe"]``) and custom agent
type (``[shell, "-l", "-c", command]`` / ``["cmd.exe", "/c", command]``
with the stored command as exactly one argv element).
"""
from __future__ import annotations

import sys
import types

import pytest

from mobaide.core import shell


@pytest.fixture(autouse=True)
def _posix(monkeypatch):
    """Pin the POSIX branch so results don't depend on the host OS; the
    Windows-shape tests flip the seam back explicitly."""
    monkeypatch.setattr(shell, "_is_windows", lambda: False)


class TestLoginShellPath:
    def test_shell_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert shell.login_shell_path() == "/bin/zsh"

    def test_disallowed_basename_falls_back_to_bin_sh(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
        assert shell.login_shell_path() == "/bin/sh"

    @pytest.mark.skipif(sys.platform == "win32", reason="pwd is POSIX-only")
    def test_empty_shell_env_uses_pwd_fallback(self, monkeypatch):
        import pwd

        monkeypatch.setenv("SHELL", "   ")
        monkeypatch.setattr(
            pwd,
            "getpwuid",
            lambda uid: types.SimpleNamespace(pw_shell="/bin/dash"),
        )
        assert shell.login_shell_path() == "/bin/dash"

    @pytest.mark.skipif(sys.platform == "win32", reason="pwd is POSIX-only")
    def test_pwd_fallback_disallowed_shell_falls_back(self, monkeypatch):
        import pwd

        monkeypatch.delenv("SHELL", raising=False)
        monkeypatch.setattr(
            pwd,
            "getpwuid",
            lambda uid: types.SimpleNamespace(pw_shell="/usr/sbin/nologin"),
        )
        assert shell.login_shell_path() == "/bin/sh"

    @pytest.mark.skipif(sys.platform == "win32", reason="pwd is POSIX-only")
    def test_empty_everywhere_falls_back(self, monkeypatch):
        import pwd

        monkeypatch.delenv("SHELL", raising=False)
        monkeypatch.setattr(
            pwd,
            "getpwuid",
            lambda uid: types.SimpleNamespace(pw_shell=""),
        )
        assert shell.login_shell_path() == "/bin/sh"

    @pytest.mark.skipif(sys.platform == "win32", reason="pwd is POSIX-only")
    def test_pwd_lookup_failure_falls_back(self, monkeypatch):
        import pwd

        def boom(uid):
            raise KeyError(uid)

        monkeypatch.delenv("SHELL", raising=False)
        monkeypatch.setattr(pwd, "getpwuid", boom)
        assert shell.login_shell_path() == "/bin/sh"


class TestResolveDefaultShell:
    def test_posix_login_shell_with_dash_l(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert shell.resolve_default_shell() == ["/bin/zsh", "-l"]

    def test_windows_is_bare_cmd(self, monkeypatch):
        monkeypatch.setattr(shell, "_is_windows", lambda: True)
        assert shell.resolve_default_shell() == ["cmd.exe"]


class TestBuildCustomArgv:
    def test_posix_command_is_single_argv_element(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/bash")
        command = "aider --model 'gpt x' && echo \"done\""
        assert shell.build_custom_argv(command) == [
            "/bin/bash", "-l", "-c", command,
        ]

    def test_posix_disallowed_login_shell_still_falls_back(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/opt/nu/bin/nu")
        assert shell.build_custom_argv("echo hi") == [
            "/bin/sh", "-l", "-c", "echo hi",
        ]

    def test_windows_uses_cmd_slash_c(self, monkeypatch):
        monkeypatch.setattr(shell, "_is_windows", lambda: True)
        command = "echo hi & dir"
        assert shell.build_custom_argv(command) == ["cmd.exe", "/c", command]

"""Tests for Codex auth helpers — presence detection + stdin-piped API-key login.

The login subprocess is never spawned for real: ``subprocess.run`` (invoked by
the helper via ``asyncio.to_thread``) is monkeypatched with a fake whose
``CompletedProcess`` / ``TimeoutExpired`` are scripted, so every test asserts a
contract (exit-code mapping, stderr scrubbing, the key NEVER appearing in argv,
timeout handling) without depending on a real ``codex``.
"""
from __future__ import annotations

import subprocess

from mobaide.core import codex_auth
from mobaide.core.codex_auth import (
    codex_auth_present,
    codex_home,
    codex_login_with_api_key,
)


# ---------------------------------------------------------------------------
# codex_home / codex_auth_present
# ---------------------------------------------------------------------------


def test_codex_home_uses_env_override():
    assert codex_home({"CODEX_HOME": "/custom/codex"}) == "/custom/codex"


def test_codex_home_defaults_to_user_dot_codex():
    home = codex_home({})
    assert home.endswith(".codex")
    assert "CODEX_HOME" not in home


async def test_auth_present_true_when_env_has_api_key(tmp_path, monkeypatch):
    """An ``OPENAI_API_KEY`` in the env is a definitive credential — returned
    with no status probe (the env-credential path Codex itself honours)."""
    captured = _patch_run(monkeypatch, returncode=1)
    env = {"OPENAI_API_KEY": "sk-live-xyz", "CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is True
    assert "argv" not in captured  # env key short-circuits the status probe


async def test_auth_present_true_when_status_signed_in(tmp_path, monkeypatch):
    """``codex login status`` exit 0 is authoritative — signed in. Also covers the
    OS keyring / auto store with no auth.json present."""
    captured = _patch_run(monkeypatch, returncode=0)
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is True
    assert captured["argv"] == ["codex", "login", "status"]


async def test_auth_present_false_when_status_logged_out_despite_auth_json(
    tmp_path, monkeypatch,
):
    """A present-but-stale ``auth.json`` must NOT be trusted: when ``codex login
    status`` reports signed out (exit 1), the guard returns False so the session
    is never launched into Codex's interactive login/failure path. This is the
    round-4 fix — file presence no longer short-circuits."""
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    _patch_run(monkeypatch, returncode=1, stderr=b"Not logged in")
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is False


async def test_auth_present_true_when_status_unsupported_falls_back_to_auth_json(
    tmp_path, monkeypatch,
):
    """An older codex without the ``status`` subcommand (clap exit 2 /
    'unrecognized subcommand') is indeterminate — fall back to auth.json presence
    so a valid file-authed host on an old CLI is not wrongly rejected."""
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    _patch_run(
        monkeypatch, returncode=2,
        stderr=b"error: unrecognized subcommand 'status'",
    )
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is True


async def test_auth_present_false_when_status_unsupported_and_no_auth_json(
    tmp_path, monkeypatch,
):
    """Indeterminate status (old codex) with no auth.json -> unauthenticated."""
    _patch_run(
        monkeypatch, returncode=2,
        stderr=b"error: unrecognized subcommand 'status'",
    )
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is False


async def test_auth_present_true_when_status_cannot_run_falls_back_to_auth_json(
    tmp_path, monkeypatch,
):
    """If the probe can't execute (missing/broken binary), fall back to auth.json
    presence rather than crashing or wrongly rejecting a file credential."""
    def _boom(argv, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(codex_auth.subprocess, "run", _boom)
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is True


async def test_auth_present_false_when_status_logged_out_and_no_auth_json(
    tmp_path, monkeypatch,
):
    """No env key, ``codex login status`` signed out, no auth.json -> the
    no-credential hang the launcher guard prevents."""
    _patch_run(monkeypatch, returncode=1, stderr=b"Not logged in")
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is False


async def test_auth_present_false_for_empty_env_api_key(tmp_path, monkeypatch):
    """A present-but-empty ``OPENAI_API_KEY`` is not a credential; it falls
    through to a signed-out status with no auth.json -> unauthenticated."""
    _patch_run(monkeypatch, returncode=1)
    env = {"OPENAI_API_KEY": "", "CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin="codex", env=env) is False


async def test_auth_present_status_windows_cmd_shim_wrapped(tmp_path, monkeypatch):
    """The status probe wraps a Windows ``.cmd`` shim via ``cmd /c`` — the same
    shared argv builder the login path uses."""
    monkeypatch.setattr(codex_auth.sys, "platform", "win32")
    captured = _patch_run(monkeypatch, returncode=0)
    env = {"CODEX_HOME": str(tmp_path)}
    assert await codex_auth_present(codex_bin=r"C:\npm\codex.cmd", env=env) is True
    assert captured["argv"][:2] == ["cmd", "/c"]
    assert captured["argv"][2:] == [r"C:\npm\codex.cmd", "login", "status"]


# ---------------------------------------------------------------------------
# codex_login_with_api_key — fake subprocess
# ---------------------------------------------------------------------------


def _patch_run(
    monkeypatch, *, returncode=0, stdout=b"", stderr=b"", timeout=False
) -> dict:
    """Patch ``subprocess.run`` (the helper invokes it via ``asyncio.to_thread``)
    to return a scripted ``CompletedProcess`` and capture its argv / kwargs. With
    ``timeout=True`` it raises ``TimeoutExpired`` to exercise the timeout path."""
    captured: dict = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        if timeout:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=0.0)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(codex_auth.subprocess, "run", _fake_run)
    return captured


async def test_login_success_returns_true_empty_detail(monkeypatch):
    captured = _patch_run(monkeypatch, returncode=0)

    ok, detail = await codex_login_with_api_key(
        "sk-good-key", codex_bin="codex", env={},
    )

    assert ok is True
    assert detail == ""
    # The key is fed on stdin with a trailing newline — never via argv.
    assert captured["kwargs"]["input"] == b"sk-good-key\n"


async def test_login_failure_returns_false_with_sanitized_detail(monkeypatch):
    _patch_run(
        monkeypatch, returncode=1, stderr=b"login rejected for sk-secret-1234567890",
    )

    ok, detail = await codex_login_with_api_key(
        "sk-secret-1234567890", codex_bin="codex", env={},
    )

    assert ok is False
    assert "sk-secret-1234567890" not in detail
    assert "***" in detail


async def test_login_key_never_appears_in_argv(monkeypatch):
    """The defining security contract: the submitted key must never land in the
    process argv (it would leak to process listings / shell history)."""
    captured = _patch_run(monkeypatch, returncode=0)

    api_key = "sk-do-not-leak-abcdef123456"
    await codex_login_with_api_key(api_key, codex_bin="codex", env={})

    argv = captured["argv"]
    assert argv == ["codex", "login", "--with-api-key"]
    assert all(api_key not in str(arg) for arg in argv)


async def test_login_scrubs_unrelated_sk_shape_from_stderr(monkeypatch):
    """An ``sk-`` shaped token in stderr is scrubbed even when it is NOT the
    submitted key (defense-in-depth against the CLI echoing other secrets)."""
    _patch_run(
        monkeypatch, returncode=1, stderr=b"context token sk-OTHERvalue9876543210 invalid",
    )

    ok, detail = await codex_login_with_api_key(
        "sk-submitted-0000", codex_bin="codex", env={},
    )

    assert ok is False
    assert "sk-OTHERvalue9876543210" not in detail


async def test_login_falls_back_to_stdout_when_stderr_empty(monkeypatch):
    _patch_run(monkeypatch, returncode=2, stdout=b"stdout failure note", stderr=b"")

    ok, detail = await codex_login_with_api_key("sk-x", codex_bin="codex", env={})

    assert ok is False
    assert "stdout failure note" in detail


async def test_login_timeout_returns_timed_out_detail(monkeypatch):
    """A hung login must time out without blocking the relay; ``subprocess.run``
    kills + reaps the child internally on ``TimeoutExpired``."""
    _patch_run(monkeypatch, timeout=True)

    ok, detail = await codex_login_with_api_key(
        "sk-x", codex_bin="codex", env={}, timeout=0.01,
    )

    assert ok is False
    assert "timed out" in detail.lower()


async def test_login_passes_env_to_subprocess(monkeypatch):
    captured = _patch_run(monkeypatch, returncode=0)

    await codex_login_with_api_key(
        "sk-x", codex_bin="codex", env={"CODEX_HOME": "/x", "PATH": "/y"},
    )

    passed_env = captured["kwargs"]["env"]
    assert passed_env["CODEX_HOME"] == "/x"
    assert passed_env["PATH"] == "/y"


async def test_login_windows_cmd_shim_wrapped_via_cmd_c(monkeypatch):
    """A Windows ``.cmd`` shim is not directly CreateProcess-able, so it must be
    invoked through ``cmd /c`` — with the key still on stdin, never in argv."""
    monkeypatch.setattr(codex_auth.sys, "platform", "win32")
    captured = _patch_run(monkeypatch, returncode=0)

    api_key = "sk-windows-key-12345"
    await codex_login_with_api_key(
        api_key, codex_bin=r"C:\npm\codex.cmd", env={},
    )

    argv = captured["argv"]
    assert argv[:2] == ["cmd", "/c"]
    assert argv[2:] == [r"C:\npm\codex.cmd", "login", "--with-api-key"]
    assert all(api_key not in str(arg) for arg in argv)
    assert captured["kwargs"]["input"] == api_key.encode() + b"\n"

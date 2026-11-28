"""Pins the relay key checks in ``deploy_central``'s remote preflight.

The relay must hold its own pairing key (RELAY_PAIRING_PRIVATE_KEY) and must
not hold the release signing key (MANIFEST_SIGNING_PRIVATE_KEY): without the
first every pairing answers 503, and the second is the key every agent update
trusts. The deploy refuses either state before it ships anything.

The preflight script runs for real under a local bash, pointed at a temporary
``REMOTE_BASE``, with a stub ``docker`` first on ``PATH``; only the ssh hop is
replaced. No server, no network.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_install_script_trap import _BASH


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import deploy_central  # noqa: E402


pytestmark = [
    pytest.mark.skipif(_BASH is None, reason="bash unavailable on this platform"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX stub docker"),
]

# Base64 of 32 bytes, the shape both private keys have in the relay .env.
RELEASE_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
PAIRING_KEY = "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="
BASE_ENV = "JWT_SECRET_KEY=test-secret\n"
PAIRING_LINE = f"RELAY_PAIRING_PRIVATE_KEY={PAIRING_KEY}\n"


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable stand-in for /opt/mobaide where docker answers."""
    base = tmp_path / "opt-mobaide"
    base.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    monkeypatch.setattr(deploy_central, "REMOTE_BASE", str(base))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return base


def _run_remote(script: str) -> deploy_central.SshResult:
    """What ``invoke_ssh`` does on the server: bash reads the script on stdin."""
    assert _BASH is not None
    proc = subprocess.run(
        [_BASH, "-s"],
        input=script,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return deploy_central.SshResult(proc.returncode, proc.stdout)


def _preflight_errors(remote: Path, env_body: str | None) -> set[str]:
    if env_body is not None:
        (remote / ".env").write_text(env_body, encoding="utf-8")
    result = _run_remote(deploy_central.build_preflight_script("LetsEncrypt"))
    errors: set[str] = set()
    for line in result.output.splitlines():
        if line.startswith("ERRORS:"):
            errors = set(line[len("ERRORS:"):].split())
    assert result.exit_code == (1 if errors else 0), result.output
    return errors


class TestRelayKeyChecks:
    @pytest.mark.parametrize(
        ("env_body", "expected"),
        [
            (
                BASE_ENV + PAIRING_LINE + f"MANIFEST_SIGNING_PRIVATE_KEY={RELEASE_KEY}\n",
                {"RELEASE_KEY_ON_RELAY"},
            ),
            (
                BASE_ENV + PAIRING_LINE + f"export MANIFEST_SIGNING_PRIVATE_KEY={RELEASE_KEY}\n",
                {"RELEASE_KEY_ON_RELAY"},
            ),
            (
                BASE_ENV + PAIRING_LINE + f"# MANIFEST_SIGNING_PRIVATE_KEY={RELEASE_KEY}\n",
                set(),
            ),
            (BASE_ENV + PAIRING_LINE + "MANIFEST_SIGNING_PRIVATE_KEY=\n", set()),
            (BASE_ENV, {"PAIRING_KEY_MISSING"}),
            (BASE_ENV + "RELAY_PAIRING_PRIVATE_KEY=\n", {"PAIRING_KEY_MISSING"}),
            (BASE_ENV + f"# {PAIRING_LINE}", {"PAIRING_KEY_MISSING"}),
            (
                BASE_ENV + f"MANIFEST_SIGNING_PRIVATE_KEY={RELEASE_KEY}\n",
                {"RELEASE_KEY_ON_RELAY", "PAIRING_KEY_MISSING"},
            ),
        ],
        ids=[
            "release-key-set",
            "release-key-exported",
            "release-key-commented-out",
            "release-key-blank",
            "pairing-key-absent",
            "pairing-key-blank",
            "pairing-key-commented-out",
            "both-wrong-in-one-pass",
        ],
    )
    def test_only_a_live_value_counts(
        self, remote: Path, env_body: str, expected: set[str]
    ) -> None:
        """A blank or commented-out line is unset, as the relay's config reads
        it: ``release_private_key_left_in_env`` ignores a blank value, so a
        blank release-key line must not block the deploy, and a blank pairing
        key still leaves every pairing answering 503.
        """
        assert _preflight_errors(remote, env_body) == expected

    def test_a_missing_env_is_reported_once_not_as_a_missing_pairing_key(
        self, remote: Path
    ) -> None:
        assert _preflight_errors(remote, None) == {"ENV_MISSING"}

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root reads a mode-000 file",
    )
    def test_an_unreadable_env_skips_the_key_checks(self, remote: Path) -> None:
        """The checks run only where the deploy user can read .env; a guess
        from an unreadable file would block a healthy relay."""
        env_file = remote / ".env"
        env_file.write_text(BASE_ENV, encoding="utf-8")
        env_file.chmod(0)
        try:
            assert _preflight_errors(remote, None) == set()
        finally:
            env_file.chmod(0o600)


class TestRemotePreflightRefusal:
    @pytest.mark.parametrize(
        ("env_body", "named_key"),
        [
            (
                BASE_ENV + PAIRING_LINE + f"MANIFEST_SIGNING_PRIVATE_KEY={RELEASE_KEY}\n",
                "MANIFEST_SIGNING_PRIVATE_KEY",
            ),
            (BASE_ENV, "RELAY_PAIRING_PRIVATE_KEY"),
        ],
        ids=["release-key-on-relay", "pairing-key-missing"],
    )
    def test_the_deploy_stops_and_names_the_key_but_never_its_value(
        self,
        remote: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        env_body: str,
        named_key: str,
    ) -> None:
        (remote / ".env").write_text(env_body, encoding="utf-8")
        seen: list[str] = []

        def fake_ssh(script: str) -> deploy_central.SshResult:
            result = _run_remote(script)
            seen.append(result.output)
            return result

        monkeypatch.setattr(deploy_central, "invoke_ssh", fake_ssh)

        with pytest.raises(deploy_central.DeployError):
            deploy_central.remote_preflight("LetsEncrypt")

        out = capsys.readouterr().out
        assert f"[XX] {remote}/.env" in out
        assert named_key in out
        assert "Server prereqs OK" not in out
        for secret in (RELEASE_KEY, PAIRING_KEY):
            assert secret not in out
            assert secret not in seen[0]

    def test_a_relay_with_only_its_pairing_key_passes(
        self,
        remote: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (remote / ".env").write_text(BASE_ENV + PAIRING_LINE, encoding="utf-8")
        monkeypatch.setattr(deploy_central, "invoke_ssh", _run_remote)

        deploy_central.remote_preflight("LetsEncrypt")

        out = capsys.readouterr().out
        assert "[OK] Server prereqs OK" in out
        assert "[XX]" not in out

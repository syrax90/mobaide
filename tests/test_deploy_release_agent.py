"""Pins the parts of ``deploy/prod/release_agent.py`` that decide what a
signed manifest tells agents to trust: the signed body (dependency lock and
key lists), the check that the install scripts carry the signing key, and
the comparison of the relay pairing key with the relay. No network, no build.

Keys are made at test run time.
"""
from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import tomllib
import urllib.error
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import release_agent as ra  # noqa: E402
import release_common  # noqa: E402
from release_common import ReleaseError  # noqa: E402

from mobaide.agent.trust_store import (  # noqa: E402
    pairing_verification_keys,
    record_manifest_trust,
    release_verification_keys,
)
from mobaide.agent.updater import InstallLayout  # noqa: E402
from mobaide.manifest_signing import verify_signature  # noqa: E402

SERVER_URL = "https://relay.test"


def _new_public_key() -> str:
    return ra.public_key_b64(Ed25519PrivateKey.generate())


def _sign(key, **trust_keys) -> dict:
    return ra.sign_manifest(
        key,
        "1.2.3",
        SERVER_URL,
        Path("mobaide-agent-1.2.3.tar.gz"),
        "a" * 64,
        Path("mobaide_agent-1.2.3-py3-none-any.whl"),
        "b" * 64,
        "c" * 64,
        **trust_keys,
    )


class TestSignManifest:
    def test_the_agent_accepts_the_signature_and_the_listed_keys(self, tmp_path):
        """The release script and the agent are separate programs. The agent
        must verify what the script signed and read the same key lists."""
        key = Ed25519PrivateKey.generate()
        pinned = ra.public_key_b64(key)
        pairing_key, next_release_key = _new_public_key(), _new_public_key()

        record = _sign(
            key, relay_pairing_key=pairing_key, next_signing_key=next_release_key,
        )

        body = record["body"]
        verify_signature(body, record["signature_b64"], pinned)
        assert body["dependency_lock_sha256"] == "c" * 64
        layout = InstallLayout(prefix=tmp_path / "agent")
        layout.trust_dir.mkdir(parents=True)
        assert record_manifest_trust(layout, body)
        assert release_verification_keys(layout, pinned) == (pinned, next_release_key)
        assert pairing_verification_keys(layout, pinned) == (pinned, pairing_key)

    def test_without_optional_keys_the_manifest_lists_none(self):
        record = _sign(Ed25519PrivateKey.generate())

        assert record["body"]["trust"] == {
            "relay_pairing_public_keys": [],
            "release_public_keys": [],
        }


class TestCheckInstallScripts:
    @staticmethod
    def _checkout(tmp_path: Path, *, sh_key: str, ps1_key: str) -> Path:
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "install.sh").write_text(
            f'MOBAIDE_RELEASE_PUBLIC_KEY_B64="{sh_key}"\n', encoding="utf-8",
        )
        (deploy / "install.ps1").write_text(
            f'$Script:ReleasePublicKeyB64 = "{ps1_key}"\n', encoding="utf-8",
        )
        return tmp_path

    def test_passes_when_both_scripts_carry_the_signing_key(self, tmp_path):
        key = Ed25519PrivateKey.generate()
        public = ra.public_key_b64(key)

        ra.check_install_scripts(
            self._checkout(tmp_path, sh_key=public, ps1_key=public), key,
        )

    @pytest.mark.parametrize("stale", ["install.sh", "install.ps1"])
    def test_stops_the_release_when_a_script_carries_another_key(
        self, tmp_path, stale
    ):
        key = Ed25519PrivateKey.generate()
        public = ra.public_key_b64(key)
        other = _new_public_key()
        src = self._checkout(
            tmp_path,
            sh_key=other if stale == "install.sh" else public,
            ps1_key=other if stale == "install.ps1" else public,
        )

        with pytest.raises(ReleaseError, match=f"deploy/{stale}"):
            ra.check_install_scripts(src, key)


class TestDependencyLockPythonFloor:
    """The lock must cover every Python the agent supports. An agent on an
    interpreter below the one that made the lock would otherwise need a
    dependency that the lock does not pin, and its update would fail."""

    def test_reads_the_floor_that_the_agent_package_declares(self):
        with (_REPO_ROOT / "mobaide_agent" / "pyproject.toml").open("rb") as fh:
            requires_python = tomllib.load(fh)["project"]["requires-python"]

        floor = ra.minimum_python(requires_python)

        assert requires_python.replace(" ", "") == f">={floor}"

    @pytest.mark.parametrize(
        "requires_python",
        [">=3.12,<4", "~=3.12", "==3.12.*", ">3.11", ">=3", ""],
    )
    def test_a_range_without_one_plain_floor_stops_the_release(
        self, requires_python
    ):
        with pytest.raises(ReleaseError, match="requires-python"):
            ra.minimum_python(requires_python)

    def test_the_lock_is_resolved_for_every_python_from_the_floor_up(
        self, tmp_path, monkeypatch
    ):
        commands: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands.append(list(cmd))
            Path(cmd[cmd.index("--output-file") + 1]).write_text(
                "example-dependency==1.0 --hash=sha256:" + "0" * 64 + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(ra.subprocess, "run", fake_run)

        lock = ra.build_dependency_lock(tmp_path / "agent", tmp_path, "3.12")

        (compile_cmd,) = commands
        assert lock == tmp_path / ra.DEPENDENCY_LOCK_NAME
        assert compile_cmd[compile_cmd.index("--python-version") + 1] == "3.12"
        # Without --universal the version would select one Python, not a floor.
        assert "--universal" in compile_cmd


class TestLockHasWheelsEverywhere:
    """The agent installs with ``--only-binary``. The trial install proves the
    lock on the release machine only, so a dependency without a wheel for
    another platform or Python must stop the release, not the agents there."""

    @staticmethod
    def _stub_uv(monkeypatch, *, no_wheel=()) -> list[list[str]]:
        """Answer every ``uv pip compile``; the ``(platform, version)`` pairs
        in ``no_wheel`` fail. Returns the list that collects the commands."""
        commands: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands.append(list(cmd))
            if _target(cmd) in no_wheel:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="",
                    stderr="example-dependency==1.0 has no usable wheel\n",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ra.subprocess, "run", fake_run)
        return commands

    @pytest.mark.parametrize("below_floor", [0, 1])
    def test_asks_for_every_platform_and_every_python_from_the_floor_up(
        self, tmp_path, monkeypatch, below_floor
    ):
        commands = self._stub_uv(monkeypatch)
        lock = tmp_path / ra.DEPENDENCY_LOCK_NAME
        versions = ra.LOCK_PYTHON_VERSIONS[below_floor:]

        ra.check_lock_has_wheels(lock, tmp_path, versions[0])

        assert sorted(_target(cmd) for cmd in commands) == sorted(
            (platform, version)
            for platform in ra.LOCK_TARGET_PLATFORMS
            for version in versions
        )
        for cmd in commands:
            assert cmd[:4] == ["uv", "pip", "compile", str(lock)]
            # Without --no-build uv accepts a source distribution, which the
            # agent refuses to install.
            assert "--no-build" in cmd

    def test_one_error_names_every_platform_and_python_without_a_wheel(
        self, tmp_path, monkeypatch
    ):
        no_wheel = [
            (ra.LOCK_TARGET_PLATFORMS[3], ra.LOCK_PYTHON_VERSIONS[0]),
            (ra.LOCK_TARGET_PLATFORMS[-1], ra.LOCK_PYTHON_VERSIONS[-1]),
        ]
        self._stub_uv(monkeypatch, no_wheel=no_wheel)

        with pytest.raises(ReleaseError) as failure:
            ra.check_lock_has_wheels(
                tmp_path / ra.DEPENDENCY_LOCK_NAME, tmp_path, ra.LOCK_PYTHON_VERSIONS[0],
            )

        message = str(failure.value)
        for platform, version in no_wheel:
            assert f"{platform}, Python {version}" in message
        # The answer of uv says which dependency to fix.
        assert "example-dependency==1.0" in message
        assert ra.LOCK_TARGET_PLATFORMS[0] not in message

    def test_a_floor_above_every_listed_python_stops_the_release(
        self, tmp_path, monkeypatch
    ):
        """With no Python version left to ask for, the check would run
        nothing and report that every platform has wheels."""
        commands = self._stub_uv(monkeypatch)
        major, minor = ra.LOCK_PYTHON_VERSIONS[-1].split(".")
        above_every_version = f"{major}.{int(minor) + 1}"

        with pytest.raises(ReleaseError, match="LOCK_PYTHON_VERSIONS"):
            ra.check_lock_has_wheels(
                tmp_path / ra.DEPENDENCY_LOCK_NAME, tmp_path, above_every_version,
            )

        assert commands == []


class TestCryptographyRequirementPerPlatform:
    """The agent installs from wheels only, and the newest ``cryptography``
    has no wheel for two platforms. The agent package therefore lists
    ``cryptography`` more than once, with platform markers. Each platform
    must get exactly one of these requirements: with none the lock has no
    ``cryptography`` there, and with two it gets versions that conflict.

    Reads ``mobaide_agent/pyproject.toml`` only. The check against the real
    wheels is ``check_lock_has_wheels``, at release time."""

    @staticmethod
    def _requirements_for(sys_platform: str, platform_machine: str) -> list:
        requirements = pytest.importorskip("packaging.requirements")
        with (_REPO_ROOT / "mobaide_agent" / "pyproject.toml").open("rb") as fh:
            dependencies = tomllib.load(fh)["project"]["dependencies"]
        environment = {
            "sys_platform": sys_platform,
            "platform_machine": platform_machine,
        }
        return [
            requirement
            for requirement in map(requirements.Requirement, dependencies)
            if requirement.name == "cryptography"
            and (requirement.marker is None or requirement.marker.evaluate(environment))
        ]

    @pytest.mark.parametrize(
        "sys_platform, platform_machine, newest_with_a_wheel",
        [
            ("darwin", "x86_64", "48.0.1"),
            ("darwin", "arm64", None),
            ("win32", "AMD64", None),
            ("win32", "ARM64", "46.0.3"),
            ("linux", "x86_64", None),
            ("linux", "aarch64", None),
        ],
    )
    def test_each_platform_gets_one_requirement_with_its_own_ceiling(
        self, sys_platform, platform_machine, newest_with_a_wheel
    ):
        applies = self._requirements_for(sys_platform, platform_machine)

        assert len(applies) == 1, [str(requirement) for requirement in applies]
        versions = applies[0].specifier
        if newest_with_a_wheel is None:
            assert versions.contains("9999.0")
        else:
            major, minor, patch = newest_with_a_wheel.split(".")
            assert versions.contains(newest_with_a_wheel)
            assert not versions.contains(f"{major}.{minor}.{int(patch) + 1}")


def _target(cmd: list[str]) -> tuple[str, str]:
    return (
        cmd[cmd.index("--python-platform") + 1],
        cmd[cmd.index("--python-version") + 1],
    )


def _relay_answers(monkeypatch, answer) -> None:
    """Make the relay answer ``GET /api/v1/agent/pairing_key`` with a key, or
    with an HTTP error status. Retries do not sleep."""
    monkeypatch.setattr(release_common.time, "sleep", lambda _seconds: None)

    def http_json(url, **_kwargs):
        assert url == f"{SERVER_URL}/api/v1/agent/pairing_key"
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "error", None, io.BytesIO(b""))
        return {"public_key_b64": answer}

    monkeypatch.setattr(ra, "http_json", http_json)


class TestCheckRelayPairingKey:
    """The manifest names the key that signs the pairing credential push, so
    it must be the key the relay holds."""

    def test_passes_when_the_local_key_is_the_relay_key(self, monkeypatch):
        key = _new_public_key()
        _relay_answers(monkeypatch, key)

        ra.check_relay_pairing_key(key, SERVER_URL)

    @pytest.mark.parametrize("answer", [404, None])
    def test_passes_when_neither_side_has_a_key(self, monkeypatch, answer):
        # 404: a relay from before the endpoint. None: the relay answers 200
        # with a null key because RELAY_PAIRING_PRIVATE_KEY is not set.
        _relay_answers(monkeypatch, answer)

        ra.check_relay_pairing_key(None, SERVER_URL)

    def test_stops_when_the_relay_has_a_key_that_the_release_dir_lacks(
        self, monkeypatch
    ):
        relay_key = _new_public_key()
        _relay_answers(monkeypatch, relay_key)

        with pytest.raises(ReleaseError, match="is missing") as failure:
            ra.check_relay_pairing_key(None, SERVER_URL)

        # The operator needs the key to put it into the release dir.
        assert relay_key in str(failure.value)

    @pytest.mark.parametrize("answer", [404, None])
    def test_stops_when_the_relay_serves_no_key_for_a_local_one(
        self, monkeypatch, answer
    ):
        _relay_answers(monkeypatch, answer)

        with pytest.raises(ReleaseError, match="serves no pairing key"):
            ra.check_relay_pairing_key(_new_public_key(), SERVER_URL)

    def test_stops_when_the_keys_differ(self, monkeypatch):
        _relay_answers(monkeypatch, _new_public_key())

        with pytest.raises(ReleaseError, match="does not match"):
            ra.check_relay_pairing_key(_new_public_key(), SERVER_URL)

    @pytest.mark.parametrize("status", [500, 503])
    def test_another_relay_error_is_not_read_as_no_key(self, monkeypatch, status):
        _relay_answers(monkeypatch, status)

        with pytest.raises(ReleaseError, match=f"HTTP {status}"):
            ra.check_relay_pairing_key(None, SERVER_URL)


class TestReadOptionalPublicKey:
    @staticmethod
    def _release_dir(tmp_path: Path, content: str | None) -> Path:
        release_dir = tmp_path / "release"
        agent_dir = release_dir / "agent"
        agent_dir.mkdir(parents=True)
        if os.name != "nt":
            os.chmod(agent_dir, 0o700)
        if content is not None:
            key_file = release_dir / ra.RELAY_PAIRING_PUBLIC_KEY_FILE
            key_file.write_text(content, encoding="utf-8")
            if os.name != "nt":
                os.chmod(key_file, 0o600)
        return release_dir

    def test_missing_file_means_no_key(self, tmp_path):
        release_dir = self._release_dir(tmp_path, None)

        assert (
            ra.read_optional_public_key(release_dir, ra.RELAY_PAIRING_PUBLIC_KEY_FILE)
            is None
        )

    def test_reads_a_public_key(self, tmp_path):
        key = _new_public_key()
        release_dir = self._release_dir(tmp_path, key + "\n")

        assert (
            ra.read_optional_public_key(release_dir, ra.RELAY_PAIRING_PUBLIC_KEY_FILE)
            == key
        )

    @pytest.mark.parametrize(
        "content",
        ["not-base64-!!", base64.b64encode(b"\x01" * 16).decode()],
        ids=["not-base64", "wrong-length"],
    )
    def test_a_file_that_is_not_a_public_key_stops_the_release(
        self, tmp_path, content
    ):
        release_dir = self._release_dir(tmp_path, content)

        with pytest.raises(ReleaseError, match="not a base64 Ed25519 public key"):
            ra.read_optional_public_key(release_dir, ra.RELAY_PAIRING_PUBLIC_KEY_FILE)

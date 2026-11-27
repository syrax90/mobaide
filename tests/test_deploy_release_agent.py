"""Pins the parts of ``deploy/prod/release_agent.py`` that decide what a
signed manifest tells agents to trust: the signed body (dependency lock and
key lists), the check that the install scripts carry the signing key, the
committed dependency lock, and the comparison of the relay pairing key with
the relay. No network, no build.

Keys are made at test run time.
"""
from __future__ import annotations

import base64
import io
import os
import re
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
        lock = tmp_path / ra.DEPENDENCY_LOCK_NAME

        ra.compile_lock(tmp_path / "agent", lock, "3.12")

        (compile_cmd,) = commands
        assert compile_cmd[compile_cmd.index("--output-file") + 1] == str(lock)
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
            (_GLIBC_2_39_ONLY.get((platform, version), platform), version)
            for platform in ra.LOCK_TARGET_PLATFORMS
            for version in versions
        )
        for cmd in commands:
            assert cmd[:5] == ["uv", "--no-config", "pip", "compile", str(lock)]
            # Without --no-build uv accepts a source distribution, which the
            # agent refuses to install.
            assert "--no-build" in cmd

    def test_a_missing_wheel_for_glibc_2_39_names_the_listed_platform(
        self, tmp_path, monkeypatch
    ):
        """The operator edits ``LOCK_TARGET_PLATFORMS``, so the error names
        the platform as listed there, not the glibc 2.39 one uv was asked for."""
        (listed, version), asked = next(iter(_GLIBC_2_39_ONLY.items()))
        self._stub_uv(monkeypatch, no_wheel=[(asked, version)])

        with pytest.raises(ReleaseError) as failure:
            ra.check_lock_has_wheels(
                tmp_path / ra.DEPENDENCY_LOCK_NAME, tmp_path, ra.LOCK_PYTHON_VERSIONS[0],
            )

        assert f"{listed}, Python {version}" in str(failure.value)

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

    def test_macos_wheels_are_checked_for_the_oldest_macos_install_sh_accepts(
        self, tmp_path, monkeypatch
    ):
        """uv checks a macOS platform for macOS 13 by default, and the
        machine may set another target. A wheel that needs a newer macOS
        would pass and then fail on the oldest Macs the installer accepts."""
        monkeypatch.setenv("MACOSX_DEPLOYMENT_TARGET", "14.0")
        targets: dict[tuple[str, str], str | None] = {}

        def fake_run(cmd, **kwargs):
            targets[_target(cmd)] = kwargs["env"].get("MACOSX_DEPLOYMENT_TARGET")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ra.subprocess, "run", fake_run)

        ra.check_lock_has_wheels(
            tmp_path / ra.DEPENDENCY_LOCK_NAME, tmp_path, ra.LOCK_PYTHON_VERSIONS[0],
        )

        macos = {
            target: value
            for target, value in targets.items()
            if target[0].endswith("-apple-darwin")
        }
        assert len(macos) == 2 * len(ra.LOCK_PYTHON_VERSIONS)
        assert set(macos.values()) == {ra.LOCK_MACOS_MINIMUM}


class TestUvResolvesFromThePublicIndexOnly:
    """The versions and hashes in the lock come from what uv resolves. A uv
    setting on the release machine (a config file, an index variable) must
    not select another package index for the lock or for the wheel check."""

    _RESOLUTION_SETTINGS = (
        "UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS", "UV_CONSTRAINT", "UV_OVERRIDE",
    )

    def _machine_settings(self, monkeypatch, tmp_path: Path) -> None:
        for name in self._RESOLUTION_SETTINGS:
            monkeypatch.setenv(name, "https://packages.example.test/simple")
        monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))

    def test_machine_settings_do_not_select_another_index(
        self, tmp_path, monkeypatch
    ):
        index_settings = self._RESOLUTION_SETTINGS
        self._machine_settings(monkeypatch, tmp_path)
        calls: list[tuple[list[str], dict[str, str]]] = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs["env"]))
            Path(cmd[cmd.index("--output-file") + 1]).write_text(
                _lock_text("example-dependency==1.0"), encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ra.subprocess, "run", fake_run)
        lock = tmp_path / ra.DEPENDENCY_LOCK_NAME

        ra.compile_lock(tmp_path / "agent", lock, "3.12")
        ra.check_lock_has_wheels(lock, tmp_path, ra.LOCK_PYTHON_VERSIONS[-1])

        assert len(calls) == 1 + len(ra.LOCK_TARGET_PLATFORMS)
        for cmd, env in calls:
            assert cmd[:2] == ["uv", "--no-config"]
            assert cmd[cmd.index("--default-index") + 1] == "https://pypi.org/simple"
            assert [name for name in index_settings if name in env] == []
            # Resolution settings go: uv still needs cache, TLS and timeout.
            assert env["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
            assert env["PATH"] == os.environ["PATH"]

    def test_the_package_build_does_not_use_machine_settings_either(
        self, tmp_path, monkeypatch
    ):
        """``uv build`` downloads the build tools of the agent package, and
        they produce the wheel that is signed. That download follows the
        same rule as the lock."""
        self._machine_settings(monkeypatch, tmp_path)
        agent_dir = tmp_path / "src" / "mobaide_agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text(
            '[project]\nversion = "1.2.3"\nrequires-python = ">=3.12"\n',
            encoding="utf-8",
        )
        calls: list[tuple[list[str], dict[str, str]]] = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs["env"]))
            # A failed build ends build_artifacts at this one command.
            return subprocess.CompletedProcess(cmd, 1)

        monkeypatch.setattr(ra.subprocess, "run", fake_run)

        with pytest.raises(ReleaseError, match="uv build failed"):
            ra.build_artifacts(tmp_path / "src", "1.2.3", tmp_path / "out")

        ((cmd, env),) = calls
        assert cmd[:3] == ["uv", "--no-config", "build"]
        assert cmd[cmd.index("--default-index") + 1] == "https://pypi.org/simple"
        assert [name for name in self._RESOLUTION_SETTINGS if name in env] == []
        assert env["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
        assert env["PATH"] == os.environ["PATH"]


class TestLoadDependencyLock:
    """A release installs the dependency versions that were reviewed in a
    pull request: the lock committed with the source. The release tool must
    take that file as it is, and stop when it no longer fits the package or
    the index. It never puts versions of its own choice into a release."""

    _EXAMPLE = "example-dependency==1.0"
    _INDEX_HASHES = ("a" * 64, "b" * 64)
    _INDEX_MARKER = "sys_platform == 'win32'"

    @classmethod
    def _two_entry_lock(cls, example_hashes: tuple[str, ...], other_marker: str) -> str:
        return _lock_text(
            cls._EXAMPLE, f"other-dependency==2.0 ; {other_marker}",
            hashes={cls._EXAMPLE: example_hashes},
        )

    @staticmethod
    def _agent_dir(tmp_path: Path, committed: bytes | None) -> Path:
        agent_dir = tmp_path / "mobaide_agent"
        agent_dir.mkdir()
        if committed is not None:
            (agent_dir / ra.DEPENDENCY_LOCK_NAME).write_bytes(committed)
        return agent_dir

    @staticmethod
    def _uv_resolves(monkeypatch, fresh: str) -> list[tuple[list[str], str | None]]:
        """uv answers a compile with the lock text ``fresh``. Collects each
        command with the text its output file held before uv ran."""
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(cmd, **_kwargs):
            output = Path(cmd[cmd.index("--output-file") + 1])
            held = output.read_text(encoding="utf-8") if output.exists() else None
            calls.append((list(cmd), held))
            output.write_text(fresh, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(ra.subprocess, "run", fake_run)
        return calls

    def test_a_tag_without_the_lock_stops_the_release(self, tmp_path, monkeypatch):
        calls = self._uv_resolves(monkeypatch, _lock_text("example-dependency==1.0"))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        with pytest.raises(ReleaseError, match="is missing"):
            ra.load_dependency_lock(self._agent_dir(tmp_path, None), out_dir, "3.12")

        # uv must not make a lock in place of the missing one.
        assert calls == []
        assert not (out_dir / ra.DEPENDENCY_LOCK_NAME).exists()

    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf-checkout"])
    def test_the_release_gets_the_committed_lock_when_the_index_gives_the_same(
        self, tmp_path, monkeypatch, newline
    ):
        pins = (
            self._EXAMPLE,
            "per-platform==46.0.3 ; platform_machine == 'ARM64' and sys_platform == 'win32'",
            "per-platform==50.0.2 ; sys_platform != 'win32'",
        )
        committed = _lock_text(*pins, hashes={self._EXAMPLE: self._INDEX_HASHES})
        calls = self._uv_resolves(monkeypatch, committed)
        agent_dir = self._agent_dir(
            tmp_path, committed.replace("\n", newline).encode("utf-8"),
        )
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        lock = ra.load_dependency_lock(agent_dir, out_dir, "3.12")

        assert lock == out_dir / ra.DEPENDENCY_LOCK_NAME
        # LF on every OS, so the signed SHA-256 does not depend on the checkout.
        assert lock.read_bytes() == committed.encode("utf-8")
        assert (agent_dir / ra.DEPENDENCY_LOCK_NAME).read_bytes() == (
            committed.replace("\n", newline).encode("utf-8")
        )
        ((compile_cmd, held_before_uv),) = calls
        # uv keeps the versions that its output file holds. From an empty
        # file it takes the newest ones, and a release would stop each time
        # a dependency publishes a version. The hashes must not be in that
        # file: uv would give them back, and the comparison would compare the
        # committed lock with itself.
        assert held_before_uv is not None
        assert held_before_uv.splitlines() == list(pins)
        assert "--upgrade" not in compile_cmd
        # The answer of uv goes to a file of its own, not over the release lock.
        assert compile_cmd[compile_cmd.index("--output-file") + 1] != str(lock)

    @pytest.mark.parametrize(
        "fresh_pins",
        [
            ("example-dependency==1.1", "other-dependency==2.0"),
            ("example-dependency==1.0", "other-dependency==2.0", "new-dependency==3.0"),
            ("example-dependency==1.0",),
            (
                "example-dependency==1.0",
                "other-dependency==2.0",
                "other-dependency==1.9 ; sys_platform == 'win32'",
            ),
        ],
        ids=[
            "version-changed", "dependency-added", "dependency-removed",
            "second-version-for-one-platform",
        ],
    )
    def test_a_lock_that_does_not_fit_the_agent_package_stops_the_release(
        self, tmp_path, monkeypatch, fresh_pins
    ):
        committed = _lock_text("example-dependency==1.0", "other-dependency==2.0")
        self._uv_resolves(monkeypatch, _lock_text(*fresh_pins))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        with pytest.raises(ReleaseError, match="--update-lock"):
            ra.load_dependency_lock(
                self._agent_dir(tmp_path, committed.encode("utf-8")), out_dir, "3.12",
            )

    @pytest.mark.parametrize(
        "example_hashes, other_marker",
        [
            (("a" * 64, "c" * 64), "sys_platform == 'win32'"),
            (("a" * 64, "b" * 64, "c" * 64), "sys_platform == 'win32'"),
            (("a" * 64,), "sys_platform == 'win32'"),
            (("a" * 64, "b" * 64), "sys_platform == 'linux'"),
        ],
        ids=["hash-changed", "hash-added", "hash-missing", "marker-changed"],
    )
    def test_a_committed_lock_that_differs_from_the_index_stops_the_release(
        self, tmp_path, monkeypatch, example_hashes, other_marker
    ):
        """The versions are the ones the index resolves, but an entry is not
        what the index holds for them. pip installs any file whose hash the
        lock lists, on every platform the marker selects, so the set of
        hashes and the marker must be equal, not only overlap."""
        self._uv_resolves(
            monkeypatch, self._two_entry_lock(self._INDEX_HASHES, self._INDEX_MARKER),
        )
        committed = self._two_entry_lock(example_hashes, other_marker)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        with pytest.raises(ReleaseError, match="--update-lock"):
            ra.load_dependency_lock(
                self._agent_dir(tmp_path, committed.encode("utf-8")), out_dir, "3.12",
            )

    @pytest.mark.parametrize(
        "before, after, number",
        [
            ("--extra-index-url https://index.example/simple\n", "", 1),
            ("", "second-dependency @ https://files.example/second_dependency-3.0-py3-none-any.whl\n", 4),
        ],
        ids=["index-option", "url-requirement"],
    )
    def test_a_line_the_comparison_does_not_read_stops_the_release_before_uv_runs(
        self, tmp_path, monkeypatch, before, after, number
    ):
        """pip acts on such a line, but the comparison with the index reads
        pinned versions and their sha256 hashes only. The pins and hashes of
        this lock are what the index gives, so the comparison alone would
        accept it."""
        as_the_index_gives = _lock_text(self._EXAMPLE, hashes={self._EXAMPLE: self._INDEX_HASHES})
        committed = before + as_the_index_gives + after
        assert ra.lock_entries(committed) == ra.lock_entries(as_the_index_gives)
        calls = self._uv_resolves(monkeypatch, as_the_index_gives)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        with pytest.raises(ReleaseError, match=rf"line {number}\b"):
            ra.load_dependency_lock(
                self._agent_dir(tmp_path, committed.encode("utf-8")), out_dir, "3.12",
            )

        assert calls == []

    def test_entries_are_read_per_dependency_with_every_platform_version(self):
        """A marker and a hash line both hold text that looks like part of
        a pin. Only the line that starts an entry names a version, and each
        hash belongs to the entry above it."""
        arm64_windows = "platform_machine == 'ARM64' and sys_platform == 'win32'"
        text = _lock_text(
            "example-dependency==1.0",
            f"per-platform==46.0.3 ; {arm64_windows}",
            "per-platform==50.0.2 ; sys_platform != 'win32'",
            hashes={"example-dependency==1.0": ("a" * 64, "b" * 64)},
        )

        assert ra.lock_pins(text) == {
            "example-dependency": {"1.0"},
            "per-platform": {"46.0.3", "50.0.2"},
        }
        assert ra.lock_entries(text) == {
            ("example-dependency", "1.0", ""): {"sha256:" + "a" * 64, "sha256:" + "b" * 64},
            ("per-platform", "46.0.3", arm64_windows): {"sha256:" + "0" * 64},
            ("per-platform", "50.0.2", "sys_platform != 'win32'"): {"sha256:" + "0" * 64},
        }


class TestUpdateLockCommand:
    """``--update-lock`` and ``--upgrade-lock`` write the lock in the
    checkout for a pull request. Both run in a temporary checkout here: the
    committed lock of this repository is not touched."""

    @staticmethod
    def _checkout(tmp_path: Path, monkeypatch, lock_text: str | None = None) -> Path:
        agent_dir = tmp_path / "mobaide_agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\n', encoding="utf-8",
        )
        lock = agent_dir / ra.DEPENDENCY_LOCK_NAME
        if lock_text is not None:
            lock.write_text(lock_text, encoding="utf-8")
        monkeypatch.setattr(ra, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(ra, "require_tools", lambda *_tools: None)
        monkeypatch.setattr(
            ra, "run", lambda _args: pytest.fail("a lock update must not start a release"),
        )
        return lock

    @staticmethod
    def _stub_uv(
        monkeypatch, fresh: str, *, lock: Path,
        resolves: bool = True, wheels_everywhere: bool = True,
    ):
        """uv resolves to the lock text ``fresh``, or fails; the wheel check
        passes or fails for every platform. Collects each command with the
        text its output file held before uv ran, and the text that ``lock``
        (the lock in the checkout) held at that time."""
        calls: list[tuple[list[str], str | None, str | None]] = []

        def fake_run(cmd, **_kwargs):
            output = Path(cmd[cmd.index("--output-file") + 1])
            held = output.read_text(encoding="utf-8") if output.exists() else None
            in_checkout = lock.read_text(encoding="utf-8") if lock.exists() else None
            calls.append((list(cmd), held, in_checkout))
            if "--python-platform" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0 if wheels_everywhere else 1, stdout="",
                    stderr="example-dependency==1.1 has no usable wheel\n",
                )
            if not resolves:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            output.write_text(fresh, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ra.subprocess, "run", fake_run)
        return calls

    @pytest.mark.parametrize(
        "flag, takes_newest", [("--update-lock", False), ("--upgrade-lock", True)],
    )
    def test_writes_the_lock_in_the_checkout_after_the_wheel_check(
        self, tmp_path, monkeypatch, flag, takes_newest
    ):
        kept = "other-dependency==2.0 ; sys_platform == 'win32'"
        before = _lock_text("example-dependency==1.0", kept)
        after = _lock_text("example-dependency==1.1", kept)
        lock = self._checkout(tmp_path, monkeypatch, before)
        calls = self._stub_uv(monkeypatch, after, lock=lock)

        assert ra.main([flag]) == 0

        assert lock.read_text(encoding="utf-8") == after
        (resolve, held_before_uv, _in_checkout), *wheel_checks = calls
        resolved = resolve[resolve.index("--output-file") + 1]
        assert ("--upgrade" in resolve) is takes_newest
        # uv keeps the versions that the output file holds, so a plain
        # update changes only what pyproject.toml no longer accepts. The
        # hashes are not in that file: uv must read them from the index.
        assert held_before_uv is not None
        assert held_before_uv.splitlines() == ["example-dependency==1.0", kept]
        assert wheel_checks
        for cmd, _held, in_checkout in wheel_checks:
            # The check reads what uv resolved, and the checkout keeps the
            # old lock until every platform has its wheels.
            assert cmd[cmd.index("compile") + 1] == resolved
            assert in_checkout == before

    @pytest.mark.parametrize("failure", ["uv-fails", "no-wheel-somewhere"])
    @pytest.mark.parametrize(
        "had_a_lock", [True, False], ids=["lock-in-checkout", "first-lock"],
    )
    def test_a_failed_update_leaves_the_checkout_as_it_was(
        self, tmp_path, monkeypatch, failure, had_a_lock
    ):
        """The pull request must not carry a lock that some agents could
        never install, or a file that uv did not finish. The operator sees
        the failure, and the next commit still holds the reviewed lock."""
        before = _lock_text("example-dependency==1.0") if had_a_lock else None
        lock = self._checkout(tmp_path, monkeypatch, before)
        self._stub_uv(
            monkeypatch, _lock_text("example-dependency==1.1"), lock=lock,
            resolves=failure != "uv-fails",
            wheels_everywhere=failure != "no-wheel-somewhere",
        )

        assert ra.main(["--update-lock"]) == 1

        assert (lock.read_text(encoding="utf-8") if lock.exists() else None) == before


    @pytest.mark.parametrize(
        "had_a_lock", [True, False], ids=["lock-in-checkout", "first-lock"],
    )
    def test_a_resolved_text_with_another_kind_of_line_is_not_written(
        self, tmp_path, monkeypatch, had_a_lock
    ):
        """The release refuses a lock that holds more than pinned versions
        and their sha256 lines. The update command must not put such a file
        into the checkout for a pull request."""
        before = _lock_text("example-dependency==1.0") if had_a_lock else None
        lock = self._checkout(tmp_path, monkeypatch, before)
        resolved = (
            "--index-url https://index.example/simple\n" + _lock_text("example-dependency==1.1")
        )
        self._stub_uv(monkeypatch, resolved, lock=lock)

        with pytest.raises(ReleaseError, match=r"line 1\b"):
            ra.update_lock(upgrade=False)

        assert (lock.read_text(encoding="utf-8") if lock.exists() else None) == before


class TestCommittedDependencyLock:
    """``mobaide_agent/requirements.lock`` as committed. The full check (the
    lock equals what uv resolves) needs the package index and runs at release
    time. These checks read two files only, so a pull request that changes a
    dependency without the lock fails here first."""

    _LOCK = _REPO_ROOT / "mobaide_agent" / ra.DEPENDENCY_LOCK_NAME

    def test_every_direct_dependency_has_a_pin_that_its_requirement_accepts(self):
        requirements = pytest.importorskip("packaging.requirements")
        canonical = pytest.importorskip("packaging.utils").canonicalize_name
        with (_REPO_ROOT / "mobaide_agent" / "pyproject.toml").open("rb") as fh:
            dependencies = tomllib.load(fh)["project"]["dependencies"]
        pins = {
            canonical(name): versions
            for name, versions in ra.lock_pins(self._LOCK.read_text(encoding="utf-8")).items()
        }

        without_a_pin = [
            dependency
            for dependency in dependencies
            for requirement in [requirements.Requirement(dependency)]
            if not any(
                requirement.specifier.contains(version, prereleases=True)
                for version in pins.get(canonical(requirement.name), ())
            )
        ]

        assert without_a_pin == []

    def test_every_entry_is_one_pinned_version_with_a_sha256_hash(self):
        """The installers pass the lock to pip with ``--require-hashes``. A
        line that is not a hashed pin (an index option, a URL) would change
        where or what pip installs."""
        text = self._LOCK.read_text(encoding="utf-8")
        entries = [
            entry.strip() for entry in text.replace("\\\n", " ").splitlines() if entry.strip()
        ]

        not_a_hashed_pin = []
        for entry in entries:
            requirement, *options = re.split(r"\s+(?=--)", entry)
            if not (
                re.fullmatch(r"[a-z0-9][a-z0-9._-]*==[^\s;]+( ; .+)?", requirement)
                and options
                and all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", o) for o in options)
            ):
                not_a_hashed_pin.append(entry)

        assert not_a_hashed_pin == []
        # The release compares the lock with the index through this reader.
        # An entry or a hash that it does not see would not be compared.
        read = ra.lock_entries(text)
        assert len(read) == len(entries)
        assert sum(len(hashes) for hashes in read.values()) == text.count("--hash=")


    def test_the_release_accepts_every_line(self):
        ra.check_lock_grammar(self._LOCK.read_text(encoding="utf-8"))


class TestLockGrammar:
    """The installers give the lock to pip, and pip reads more from such a
    file than the release compares with the package index. So the lock may
    hold only pinned versions with their sha256 lines; every other line
    must stop the release and the error must name the line."""

    _A, _B = "a" * 64, "b" * 64
    _VALID = (
        "example-dependency==1.0 \\",
        f"    --hash=sha256:{_A} \\",
        f"    --hash=sha256:{_B}",
        "other-dependency==2.0 ; sys_platform == 'win32' \\",
        f"    --hash=sha256:{_A}",
    )

    @pytest.mark.parametrize(
        "number, written, replaces",
        [
            (3, f"    --hash sha256:{_B}", True),
            (3, f"    --hash=sha256:{_B.upper()}", True),
            (3, f"    --hash=sha512:{_B * 2}", True),
            (3, f'    --hash="sha256:{_B}"', True),
            (4, f"second-dependency==3.0 --hash=sha256:{_B}", False),
            (1, "example-dependency @ https://files.example/example_dependency-1.0-py3-none-any.whl \\", True),
            (1, "--extra-index-url https://index.example/simple", False),
            (1, "-r other.txt", False),
            (1, f"    --hash=sha256:{_B}", False),
            (4, f"other-dependency==2.0 ; sys_platform == 'win32' --hash=sha256:{_B} \\", True),
        ],
        ids=[
            "hash-with-a-space", "uppercase-hash", "sha512-hash", "quoted-hash",
            "requirement-and-hash-on-one-line", "url-requirement", "index-option",
            "include-of-another-file", "hash-before-the-first-pin", "option-in-a-marker",
        ],
    )
    def test_a_line_that_is_not_a_pin_or_one_of_its_sha256_lines_is_refused(
        self, number, written, replaces
    ):
        lines = list(self._VALID)
        ra.check_lock_grammar("\n".join(lines) + "\n")
        if replaces:
            lines[number - 1] = written
        else:
            lines.insert(number - 1, written)

        with pytest.raises(ReleaseError, match=rf"line {number}\b"):
            ra.check_lock_grammar("\n".join(lines) + "\n")

    def test_a_file_that_ends_inside_an_entry_is_refused(self):
        """A lock that was cut short: the last line promises one more line."""
        cut_after_the_last_pin = "\n".join(self._VALID[:4]) + "\n"

        with pytest.raises(ReleaseError, match=r"line 4\b"):
            ra.check_lock_grammar(cut_after_the_last_pin)


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


# The wheel check asks for these platforms in place of the listed ones: one
# dependency has wheels for Python 3.15 on glibc Linux only from glibc 2.39,
# and deploy/install.sh refuses an older glibc with that Python.
_GLIBC_2_39_ONLY = {
    ("x86_64-unknown-linux-gnu", "3.15"): "x86_64-manylinux_2_39",
    ("aarch64-unknown-linux-gnu", "3.15"): "aarch64-manylinux_2_39",
}


def _lock_text(*pins: str, hashes: dict[str, tuple[str, ...]] | None = None) -> str:
    """A lock in the layout uv writes: each pin, then one line per hash. A
    pin has one hash of zeros unless ``hashes`` names others for it."""
    return "".join(
        " \\\n".join(
            [pin, *(f"    --hash=sha256:{h}" for h in (hashes or {}).get(pin, ("0" * 64,)))]
        ) + "\n"
        for pin in pins
    )


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

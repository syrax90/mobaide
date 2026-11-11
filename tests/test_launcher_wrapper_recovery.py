"""Behavioural tests for the launcher wrapper that ``write_launcher_script``
in ``deploy/install.sh`` generates, and for ``stage_release``'s version guard.

The wrapper is the systemd / launchd entry point. When the current release
has no runnable, finished venv it rolls forward to the newest complete
sibling release and rewrites ``current_version`` before ``exec`` — the
in-venv ``mobaide.launcher`` cannot do that without its own interpreter.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_install_script_trap import _BASH, _sh_function, _sh_function_body


pytestmark = [
    pytest.mark.skipif(_BASH is None, reason="bash not available"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX wrapper"),
]


def _generate_wrapper(prefix: Path) -> Path:
    """Run ``write_launcher_script`` (PLATFORM=linux) to emit the wrapper."""
    assert _BASH is not None
    body = _sh_function_body("write_launcher_script")
    harness = (
        "set -u\n"
        "PLATFORM=linux\n"
        f"MOBAIDE_AGENT_PREFIX={shlex.quote(str(prefix))}\n"
        f"write_launcher_script() {{\n{body}\n}}\n"
        "write_launcher_script\n"
    )
    proc = subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)
    assert proc.returncode == 0, f"wrapper generation failed: {proc.stderr}"
    wrapper = prefix / "bin" / "mobaide-agent-launcher.sh"
    assert wrapper.is_file()
    return wrapper


def _make_release(prefix: Path, version: str, *, complete: bool = True) -> None:
    """``releases/<version>/venv/bin/python`` as a stub that echoes its
    version; ``complete`` writes the ``.installed`` sentinel."""
    release = prefix / "releases" / version
    bindir = release / "venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    py = bindir / "python"
    py.write_text(f'#!/bin/sh\necho "RAN {version}"\n', encoding="utf-8")
    py.chmod(0o755)
    if complete:
        (release / ".installed").write_text(version, encoding="utf-8")


def _run(prefix: Path, current: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    (prefix / "releases").mkdir(parents=True, exist_ok=True)
    (prefix / "current_version").write_text(f"{current}\n", encoding="utf-8")
    wrapper = _generate_wrapper(prefix)
    return subprocess.run(
        [_BASH, str(wrapper)],
        capture_output=True,
        text=True,
        env={"MOBAIDE_AGENT_PREFIX": str(prefix), "PATH": "/usr/bin:/bin"},
    )


def _current(prefix: Path) -> str:
    return (prefix / "current_version").read_text().strip()


def test_runs_a_healthy_current_release_without_recovery(tmp_path) -> None:
    prefix = tmp_path / "mobaide"
    _make_release(prefix, "0.2.0")
    _make_release(prefix, "0.2.13")

    result = _run(prefix, "0.2.0")

    assert result.returncode == 0, result.stderr
    assert "RAN 0.2.0" in result.stdout
    assert "recovered" not in result.stderr
    assert _current(prefix) == "0.2.0"


@pytest.mark.parametrize("current", ["0.2.99", "0.2.5"], ids=["missing", "unfinished"])
def test_recovers_to_the_newest_complete_sibling(tmp_path, current) -> None:
    prefix = tmp_path / "mobaide"
    _make_release(prefix, "0.9.0")
    _make_release(prefix, "0.10.0")  # numeric, not lexical, order
    _make_release(prefix, "0.2.5", complete=False)

    result = _run(prefix, current)

    assert result.returncode == 0, result.stderr
    assert "RAN 0.10.0" in result.stdout
    assert _current(prefix) == "0.10.0"
    assert "recovered to '0.10.0'" in result.stderr


def test_recovery_skips_everything_but_complete_numeric_releases(tmp_path) -> None:
    prefix = tmp_path / "mobaide"
    _make_release(prefix, "0.2.0")
    _make_release(prefix, ".staging-9.9.9-123")
    _make_release(prefix, ".old-9.9.8-123")
    _make_release(prefix, "backup")
    _make_release(prefix, "0.2.0-custom")
    _make_release(prefix, "9.9.9", complete=False)

    result = _run(prefix, "0.2.99")

    assert result.returncode == 0, result.stderr
    assert "RAN 0.2.0" in result.stdout
    assert _current(prefix) == "0.2.0"


def test_exits_70_when_no_runnable_release(tmp_path) -> None:
    prefix = tmp_path / "mobaide"
    _make_release(prefix, "0.2.0", complete=False)

    result = _run(prefix, "0.2.0")

    assert result.returncode == 70, result.stderr
    assert _current(prefix) == "0.2.0"


@pytest.mark.parametrize("version", ["../../etc", "..", "1.0/x", "1.0 x"])
def test_stage_release_rejects_a_path_unsafe_manifest_version(
    tmp_path, version
) -> None:
    """A signed-but-hostile version must not become a ``releases/<v>`` path."""
    assert _BASH is not None
    prefix = tmp_path / "prefix"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"body": {"version": version}}))
    harness = (
        "set -euo pipefail\n"
        f"PATH={shlex.quote(os.path.dirname(sys.executable))}:\"$PATH\"\n"
        f"MOBAIDE_AGENT_PREFIX={shlex.quote(str(prefix))}\n"
        "info() { :; }\n"
        "fail() { printf '[ERROR] %s\\n' \"$*\" >&2; exit 1; }\n"
        + _sh_function("stage_release")
        + f"stage_release {shlex.quote(str(manifest))}\n"
    )

    proc = subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)

    assert proc.returncode == 1
    assert "unsafe path characters" in proc.stderr
    assert not prefix.exists()

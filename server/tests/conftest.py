from __future__ import annotations

import sys

import pytest

from claude_mobile.core.models import SessionConfig

IS_WINDOWS = sys.platform == "win32"

skip_if_not_windows = pytest.mark.skipif(not IS_WINDOWS, reason="Windows only")
skip_if_not_linux = pytest.mark.skipif(IS_WINDOWS, reason="Linux/macOS only")


def default_shell() -> list[str]:
    """Return the default shell command for the current platform."""
    if IS_WINDOWS:
        return ["cmd.exe"]
    return ["/bin/bash"]


@pytest.fixture
def session_config() -> SessionConfig:
    return SessionConfig(
        session_id="test-session",
        command=default_shell(),
        rows=24,
        cols=80,
        scrollback_size=100,
    )


@pytest.fixture
def short_lived_config() -> SessionConfig:
    """Config that spawns a process which exits immediately."""
    if IS_WINDOWS:
        cmd = ["cmd.exe", "/c", "echo hello"]
    else:
        cmd = ["/bin/bash", "-c", "echo hello"]
    return SessionConfig(
        session_id="short-lived",
        command=cmd,
        rows=24,
        cols=80,
        scrollback_size=100,
    )

"""Shared resolution of the agent install layout and trust key.

The launcher and the running agent need to agree on the same on-disk
prefix and Ed25519 trust key location so the staged-binary auto-updater
operates on the directory the install scripts populated. The launcher
intentionally re-implements the prefix logic inline (it must remain
import-light because it runs across release dirs); this module is for
the agent process and the standalone ``mobaide-agent update`` CLI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def default_prefix() -> Path:
    """Return the canonical install prefix for the current OS.

    ``MOBAIDE_AGENT_PREFIX`` overrides every default and is honoured by
    the install scripts, the systemd unit, the launchd plist, and the
    Windows Service registration.
    """
    explicit = os.environ.get("MOBAIDE_AGENT_PREFIX")
    if explicit:
        return Path(explicit)
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        return Path(program_files) / "MobaideAgent"
    if sys.platform == "darwin":
        return Path("/usr/local/mobaide")
    return Path("/opt/mobaide")


def read_trust_public_key(layout) -> str | None:
    """Return the base64-encoded Ed25519 manifest-signing public key.

    Order: ``MOBAIDE_AGENT_TRUST_KEY`` env var (test/CI override) →
    ``<prefix>/trust/central_server.pub`` (production install).
    Returns ``None`` if neither source yields a non-empty value, in
    which case the auto-updater stays disabled — fail-closed.
    """
    explicit = os.environ.get("MOBAIDE_AGENT_TRUST_KEY")
    if explicit:
        return explicit.strip() or None
    try:
        text = layout.trust_public_key.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None

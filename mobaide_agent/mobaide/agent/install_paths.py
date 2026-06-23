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
from urllib.parse import urlparse


TRUST_KEY_ENDPOINT_PATH = "/api/v1/agent/trust_key"
MANIFEST_ENDPOINT_PATH = "/api/v1/agent/manifest.json"

ALLOW_INSECURE_HTTPS_BOOTSTRAP_ENV = "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH"


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


def user_data_dir() -> Path:
    """Per-user, always-writable data dir for the agent's mutable state.

    The anchor for ``projects.json`` / ``users.json`` / a relative
    ``audit_log_path`` when the install prefix is *not* writable — the case
    for an unprivileged ``python -m mobaide`` from a source checkout, where
    :func:`default_prefix` points at the OS-wide install location
    (``C:\\Program Files\\MobaideAgent`` / ``/opt/mobaide``) that only the
    installer's privileged context can write to.

    * Windows: ``%LOCALAPPDATA%\\MobaideAgent`` (``~/AppData/Local`` fallback).
    * POSIX (Linux/macOS): ``$XDG_DATA_HOME/mobaide`` or ``~/.local/share/mobaide``.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "MobaideAgent"
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / "mobaide"


def _is_writable_dir(path: Path) -> bool:
    """Return whether ``path`` exists-or-can-be-created and accepts a write.

    Probes by ensuring the directory then writing and removing a uniquely
    named temp file inside it. Returns ``False`` on any ``OSError`` —
    permission denied (the Windows ACL on ``C:\\Program Files``), a read-only
    volume, or a missing parent the process can't create under.

    ``os.access(path, os.W_OK)`` is deliberately *not* used: on Windows it
    reflects only the read-only file attribute, not the ACLs that actually
    block ``C:\\Program Files`` writes, so it reports a false-positive for
    precisely the case this guards. Probing with a real write is the only
    reliable signal. Side-effect-free on failure: when the directory can't be
    created or written, nothing is left behind.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".mobaide-write-probe-{os.getpid()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def state_prefix() -> Path:
    """Return the writable anchor for the agent's mutable state files.

    Equal to :func:`default_prefix` for an installed agent — the installer
    provisions its prefix as writable. A source/dev run with no
    ``MOBAIDE_AGENT_PREFIX`` would otherwise anchor state to the OS-wide
    install dir, which an unprivileged process cannot write to, and die on the
    first store write with ``[Errno 13] Permission denied``. When the resolved
    prefix isn't writable, fall back to :func:`user_data_dir` so a source run
    persists its own state, isolated from any co-installed agent — mirroring
    how ``app._resolve_env_file`` keeps a source run on its own ``.env``.

    An explicit ``MOBAIDE_AGENT_PREFIX`` always wins and is never second-
    guessed: an operator who pins the agent to a directory gets exactly that,
    and a real permission problem there surfaces loudly instead of being
    silently rerouted elsewhere.
    """
    prefix = default_prefix()
    if os.environ.get("MOBAIDE_AGENT_PREFIX"):
        return prefix
    if _is_writable_dir(prefix):
        return prefix
    return user_data_dir()


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


def endpoint_url_from_relay_server_url(relay_server_url: str, endpoint_path: str) -> str:
    """Translate a ``RELAY_SERVER_URL`` into a co-located HTTPS endpoint URL.

    ``wss://host[/path]`` becomes ``https://host`` + ``endpoint_path``;
    ``ws://host[/path]`` becomes ``http://...``. Any path component on the
    relay URL is intentionally dropped — the relay tunnel and the HTTP
    REST API live at the root of the same origin (see central_server's
    routing in ``central_server/app.py``), so the path on the relay URL
    is a relay-tunnel path, not an API base.

    Two security guards on top of basic URL parsing:

    * **User-info rejected** — ``urlparse('wss://api.mobaide.com@evil.example')``
      yields a ``netloc`` of ``api.mobaide.com@evil.example``, which DNS-resolves
      to ``evil.example``. A ``RELAY_SERVER_URL`` with user-info would silently
      retarget the HTTPS fetch to an attacker-controlled host.
    * **Cleartext refused by default** — ``ws://`` / ``http://`` would let a
      passive on-path attacker substitute the response (trust key) or feed an
      unsigned manifest body. Refused unless
      ``MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1`` is set (self-host
      loopback / local testing escape hatch — name kept for compatibility
      across the trust-key and manifest paths).
    """
    parsed = urlparse(relay_server_url)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(
            f"RELAY_SERVER_URL is not a valid URL: {relay_server_url!r}"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            f"RELAY_SERVER_URL must not contain user-info (user@/user:pass@): "
            f"{relay_server_url!r}"
        )
    scheme_map = {"wss": "https", "ws": "http", "https": "https", "http": "http"}
    scheme = scheme_map.get(parsed.scheme.lower())
    if scheme is None:
        raise ValueError(
            f"RELAY_SERVER_URL has unsupported scheme {parsed.scheme!r}: "
            f"{relay_server_url!r}"
        )
    if scheme == "http" and os.environ.get(
        ALLOW_INSECURE_HTTPS_BOOTSTRAP_ENV
    ) != "1":
        raise ValueError(
            f"HTTPS bootstrap fetch over cleartext {parsed.scheme!r} is refused "
            f"by default (RELAY_SERVER_URL={relay_server_url!r}). Set "
            f"{ALLOW_INSECURE_HTTPS_BOOTSTRAP_ENV}=1 to override (only safe "
            f"on a trusted local network, e.g. self-host loopback)."
        )
    # Rebuild from hostname + optional :port — never from raw netloc — so
    # user-info or other smuggled netloc components can't survive into the
    # outbound URL even if a future refactor drops the explicit guard above.
    if ":" in parsed.hostname and not parsed.hostname.startswith("["):
        host = f"[{parsed.hostname}]"
    else:
        host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{scheme}://{host}{endpoint_path}"


def trust_key_url_from_relay_server_url(relay_server_url: str) -> str:
    """Resolve ``<scheme>://<host>/api/v1/agent/trust_key`` from a relay URL."""
    return endpoint_url_from_relay_server_url(relay_server_url, TRUST_KEY_ENDPOINT_PATH)


def manifest_url_from_relay_server_url(relay_server_url: str) -> str:
    """Resolve ``<scheme>://<host>/api/v1/agent/manifest.json`` from a relay URL.

    The agent's HTTPS-bootstrap update path uses this to reach the signed
    manifest independent of the relay WebSocket — so a protocol-version
    mismatch (close code 4002) can self-heal by fetching the latest
    manifest, staging it, and respawning into the new release.
    """
    return endpoint_url_from_relay_server_url(relay_server_url, MANIFEST_ENDPOINT_PATH)

"""Boot-time QR pairing autorun for agents in ``pairing_pending`` state.

Runs as a single asyncio task scheduled by ``app.py``'s lifespan when the
agent boots without ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` in its
``.env``. The task initiates a pairing nonce against the central server,
renders the QR to stdout, opens the pairing WebSocket, and waits for the
mobile-driven claim push. On success it atomic-writes the credentials
into ``.env`` and asks the supervisor to restart us so the lifespan
reruns with ``pairing_pending=False`` and starts the relay client.

Failures (network, signature, TTL) loop with backoff; the task only
exits on cancellation or unrecoverable trust-key absence.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
import sys
import time
import urllib.error
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import mobaide._http as _http

from ._env_writer import (
    atomic_write,
    env_path_for_prefix,
    parse_env_file,
    render_template,
    restrict_perms,
)
from .install_paths import default_prefix, read_trust_public_key
from .pairing_qr import (
    PendingPairing,
    clear_pending_pairing,
    compute_trust_key_fp,
    load_pending_pairing,
    parse_qr_payload,
    render_qr_to_terminal,
    save_pending_pairing,
)
from .pairing_ws import PairingClaim, PairingWsClient, PairingWsError
from .updater import InstallLayout

logger = logging.getLogger(__name__)

_INITIATE_PATH = "/api/pairing/initiate"
_BACKOFF_FLOOR_S = 2.0
_BACKOFF_CEIL_S = 60.0
_ALLOW_INSECURE_FETCH_ENV = "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH"


def _initiate_url(relay_server_url: str) -> str:
    """Translate ``RELAY_SERVER_URL`` into the HTTP initiate URL."""
    from mobaide.__main__ import _trust_key_url_from_relay_server_url

    base = _trust_key_url_from_relay_server_url(relay_server_url)
    return base.removesuffix("/api/v1/agent/trust_key") + _INITIATE_PATH


def _pairing_ws_url(relay_server_url: str, nonce: str) -> str:
    """Derive the pairing WS URL from ``RELAY_SERVER_URL`` + ``nonce``.

    Single source of truth: the agent computes the URL it dials rather
    than trusting a server-supplied ``ws_url`` field. Eliminates a class
    of deployment-skew bugs where a stale central_server returns
    ``https://...`` for ``ws_url``; the Python ``websockets`` library
    hard-rejects anything but ``ws``/``wss``.

    Scheme map:
    * ``wss://host``  → ``wss://host/ws/pairing/{nonce}``
    * ``ws://host``   → ``ws://host/ws/pairing/{nonce}``
    * ``https://host`` → ``wss://host/ws/pairing/{nonce}``
    * ``http://host``  → ``ws://host/ws/pairing/{nonce}``

    Cleartext (``ws://`` / ``http://``) is refused unless
    ``MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1`` is set, mirroring
    the trust-key fetch's same loopback-only escape hatch (see
    ``_trust_key_url_from_relay_server_url`` in ``mobaide.__main__``).

    Any path/query/fragment on the relay URL is intentionally discarded
    — the WS endpoint is pinned at ``/ws/pairing/{nonce}`` regardless of
    the relay-tunnel path.
    """
    parts = urlsplit(relay_server_url)
    if not parts.scheme or not parts.hostname:
        raise ValueError(
            f"RELAY_SERVER_URL is not a valid URL: {relay_server_url!r}"
        )
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            f"RELAY_SERVER_URL must not contain user-info "
            f"(user@/user:pass@): {relay_server_url!r}"
        )
    scheme = parts.scheme.lower()
    if scheme in ("https", "wss"):
        ws_scheme = "wss"
    elif scheme in ("http", "ws"):
        ws_scheme = "ws"
    else:
        raise ValueError(
            f"RELAY_SERVER_URL has unsupported scheme {scheme!r}: "
            f"{relay_server_url!r}"
        )
    if ws_scheme == "ws" and os.environ.get(_ALLOW_INSECURE_FETCH_ENV) != "1":
        raise ValueError(
            f"Pairing over cleartext {scheme!r} is refused by default "
            f"(RELAY_SERVER_URL={relay_server_url!r}). Set "
            f"{_ALLOW_INSECURE_FETCH_ENV}=1 to override (only safe on a "
            f"trusted local network)."
        )
    if ":" in parts.hostname and not parts.hostname.startswith("["):
        host = f"[{parts.hostname}]"
    else:
        host = parts.hostname
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    path = f"/ws/pairing/{nonce}"
    derived = urlunsplit((ws_scheme, netloc, path, "", ""))
    if not derived.startswith(("ws://", "wss://")):
        raise ValueError(
            f"derived pairing WS URL has unexpected scheme: {derived!r}"
        )
    return derived


def initiate_pairing(
    relay_server_url: str,
    pinned_trust_key_fp: str,
    *,
    hostname: str | None = None,
    os_name: str | None = None,
    current_agent_id: str | None = None,
) -> PendingPairing:
    """Synchronous HTTP call to ``POST /api/pairing/initiate``.

    Synchronous so it can be called from both the CLI (``pair-qr``) and
    the asyncio app via ``asyncio.to_thread``. Validates the
    fingerprint the server returns matches our pinned key — fail-closed
    against trust-key rotation we haven't picked up.
    """
    url = _initiate_url(relay_server_url)
    body = {}
    if hostname:
        body["hostname"] = hostname
    if os_name:
        body["os"] = os_name
    if current_agent_id:
        body["current_agent_id"] = current_agent_id
    data = json.dumps(body).encode("utf-8")
    with _http.urlopen(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
        timeout=30,
    ) as resp:
        if resp.status != 200:
            raise PairingMonitorError(
                f"initiate returned HTTP {resp.status}"
            )
        record = json.loads(resp.read().decode("utf-8"))
    nonce = record["nonce"]
    expires_at = record["expires_at"]
    server_fp = record["trust_key_fp"]
    relay_url = record["relay_url"]
    qr_payload = record["qr_payload"]
    # WS URL is derived locally from RELAY_SERVER_URL — see
    # ``_pairing_ws_url`` for the deployment-skew rationale.
    ws_url = _pairing_ws_url(relay_server_url, nonce)

    if server_fp != pinned_trust_key_fp:
        raise PairingMonitorError(
            f"trust key fingerprint mismatch: server announced "
            f"{server_fp!r}, agent has pinned {pinned_trust_key_fp!r}; "
            f"refuse to render a QR a mobile can't safely scan"
        )
    parsed = parse_qr_payload(qr_payload)
    if parsed is None:
        raise PairingMonitorError(
            f"server returned a malformed QR payload: {qr_payload!r}"
        )

    return PendingPairing(
        nonce=nonce,
        expires_at=expires_at,
        trust_key_fp=server_fp,
        ws_url=ws_url,
        relay_url=relay_url,
        qr_payload=qr_payload,
    )


class PairingMonitorError(Exception):
    """Raised when initiate or WS-wait fails irrecoverably for one cycle."""


async def write_credentials_to_env(
    env_path: Path, claim: PairingClaim
) -> None:
    """Atomically write ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` into .env.

    Reuses the same ``render_template`` / ``atomic_write`` /
    ``restrict_perms`` triple as ``init-env`` and the legacy ``pair``
    verb. Backfills ``RELAY_SERVER_URL`` if it was somehow blanked.
    """
    template_text = (
        env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    )
    existing = parse_env_file(template_text)
    overrides = {
        "RELAY_AGENT_ID": claim.agent_id,
        "RELAY_AGENT_TOKEN": claim.agent_token,
    }
    if not existing.get("RELAY_SERVER_URL"):
        overrides["RELAY_SERVER_URL"] = claim.relay_url
    rendered = render_template(template_text, overrides=overrides)
    atomic_write(env_path, rendered)
    restrict_perms(env_path)


async def _render_pending_to_stdout(pending: PendingPairing) -> None:
    """Print the QR header + the QR itself for SSH-attached operators."""
    sys.stdout.write(
        "[mobaide-agent] Pairing QR — scan from the Mobaide mobile app:\n"
    )
    render_qr_to_terminal(pending.qr_payload)
    sys.stdout.write(
        f"[mobaide-agent] Pairing nonce expires at {pending.expires_at}\n"
    )
    sys.stdout.flush()


async def _initiate_with_metadata(
    relay_server_url: str,
    layout: InstallLayout,
    *,
    hostname: str,
    os_name: str,
    current_agent_id: str | None,
) -> PendingPairing:
    """Wrapper that runs the synchronous initiate in a thread pool."""
    pinned_fp_b64 = read_trust_public_key(layout)
    if not pinned_fp_b64:
        raise PairingMonitorError("trust key not pinned")
    pinned_fp = compute_trust_key_fp(pinned_fp_b64)
    return await asyncio.to_thread(
        initiate_pairing,
        relay_server_url,
        pinned_fp,
        hostname=hostname,
        os_name=os_name,
        current_agent_id=current_agent_id,
    )


async def run_pairing_monitor(
    *,
    layout: InstallLayout,
    relay_server_url: str,
    current_agent_id: str | None = None,
    on_paired: Callable[[PairingClaim], Awaitable[None]] | None = None,
) -> None:
    """Drive the pairing flow until paired or cancelled.

    On success: writes credentials to .env, optionally awaits
    ``on_paired(claim)`` so the caller can transition the running
    process into the operational state (start the relay client
    in-process — see ``app.py``'s ``_on_paired`` for the production
    callback), then exits.

    The ``on_paired`` callback receives the validated ``PairingClaim``
    so the caller has direct access to the just-received credentials
    without re-reading ``.env``. Earlier designs used a no-arg
    callback that triggered a process restart so the next cold boot
    would pick up the persisted credentials; that worked but depended
    on a fragile chain of exit-code propagation through
    asyncio/uvicorn/launcher/supervisor that the in-process design
    avoids entirely.
    """
    env_path = env_path_for_prefix(layout.prefix)
    pinned_pub_key = read_trust_public_key(layout)
    if not pinned_pub_key:
        logger.error(
            "pairing_monitor.no_trust_key path=%s; cannot proceed. "
            "Run `mobaide-agent fetch-trust-key` to bootstrap.",
            layout.trust_public_key,
        )
        return

    backoff = _BACKOFF_FLOOR_S
    while True:
        pending = load_pending_pairing(layout)
        if pending is None:
            try:
                pending = await _initiate_with_metadata(
                    relay_server_url,
                    layout,
                    hostname=socket.gethostname(),
                    os_name=platform.system(),
                    current_agent_id=current_agent_id,
                )
                save_pending_pairing(layout, pending)
                backoff = _BACKOFF_FLOOR_S
            except (PairingMonitorError, urllib.error.URLError, OSError) as exc:
                logger.warning(
                    "pairing_monitor.initiate_failed: %s (retry in %.1fs)",
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(_BACKOFF_CEIL_S, backoff * 2)
                continue

        await _render_pending_to_stdout(pending)

        parsed = parse_qr_payload(pending.qr_payload)
        if parsed is None:
            clear_pending_pairing(layout)
            continue
        timeout_s = max(1.0, parsed.exp_unix - time.time())

        try:
            claim = await PairingWsClient().wait_for_claim(
                ws_url=pending.ws_url,
                timeout_s=timeout_s,
                trust_pub_key_b64=pinned_pub_key,
                expected_relay_url=pending.relay_url,
            )
        except PairingWsError as exc:
            logger.warning("pairing_monitor.ws_failed: %s", exc)
            clear_pending_pairing(layout)
            await asyncio.sleep(_BACKOFF_FLOOR_S)
            continue

        logger.info(
            "pairing_monitor.writing_credentials env_path=%s agent_id=%s",
            env_path, claim.agent_id,
        )
        try:
            await write_credentials_to_env(env_path, claim)
        except Exception as exc:
            # Defence-in-depth: an unhandled exception here would silently
            # kill the pairing-monitor task (the task is created via
            # ``asyncio.create_task`` in ``app.py`` and never awaited, so
            # exceptions don't propagate). The original Windows surface
            # was ``PermissionError`` (subclass of ``OSError``) from
            # ``os.replace`` under a ``SYSTEM:R`` ACL — fixed at the ACL
            # layer in ``_restrict_perms_windows`` — but
            # ``write_credentials_to_env`` can also raise
            # ``UnicodeDecodeError`` on a corrupt-encoding ``.env`` or
            # ``ValueError`` from ``render_template``'s control-character
            # injection guard, neither of which are ``OSError`` subclasses.
            # Catch every non-Cancellation exception so the task survives
            # any failure mode and the operator sees the cause in
            # ``agent.err.log`` rather than a silent task death. The
            # ``asyncio.CancelledError`` raised by lifespan-shutdown
            # ``pairing_task.cancel()`` is a ``BaseException`` subclass
            # and is not caught here, so cancellation still propagates.
            logger.error(
                "pairing_monitor.write_failed path=%s exc_type=%s: %s; will retry",
                env_path,
                type(exc).__name__,
                exc,
            )
            clear_pending_pairing(layout)
            await asyncio.sleep(_BACKOFF_FLOOR_S)
            continue
        clear_pending_pairing(layout)
        logger.info(
            "pairing_monitor.paired agent_id=%s relay=%s",
            claim.agent_id,
            claim.relay_url,
        )
        if on_paired is not None:
            try:
                await on_paired(claim)
            except Exception as exc:
                logger.warning(
                    "pairing_monitor.on_paired_callback_failed: %s", exc
                )
        return


def default_layout() -> InstallLayout:
    """Convenience: layout for the current runtime prefix."""
    return InstallLayout(prefix=default_prefix())

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
import random
import socket
import sys
import time
import urllib.error as urllib_error
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import mobaide._http as _http
from websockets.exceptions import InvalidHandshake

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
    encode_qr_payload,
    load_pending_pairing,
    parse_qr_payload,
    render_qr_to_terminal,
    save_pending_pairing,
    write_pairing_hints,
)
from .pairing_ws import PairingClaim, PairingWsClient, PairingWsError
from .updater import InstallLayout

logger = logging.getLogger(__name__)

_INITIATE_PATH = "/api/pairing/initiate"
_DEVICE_CODE_REDEEM_PATH = "/api/pairing/device-code/redeem"
_DEVICE_CODE_ACCEPT_PATH = "/api/pairing/device-code/accept"
_BACKOFF_FLOOR_S = 2.0
_BACKOFF_CEIL_S = 60.0
# A claim wait that lasted this long proves the relay, the WS path and the
# local clock all work, so the next cycle may start from the backoff floor.
# Anything shorter is a fast failure and must keep growing the backoff.
_HEALTHY_WAIT_S = 30.0
from .install_paths import ALLOW_INSECURE_HTTPS_BOOTSTRAP_ENV as _ALLOW_INSECURE_FETCH_ENV


def _nonce_still_valid(pending: PendingPairing) -> bool:
    """Same local-clock expiry test ``load_pending_pairing`` applies."""
    parsed = parse_qr_payload(pending.qr_payload)
    return parsed is not None and parsed.exp_unix > int(time.time())


def _handshake_status(cause: BaseException | None) -> int | None:
    """HTTP status of a rejected WS handshake, across websockets versions.

    ``InvalidStatus`` (websockets >= 14) carries ``response.status_code``;
    the legacy ``InvalidStatusCode`` (13) carries ``status_code``. Read by
    attribute so the deprecated legacy class is never imported.
    """
    if not isinstance(cause, InvalidHandshake):
        return None
    response = getattr(cause, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(cause, "status_code", None)
    return status if isinstance(status, int) else None


def _initiate_url(relay_server_url: str) -> str:
    """Translate ``RELAY_SERVER_URL`` into the HTTP initiate URL."""
    from .install_paths import endpoint_url_from_relay_server_url

    return endpoint_url_from_relay_server_url(relay_server_url, _INITIATE_PATH)


def _device_code_redeem_url(relay_server_url: str) -> str:
    """Translate ``RELAY_SERVER_URL`` into the HTTP device-code redeem URL."""
    from .install_paths import endpoint_url_from_relay_server_url

    return endpoint_url_from_relay_server_url(
        relay_server_url, _DEVICE_CODE_REDEEM_PATH
    )


def _device_code_accept_url(relay_server_url: str) -> str:
    """Translate ``RELAY_SERVER_URL`` into the HTTP device-code accept URL."""
    from .install_paths import endpoint_url_from_relay_server_url

    return endpoint_url_from_relay_server_url(
        relay_server_url, _DEVICE_CODE_ACCEPT_PATH
    )


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
    ``endpoint_url_from_relay_server_url`` in ``mobaide.agent.install_paths``).

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


class DeviceCodeRejected(Exception):
    """The relay refused a device code (unknown, expired, spent, throttled)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def redeem_device_code(
    relay_server_url: str,
    pinned_trust_key_fp: str,
    *,
    code: str,
    redeemer_id: str,
    hostname: str | None = None,
    os_name: str | None = None,
    current_agent_id: str | None = None,
) -> tuple[PendingPairing | None, str, bool]:
    """Exchange a retyped device code for a pairing nonce.

    The reversed-direction twin of ``initiate_pairing``: same fail-closed
    fingerprint comparison, same locally-derived WS URL, same
    ``PendingPairing`` result. Returns
    ``(pending, account_hint, is_repair)``.

    ``pending`` is ``None`` when the relay reports the code was already
    redeemed by this same ``redeemer_id`` — the nonce is not stored
    server-side, so a caller that also lost its local copy has to mint a
    fresh code rather than resume.
    """
    url = _device_code_redeem_url(relay_server_url)
    body: dict[str, str] = {"code": code, "redeemer_id": redeemer_id}
    if hostname:
        body["hostname"] = hostname
    if os_name:
        body["os"] = os_name
    if current_agent_id:
        body["current_agent_id"] = current_agent_id
    data = json.dumps(body).encode("utf-8")
    try:
        with _http.urlopen(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
            timeout=30,
        ) as resp:
            if resp.status != 200:
                raise DeviceCodeRejected(
                    f"redeem returned HTTP {resp.status}", status=resp.status
                )
            record = json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        raise DeviceCodeRejected(
            _redeem_error_message(exc), status=exc.code
        ) from exc

    server_fp = record["trust_key_fp"]
    if server_fp != pinned_trust_key_fp:
        raise PairingMonitorError(
            f"trust key fingerprint mismatch: server announced "
            f"{server_fp!r}, agent has pinned {pinned_trust_key_fp!r}; "
            f"refuse to pair against a relay this agent does not trust"
        )

    account_hint = record.get("account_hint") or "unknown account"
    is_repair = bool(record.get("is_repair"))
    nonce = record.get("nonce")
    if not nonce:
        return None, account_hint, is_repair

    relay_url = record["relay_url"]
    expires_at = record["expires_at"]
    exp_unix = int(datetime.fromisoformat(expires_at).timestamp())
    qr_payload = encode_qr_payload(relay_url, nonce, server_fp, exp_unix)
    return (
        PendingPairing(
            nonce=nonce,
            expires_at=expires_at,
            trust_key_fp=server_fp,
            ws_url=_pairing_ws_url(relay_server_url, nonce),
            relay_url=relay_url,
            qr_payload=qr_payload,
        ),
        account_hint,
        is_repair,
    )


def accept_device_code(
    relay_server_url: str, *, code: str, redeemer_id: str
) -> None:
    """Tell the relay the operator approved this pairing on the terminal.

    Until this lands the relay reports the request as still pending, so the
    browser cannot confirm and nothing is created or rotated. That is what
    keeps the terminal prompt — the reversed direction's primary control —
    from being a message the account holder can simply outrun.

    Sent on every run that gets past the prompt, including a resume: the
    relay's update is idempotent, so re-sending is cheaper than persisting
    whether the first attempt was acknowledged.
    """
    url = _device_code_accept_url(relay_server_url)
    data = json.dumps({"code": code, "redeemer_id": redeemer_id}).encode("utf-8")
    try:
        with _http.urlopen(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
            timeout=30,
        ) as resp:
            if resp.status not in (200, 204):
                raise DeviceCodeRejected(
                    f"accept returned HTTP {resp.status}", status=resp.status
                )
    except urllib_error.HTTPError as exc:
        raise DeviceCodeRejected(
            _redeem_error_message(exc), status=exc.code
        ) from exc


def _redeem_error_message(exc) -> str:
    """Pull the relay's ``detail`` out of an error response, else summarise.

    The redeem rejections are all operator-facing (wrong code, expired
    code, throttled), so the server's own wording is worth surfacing
    verbatim rather than replacing with a status number.
    """
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return f"redeem returned HTTP {exc.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and detail.get("retry_after_seconds"):
        return (
            "too many pairing attempts against this relay; retry in "
            f"{detail['retry_after_seconds']}s"
        )
    return f"redeem returned HTTP {exc.code}"


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
    """Print the QR, the pasteable payload and the browser link for operators.

    The stdout QR is a convenience for operators tailing the service log
    or attached over SSH. The cross-process signal for ``install.ps1``'s
    ``Show-PairingQrFromService`` is ``pending_pairing.json``, which the
    caller has already persisted before this runs, and the central
    server's mobile-claim flow is independent. So a failure here — a
    rare ``UnicodeEncodeError`` on a non-UTF-8 redirected stdout that
    slipped past the renderer's encoding probe, a closed stdout, an
    OSError on a full disk — must NOT propagate. If it did, it would
    abort the surrounding ``async with PairingWsClient.connect(...)``
    block before ``wait_for_claim`` is awaited, the WS would tear down,
    the same nonce would then be rejected with HTTP 403 on every
    reconnect, and pairing would silently fail (the failure mode that
    motivated this safety net — see ``agent.out.log`` from the original
    incident).
    """
    try:
        sys.stdout.write(
            "[mobaide-agent] Pairing QR — scan from the Mobaide mobile app:\n"
        )
        render_qr_to_terminal(pending.qr_payload)
        write_pairing_hints(pending.qr_payload)
        sys.stdout.write(
            f"[mobaide-agent] Pairing nonce expires at {pending.expires_at}\n"
        )
        sys.stdout.flush()
    except Exception as exc:
        logger.warning(
            "pairing_monitor.qr_render_skipped exc_type=%s exc=%s nonce=%s",
            type(exc).__name__, exc, pending.nonce[:8],
        )


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

    Outer-loop exception policy: every iteration runs inside a
    catch-all ``try/except Exception`` so an unanticipated exception
    class (``json.JSONDecodeError`` from a proxy error page,
    ``KeyError`` from a partial server response, ``ValueError`` from
    URL parsing, ...) cannot kill the task silently. The pairing task
    is scheduled with ``asyncio.create_task`` in ``app.py``'s lifespan
    and is not awaited until shutdown — without this guard, an uncaught
    exception only surfaces via Python's "Task exception was never
    retrieved" GC log, which on a long-running service may never trigger.
    ``asyncio.CancelledError`` is NOT caught (it's a ``BaseException``
    subclass) so lifespan shutdown still cancels the task cleanly.
    """
    logger.info(
        "pairing_monitor.task_started prefix=%s relay_server_url=%s "
        "current_agent_id=%s",
        layout.prefix, relay_server_url,
        (current_agent_id[:8] + "…") if current_agent_id else None,
    )
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
    # A fresh nonce lives only here until the WS reaches ``ready`` (see the
    # save below). Reusing it after a pre-``ready`` WS failure keeps a
    # broken WS path from minting a new nonce on every retry.
    unsaved: PendingPairing | None = None
    # Set once a 403 has discarded a nonce, cleared on ``ready``. A proxy
    # that refuses every upgrade with 403 then costs one extra initiate,
    # then one per TTL, not one per retry.
    dropped_after_403 = False
    while True:
        try:
            pending = load_pending_pairing(layout)
            logger.info(
                "pairing_monitor.loop_iter has_pending=%s env_path=%s",
                pending is not None, env_path,
            )
            fresh_nonce = False
            if pending is None and unsaved is not None:
                if _nonce_still_valid(unsaved):
                    pending = unsaved
                    fresh_nonce = True
                else:
                    unsaved = None
            if pending is None:
                try:
                    pending = await _initiate_with_metadata(
                        relay_server_url,
                        layout,
                        hostname=socket.gethostname(),
                        os_name=platform.system(),
                        current_agent_id=current_agent_id,
                    )
                    fresh_nonce = True
                    unsaved = pending
                except Exception as exc:
                    # Catch-all rather than the narrow (PairingMonitorError,
                    # URLError, OSError) triplet: the server can return
                    # non-JSON behind a proxy error page (JSONDecodeError),
                    # miss expected fields (KeyError), or _http.urlopen can
                    # raise something we haven't anticipated. The task is
                    # scheduled via asyncio.create_task and never awaited
                    # before lifespan shutdown, so an uncaught exception
                    # here dies silently. We back off and retry — pairing
                    # is eventually-consistent against transient outages.
                    logger.warning(
                        "pairing_monitor.initiate_failed exc_type=%s exc=%s "
                        "(retry in %.1fs)",
                        type(exc).__name__, exc, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(_BACKOFF_CEIL_S, backoff * 2)
                    continue
                if not _nonce_still_valid(pending):
                    logger.warning(
                        "pairing_monitor.clock_skew nonce=%s expires_at=%s "
                        "local_now=%s: the relay's fresh nonce is already "
                        "expired by this machine's clock, so the system clock "
                        "is ahead of the relay. Pairing cannot complete until "
                        "the clock is corrected (enable NTP time sync).",
                        pending.nonce[:8], pending.expires_at,
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                    )

            parsed = parse_qr_payload(pending.qr_payload)
            if parsed is None:
                logger.warning(
                    "pairing_monitor.malformed_pending_qr_payload "
                    "nonce_prefix=%s — clearing and re-initiating",
                    pending.nonce[:8],
                )
                clear_pending_pairing(layout)
                unsaved = None
                continue
            timeout_s = max(1.0, parsed.exp_unix - time.time())

            # Two-phase WS handshake: open the connection and await
            # ``"ready"`` BEFORE save_pending_pairing or QR render. The
            # ``pending_pairing.json`` on-disk file is the install-time
            # signal (`install.ps1`'s Show-PairingQrFromService polls it)
            # that the agent is ready to receive a claim. If we wrote the
            # file before the WS was open, a fast mobile scan could claim
            # the nonce while the agent was still TCP/TLS-handshaking; the
            # server's claim handler would fall through to the recovery
            # path on our later WS reconnect, and the recovery path
            # historically re-signed the push with a different ``relay_url``
            # than the initiate handler produced — agent then rejected its
            # own credentials. Anchoring the on-disk signal to the WS-ready
            # handshake closes the race at the source.
            wait_started: float | None = None
            try:
                async with PairingWsClient.connect(pending.ws_url) as conn:
                    # Server is now in the ready-for-dispatch state. Safe
                    # to publish the pending-pairing file and render the QR.
                    if fresh_nonce:
                        save_pending_pairing(layout, pending)
                        unsaved = None
                    dropped_after_403 = False
                    await _render_pending_to_stdout(pending)
                    wait_started = time.monotonic()
                    claim = await conn.wait_for_claim(
                        timeout_s=timeout_s,
                        trust_pub_key_b64=pinned_pub_key,
                        expected_relay_url=pending.relay_url,
                    )
            except PairingWsError as exc:
                # Do NOT clear pending_pairing.json. A WS drop mid-wait
                # (CDN idle-close, TLS reset, proxy hiccup) can race against
                # a successful mobile claim: the central server's
                # ``claim_pairing_nonce`` persists ``claimed_token_raw`` BEFORE
                # ``dispatch_to_waiter`` and returns 200 to the mobile
                # regardless of WS delivery — so the mobile shows "Paired"
                # while the credentials sit unconsumed server-side. Reconnecting
                # to the SAME nonce on the next iteration lets the server's
                # ``read_and_clear_claimed_credentials`` recovery path deliver
                # those stranded credentials (see
                # ``central_server/pairing/router.py`` :func:`pairing_ws`).
                # The natural TTL check in :func:`load_pending_pairing`
                # evicts the entry once the nonce truly expires, so a
                # permanently broken WS (signature mismatch, dead server)
                # still rolls over to a fresh nonce within the 120 s TTL.
                #
                # Backoff resets only after a long, healthy wait (a TTL
                # rollover or a late drop). A fast failure keeps doubling it,
                # otherwise every retry would re-initiate at the floor
                # interval and hammer the relay's anonymous initiate endpoint.
                if (
                    wait_started is not None
                    and time.monotonic() - wait_started >= _HEALTHY_WAIT_S
                ):
                    backoff = _BACKOFF_FLOOR_S
                # The relay refuses a bad nonce (duplicate waiter, unknown,
                # TTL) before ``accept()``, which surfaces as HTTP 403: mint
                # a fresh one. Other handshake failures keep reusing it, so
                # a broken WS path stays at one initiate per TTL.
                if (
                    unsaved is not None
                    and not dropped_after_403
                    and _handshake_status(exc.__cause__) == 403
                ):
                    unsaved = None
                    dropped_after_403 = True
                logger.warning(
                    "pairing_monitor.ws_failed nonce=%s: %s; retaining pending so "
                    "the next reconnect can trigger the server's recovery path "
                    "(sleep=%.2fs)",
                    pending.nonce[:8], exc, backoff,
                )
                await asyncio.sleep(backoff + random.uniform(0, 1))
                backoff = min(_BACKOFF_CEIL_S, backoff * 2)
                continue

            # The claim is single-use server-side — once dispatched-or-recovered
            # by the WS handler, the server cleared ``claimed_token_raw`` and
            # cannot re-deliver. Falling back to the outer WS loop on a write
            # failure would re-initiate a new nonce and orphan the credentials.
            # Hold the claim in memory and retry the WRITE locally until it
            # lands. ``asyncio.CancelledError`` is a ``BaseException`` subclass
            # and is not caught below, so lifespan-shutdown still cancels the
            # task cleanly.
            write_attempts = 0
            write_backoff = _BACKOFF_FLOOR_S
            while True:
                write_attempts += 1
                logger.info(
                    "pairing_monitor.writing_credentials env_path=%s agent_id=%s "
                    "attempt=%d",
                    env_path, claim.agent_id, write_attempts,
                )
                try:
                    await write_credentials_to_env(env_path, claim)
                    break
                except Exception as exc:
                    logger.error(
                        "pairing_monitor.write_failed attempt=%d path=%s "
                        "exc_type=%s: %s; retrying the write in %.1fs (claim is "
                        "single-use server-side; cannot fall back to a fresh WS dial)",
                        write_attempts,
                        env_path,
                        type(exc).__name__,
                        exc,
                        write_backoff,
                    )
                    await asyncio.sleep(write_backoff)
                    write_backoff = min(_BACKOFF_CEIL_S, write_backoff * 2)
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
        except Exception as exc:
            # Outer-loop safety net. The inner ``try/except`` blocks above
            # cover the named failure points (initiate, WS, write); this
            # catch-all only fires for an unanticipated raise from a code
            # path the inner handlers don't wrap — e.g. ``parse_qr_payload``
            # blowing up on a non-string ``qr_payload``, ``clear_pending_pairing``
            # raising on a permission-denied unlink, or a future refactor
            # introducing a new failure surface inside the loop body.
            # Without this guard those exceptions would escape the task and
            # die silently (see the function docstring).
            logger.exception(
                "pairing_monitor.loop_iter_unexpected exc_type=%s exc=%s "
                "(retry in %.1fs)",
                type(exc).__name__, exc, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(_BACKOFF_CEIL_S, backoff * 2)
            continue


def default_layout() -> InstallLayout:
    """Convenience: layout for the current runtime prefix."""
    return InstallLayout(prefix=default_prefix())

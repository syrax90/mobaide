"""HTTPS-bootstrap auto-update path for the running agent.

The relay tunnel delivers signed manifest pointers inline (in ``auth_ok``
and in ``agent_manifest_update`` control frames). That is the fast path
during steady-state operation but it is fragile across breaking protocol
bumps: an agent whose ``protocol_version`` is below the server's
``SUPPORTED_PROTOCOL_VERSIONS`` is rejected with close code 4002 before
the manifest pointer is ever sent, and the in-band update channel
becomes circular — the binary that would lift the agent into the new
protocol can never be delivered.

This module is the side-channel that breaks the cycle. It fetches the
already-signed manifest over plain HTTPS from
``GET /api/v1/agent/manifest.json`` on the central server and feeds it
into the same :class:`StagedBinaryUpdater` the relay path uses, so the
crypto verification and on-disk swap stay identical regardless of the
delivery channel. It is invoked on three triggers:

* once at lifespan startup, before the relay client first connects, so
  a freshly-booted outdated agent self-heals during cold boot;
* immediately on ``ProtocolIncompatibleError`` from the relay client,
  so an agent that was running fine until a server-side protocol bump
  detects the rejection, applies the manifest, and respawns;
* on a 60-minute background poll, so any manifest the relay missed
  (queue saturation, server-side rebroadcast bug, etc.) is reconciled
  within the hour.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error

import mobaide._http as _http
from mobaide.agent.updater import StagedBinaryUpdater

logger = logging.getLogger(__name__)


class UpdateCheckError(Exception):
    """Raised when an HTTPS manifest fetch cannot be parsed."""


PERIODIC_POLL_INTERVAL_SECONDS = 60 * 60


async def fetch_signed_manifest(
    url: str, *, timeout: float = 30.0
) -> tuple[dict, str, str]:
    """Fetch the signed manifest from ``url`` and return its parts.

    Returns ``(body, signature_b64, latest_version)``. Network and parse
    failures raise :class:`UpdateCheckError` so callers can decide
    whether to log-and-continue (the periodic poll) or escalate (a
    forced check after 4002). The HTTP call goes through
    :mod:`mobaide._http` so the canonical ``User-Agent`` header is set
    — Cloudflare Bot Fight Mode blocks the default ``Python-urllib/X.Y``
    UA before the request reaches the origin.
    """

    def _fetch() -> str:
        with _http.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    try:
        raw = await asyncio.to_thread(_fetch)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise UpdateCheckError(f"cannot reach {url}: {exc}") from exc

    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise UpdateCheckError(f"malformed response from {url}: {exc}") from exc

    body = record.get("body") if isinstance(record, dict) else None
    signature_b64 = record.get("signature_b64") if isinstance(record, dict) else None
    if not isinstance(body, dict) or not isinstance(signature_b64, str):
        raise UpdateCheckError(
            f"manifest response from {url} missing body/signature_b64"
        )
    latest_version = body.get("version", "")
    if not isinstance(latest_version, str) or not latest_version:
        raise UpdateCheckError(
            f"manifest body from {url} missing version field"
        )
    return body, signature_b64, latest_version


async def run_https_update_check(
    updater: StagedBinaryUpdater,
    manifest_url: str,
    *,
    force: bool = False,
) -> str:
    """Fetch the signed manifest over HTTPS and apply it via ``updater``.

    Returns one of:

    * ``"no_change"`` — agent already on the published version.
    * ``"deferred"`` — busy sessions; updater will retry later.
    * ``"staged"`` — release staged; the updater has already invoked
      ``sys.exit(EXIT_RESTART_REQUESTED)`` and the supervisor is about
      to respawn (under test injection the call returns instead).
    * ``"signature_failed"`` — fail-closed; stays on current version.
    * ``"unreachable"`` — transport or parse failure; the caller logs
      and moves on. The periodic poll specifically depends on this
      being non-raising so transient DNS / TLS hiccups don't bubble
      up into the lifespan or the relay loop.
    """
    try:
        body, signature_b64, latest_version = await fetch_signed_manifest(
            manifest_url,
        )
    except UpdateCheckError as exc:
        logger.warning("update_check.unreachable url=%s: %s", manifest_url, exc)
        return "unreachable"

    outcome = await updater.consider_manifest(
        body, signature_b64, latest_version, force=force,
    )
    logger.info(
        "update_check.outcome url=%s latest=%s outcome=%s force=%s",
        manifest_url, latest_version, outcome, force,
    )
    return outcome


async def run_periodic_check_loop(
    updater: StagedBinaryUpdater,
    manifest_url: str,
    *,
    interval_seconds: float = PERIODIC_POLL_INTERVAL_SECONDS,
) -> None:
    """Run :func:`run_https_update_check` on a fixed cadence forever.

    Exists as the body of a long-lived ``asyncio.Task``. Sleeps before
    the first check so the lifespan's pre-connect check (which fires
    inline at startup) and this loop don't race on the very first
    iteration. Cancellation propagates out (the agent's lifespan
    awaits the task in its ``finally`` block); any other exception is
    swallowed so a single bad iteration cannot tear down the
    background poll for the lifetime of the process.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        try:
            await run_https_update_check(updater, manifest_url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "update_check.periodic_iteration_failed url=%s: %s",
                manifest_url, exc,
            )

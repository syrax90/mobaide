"""Tests for the HTTPS-bootstrap update path in ``mobaide.agent.update_check``.

Three public entry points:

* ``fetch_signed_manifest`` — parses ``GET /api/v1/agent/manifest.json``
  into ``(body, signature_b64, latest_version)``; raises
  :class:`UpdateCheckError` on every failure mode so callers can decide
  whether to escalate (forced check after a 4002 rejection) or swallow
  (periodic poll).
* ``run_https_update_check`` — composes the fetch with an injected
  :class:`StagedBinaryUpdater` and returns the same outcome strings the
  inline relay path returns. Critically, the ``UpdateCheckError`` is
  converted to outcome ``"unreachable"`` so the periodic poll never
  raises.
* ``run_periodic_check_loop`` — long-lived task; cancellation propagates,
  every other exception is swallowed so a transient failure cannot
  silently tear down the background poll for the lifetime of the
  process.

Mocks rather than real HTTP: every test patches ``_http.urlopen`` via
``mobaide.agent.update_check._http.urlopen`` so no socket is opened.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mobaide.agent.update_check import (
    UpdateCheckError,
    fetch_signed_manifest,
    run_https_update_check,
    run_periodic_check_loop,
)


class _FakeResponse:
    """Mimic the urlopen context-manager protocol used by ``_http.urlopen``."""

    def __init__(self, body: str | bytes) -> None:
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _valid_manifest_record(version: str = "0.5.0") -> dict:
    return {
        "body": {
            "version": version,
            "tarball_url": "https://example.com/agent.tar.gz",
            "tarball_sha256": "0" * 64,
            "wheel_url": "https://example.com/agent.whl",
            "wheel_sha256": "1" * 64,
            "released_at": "2026-05-23T00:00:00+00:00",
        },
        "signature_b64": "sig-payload",
    }


def _patch_urlopen(*, body: str | bytes | None = None, side_effect=None):
    """Patch ``_http.urlopen`` inside ``update_check``."""
    if side_effect is not None:
        return patch(
            "mobaide.agent.update_check._http.urlopen",
            side_effect=side_effect,
        )
    return patch(
        "mobaide.agent.update_check._http.urlopen",
        return_value=_FakeResponse(body or ""),
    )


class TestFetchSignedManifestHappyPath:
    async def test_returns_body_signature_and_version(self):
        record = _valid_manifest_record(version="1.2.3")
        with _patch_urlopen(body=json.dumps(record)):
            body, sig, ver = await fetch_signed_manifest(
                "https://example.com/api/v1/agent/manifest.json",
            )

        assert body == record["body"]
        assert sig == "sig-payload"
        assert ver == "1.2.3"


class TestFetchSignedManifestErrors:
    """All failure modes must raise :class:`UpdateCheckError` rather than
    leaking a transport-layer exception type."""

    async def test_network_failure_wraps_urlerror(self):
        err = urllib.error.URLError("connection refused")
        with _patch_urlopen(side_effect=err):
            with pytest.raises(UpdateCheckError, match="cannot reach"):
                await fetch_signed_manifest("https://example.com/x")

    async def test_malformed_json_raises_update_check_error(self):
        with _patch_urlopen(body="not json at all {{"):
            with pytest.raises(UpdateCheckError, match="malformed response"):
                await fetch_signed_manifest("https://example.com/x")

    async def test_missing_body_field_raises(self):
        # Top-level dict but no ``body`` key.
        record = {"signature_b64": "sig"}
        with _patch_urlopen(body=json.dumps(record)):
            with pytest.raises(UpdateCheckError, match="missing body/signature_b64"):
                await fetch_signed_manifest("https://example.com/x")

    async def test_non_dict_top_level_raises(self):
        # A JSON array would parse but its ``.get()`` is undefined.
        with _patch_urlopen(body=json.dumps(["not", "a", "dict"])):
            with pytest.raises(UpdateCheckError, match="missing body/signature_b64"):
                await fetch_signed_manifest("https://example.com/x")

    async def test_missing_version_in_body_raises(self):
        # The version field is what the updater compares against the
        # running ``__version__`` — without it the check is meaningless.
        record = {
            "body": {"tarball_url": "https://example.com/a.tar.gz"},
            "signature_b64": "sig",
        }
        with _patch_urlopen(body=json.dumps(record)):
            with pytest.raises(UpdateCheckError, match="missing version field"):
                await fetch_signed_manifest("https://example.com/x")


class TestRunHttpsUpdateCheckOutcomes:
    """``run_https_update_check`` is the bridge between the HTTPS fetch
    and the existing :class:`StagedBinaryUpdater`. The key behavioural
    invariant is that ``UpdateCheckError`` is *converted* to outcome
    ``"unreachable"`` — never re-raised — so the periodic poll keeps
    going through transient transport failures."""

    async def test_unreachable_does_not_propagate(self):
        # Updater must not be consulted at all when the fetch fails —
        # otherwise a transient network blip could be misinterpreted
        # as a fresh manifest.
        updater = AsyncMock()
        with _patch_urlopen(side_effect=OSError("offline")):
            outcome = await run_https_update_check(
                updater, "https://example.com/x",
            )

        assert outcome == "unreachable"
        updater.consider_manifest.assert_not_awaited()

    async def test_force_flag_propagated_to_updater(self):
        # The 4002-fallback callback uses ``force=True`` to bypass the
        # activity-aware deferral; the regular periodic poll uses
        # ``force=False``. The pass-through must be exact.
        record = _valid_manifest_record()
        updater = MagicMock()
        updater.consider_manifest = AsyncMock(return_value="no_change")

        with _patch_urlopen(body=json.dumps(record)):
            await run_https_update_check(
                updater, "https://example.com/x", force=True,
            )

        call = updater.consider_manifest.await_args
        assert call.kwargs["force"] is True

    async def test_body_signature_and_version_forwarded(self):
        record = _valid_manifest_record(version="3.1.4")
        updater = MagicMock()
        updater.consider_manifest = AsyncMock(return_value="no_change")

        with _patch_urlopen(body=json.dumps(record)):
            await run_https_update_check(
                updater, "https://example.com/x",
            )

        call = updater.consider_manifest.await_args
        # Positional args are (body, signature_b64, latest_version)
        body, sig, ver = call.args
        assert body == record["body"]
        assert sig == "sig-payload"
        assert ver == "3.1.4"


class TestRunPeriodicCheckLoopBudget:
    """The poll task must (a) sleep before the first check, (b) survive
    exceptions on a single iteration, and (c) propagate
    :class:`asyncio.CancelledError` so the lifespan ``finally`` block
    can tear it down cleanly.

    Each test caps the iteration count by cancelling the task — never
    by letting it run free — so a regression that breaks the swallow
    behaviour can't hang the test suite.
    """

    async def test_sleeps_before_first_check(self):
        # Regression guard: if the loop calls the check before its first
        # sleep, the lifespan's inline pre-connect check and the periodic
        # poll race on the very first iteration. ``interval=99`` ensures
        # cancellation happens during the sleep, before any check fires.
        updater = MagicMock()
        with _patch_urlopen(body=json.dumps(_valid_manifest_record())):
            task = asyncio.create_task(
                run_periodic_check_loop(
                    updater, "https://example.com/x", interval_seconds=99,
                ),
            )
            await asyncio.sleep(0)  # yield so the task starts and hits sleep
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        updater.consider_manifest.assert_not_called()

    async def test_continues_after_failed_iteration(self):
        # A single failing iteration (the wrapped check raises a non-
        # CancelledError exception) must not tear down the loop —
        # subsequent iterations must keep running. This is the
        # "transient DNS hiccup" survival guarantee.
        call_count = {"n": 0}

        async def fake_check(*_args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient bus error")
            return "no_change"

        with patch(
            "mobaide.agent.update_check.run_https_update_check",
            side_effect=fake_check,
        ):
            task = asyncio.create_task(
                run_periodic_check_loop(
                    MagicMock(), "https://example.com/x", interval_seconds=0,
                ),
            )
            # Let the loop run a few iterations: 1 failing + ≥1 successful.
            for _ in range(20):
                await asyncio.sleep(0)
                if call_count["n"] >= 2:
                    break
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert call_count["n"] >= 2, (
            f"loop did not continue past the failing iteration "
            f"(call_count={call_count['n']})"
        )

    async def test_cancellation_propagates(self):
        # The lifespan's ``finally`` block awaits the task; a swallowed
        # CancelledError would deadlock the shutdown path. Explicitly
        # verify the bubbling.
        async def fake_check(*_args, **_kwargs):
            return "no_change"

        with patch(
            "mobaide.agent.update_check.run_https_update_check",
            side_effect=fake_check,
        ):
            task = asyncio.create_task(
                run_periodic_check_loop(
                    MagicMock(), "https://example.com/x", interval_seconds=0,
                ),
            )
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

"""Once-per-account "Agent paired" conversion upload.

``_complete_pairing`` calls ``schedule_first_pairing`` after the account's
``first_paired_at`` compare-and-set proved this is its first pairing ever.
The upload runs in a fire-and-forget task so a slow Google round-trip never
stalls the pairing response. Whatever the outcome, the account's attribution
columns are cleared afterwards; a stored click id or consent is what marks
an upload as pending, which is what lets ``resume_pending`` pick up uploads
a previous process never finished.

The Data Manager API answers an accepted upload with a request id only; the
match verdict is published later through ``requestStatus:retrieve``. A
best-effort follow-up task fetches it and logs ``pairing.conversion_status``
— diagnostic only, never persisted, dropped at shutdown.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .data_manager import (
    CLICK_MAX_AGE,
    ConversionIdentifier,
    DataManagerConversionClient,
    StatusOutcome,
    UploadOutcome,
    parse_iso_utc,
    select_identifier,
)

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database

logger = logging.getLogger(__name__)

_GOOGLE_ADS_FIELDS = (
    "google_ads_customer_id",
    "google_ads_login_customer_id",
    "google_ads_oauth_client_id",
    "google_ads_oauth_client_secret",
    "google_ads_oauth_refresh_token",
    "google_ads_conversion_action_id",
)
# Settings of the retired Google Ads API path. A leftover value is harmless,
# but one warning tells the operator it no longer does anything.
_OBSOLETE_ENV = ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_API_VERSION")

# Google publishes a verdict from roughly 30 minutes after ingest and within
# a day.
STATUS_FIRST_CHECK = 45 * 60.0
STATUS_RECHECK = 2 * 3600.0
STATUS_DEADLINE = 24 * 3600.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_counts(counts: Sequence[tuple[str, int]]) -> str:
    return ",".join(f"{reason}:{count}" for reason, count in counts) or "-"


class ConversionUploader:
    def __init__(
        self,
        db: "Database",
        client: DataManagerConversionClient | None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = _utcnow,
        max_attempts: int = 3,
        backoff: Sequence[float] = (1.0, 4.0),
        attempt_timeout: float = 15.0,
        drain_timeout: float = 20.0,
        click_window_days: int = CLICK_MAX_AGE.days,
        status_first_check: float = STATUS_FIRST_CHECK,
        status_recheck: float = STATUS_RECHECK,
        status_deadline: float = STATUS_DEADLINE,
    ) -> None:
        self._db = db
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._max_attempts = max(1, max_attempts)
        self._backoff = tuple(backoff) or (1.0,)
        self._attempt_timeout = attempt_timeout
        self._drain_timeout = drain_timeout
        # Never beyond the retention the policy promises.
        self._click_window = timedelta(
            days=max(1, min(CLICK_MAX_AGE.days, click_window_days))
        )
        self._status_first_check = status_first_check
        self._status_recheck = status_recheck
        self._status_deadline = status_deadline
        self._tasks: set[asyncio.Task] = set()
        self._followups: set[asyncio.Task] = set()
        # Sends are serialised (the transport holds one blocking session), so
        # the pre-send re-read below runs under the same lock: an upload that
        # queued behind another must not send what the row no longer holds.
        self._send_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @classmethod
    def from_config(
        cls, db: "Database", config: "CentralServerConfig"
    ) -> "ConversionUploader":
        """Build the uploader from GOOGLE_ADS_* config; disabled (no errors)
        when nothing is set, disabled with a warning when only some is."""
        for name in _OBSOLETE_ENV:
            if os.environ.get(name):
                logger.warning("ads.obsolete_setting name=%s reason=data_manager_api", name)
        missing = config.google_ads_missing
        if missing:
            if any(getattr(config, name) for name in _GOOGLE_ADS_FIELDS):
                logger.warning("ads.misconfigured missing=%s", ",".join(missing))
            else:
                logger.info("ads.disabled reason=not_configured")
            return cls(db, None)

        from .transport import AuthorizedSessionTransport

        transport = AuthorizedSessionTransport(
            client_id=config.google_ads_oauth_client_id or "",
            client_secret=config.google_ads_oauth_client_secret or "",
            refresh_token=config.google_ads_oauth_refresh_token or "",
        )
        client = DataManagerConversionClient(
            transport=transport,
            customer_id=config.google_ads_customer_id or "",
            conversion_action_id=config.google_ads_conversion_action_id or "",
            login_customer_id=config.google_ads_login_customer_id,
        )
        logger.info(
            "ads.enabled api=data_manager customer_id=%s action_id=%s "
            "login_customer_id_set=%s click_window_days=%d",
            config.google_ads_customer_id,
            config.google_ads_conversion_action_id,
            bool(config.google_ads_login_customer_id),
            config.google_ads_click_window_days,
        )
        return cls(db, client, click_window_days=config.google_ads_click_window_days)

    def schedule_first_pairing(self, user_id: str) -> None:
        """Fire-and-forget the account's one conversion upload."""
        if self._client is None:
            return
        task = asyncio.create_task(
            self.upload_first_pairing(user_id), name=f"ads-conversion-{user_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume_pending(self) -> None:
        """Re-schedule uploads a previous process never finished."""
        if self._client is None:
            return
        pending = await self._db.list_users_with_pending_ad_conversion()
        if not pending:
            return
        logger.info("ads.resume_pending count=%d", len(pending))
        for user_id in pending:
            self.schedule_first_pairing(user_id)

    async def upload_first_pairing(self, user_id: str) -> str:
        """The task body; returns the terminal reason (``"ok"`` on success)."""
        row = await self._db.get_user_by_id(user_id)
        if row is None:
            logger.info(
                "pairing.conversion_skipped user_id=%s reason=user_missing", user_id
            )
            return "user_missing"
        try:
            reason = await self._upload(user_id, row)
        except asyncio.CancelledError:
            # Shutdown mid-flight: keep the attribution so resume_pending()
            # retries the upload in the next process.
            raise
        except Exception:
            logger.exception("pairing.conversion_upload_crashed user_id=%s", user_id)
            reason = "exception"
        try:
            await self._db.clear_user_ad_attribution(user_id)
        except Exception:
            logger.exception("pairing.conversion_clear_failed user_id=%s", user_id)
        return reason

    def _identifier_for(self, row: Mapping[str, Any]) -> ConversionIdentifier | None:
        # The hashed-email fallback needs the account's network-bound
        # consent; a click id holds no personal data and needs none.
        return select_identifier(
            row,
            self._clock(),
            email_allowed=bool(row.get("ads_consent")),
            click_window=self._click_window,
        )

    async def _upload(self, user_id: str, row: Mapping[str, Any]) -> str:
        assert self._client is not None
        identifier = self._identifier_for(row)
        if identifier is None:
            reason = (
                "no_identifier"
                if row.get("ads_consent") or row.get("ad_click_id")
                else "no_consent"
            )
            logger.info(
                "pairing.conversion_skipped user_id=%s reason=%s", user_id, reason
            )
            return reason
        # Identical across retries, so a re-upload after a lost response is
        # a duplicate Google dedupes rather than a second conversion.
        conversion_time = parse_iso_utc(row.get("first_paired_at")) or self._clock()

        outcome: UploadOutcome | None = None
        for attempt in range(1, self._max_attempts + 1):
            async with self._send_lock:
                # Re-read right before sending: the queue wait and the
                # backoff are both long enough for the account to be
                # deleted or its attribution withdrawn.
                fresh = await self._db.get_user_by_id(user_id)
                if fresh is None:
                    logger.info(
                        "pairing.conversion_skipped user_id=%s reason=user_deleted",
                        user_id,
                    )
                    return "user_deleted"
                if self._identifier_for(fresh) != identifier:
                    logger.info(
                        "pairing.conversion_skipped user_id=%s "
                        "reason=attribution_withdrawn",
                        user_id,
                    )
                    return "attribution_withdrawn"
                outcome = await self._client.upload(
                    identifier, conversion_time, timeout=self._attempt_timeout
                )
            if outcome.ok or outcome.permanent:
                break
            if attempt < self._max_attempts:
                logger.warning(
                    "pairing.conversion_upload_retry user_id=%s identifier=%s "
                    "attempt=%d reason=%s status=%s",
                    user_id,
                    identifier.kind,
                    attempt,
                    outcome.reason,
                    outcome.status,
                )
                await self._sleep(
                    self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                )
        assert outcome is not None

        if outcome.ok:
            logger.info(
                "pairing.conversion_uploaded user_id=%s identifier=%s request_id=%s",
                user_id,
                identifier.kind,
                outcome.request_id,
            )
            if outcome.message:
                logger.info(
                    "pairing.conversion_upload_warnings user_id=%s request_id=%s %s",
                    user_id,
                    outcome.request_id,
                    outcome.message,
                )
            if outcome.request_id:
                self._schedule_status_check(user_id, outcome.request_id)
            return "ok"
        reason = outcome.reason if outcome.permanent else "retries_exhausted"
        logger.warning(
            "pairing.conversion_upload_failed user_id=%s identifier=%s reason=%s "
            "status=%s request_id=%s message=%s",
            user_id,
            identifier.kind,
            reason,
            outcome.status,
            outcome.request_id,
            outcome.message,
        )
        return reason

    def _schedule_status_check(self, user_id: str, request_id: str) -> None:
        task = asyncio.create_task(
            self.check_status(user_id, request_id), name=f"ads-status-{user_id}"
        )
        self._followups.add(task)
        task.add_done_callback(self._followups.discard)

    async def check_status(self, user_id: str, request_id: str) -> str:
        """Fetch Google's verdict for an accepted upload and log it.

        Best effort: the verdict is diagnostic (the action's Diagnostics tab
        in Google Ads is the source of truth), so a restart simply loses the
        follow-up. Polls while Google still reports ``PROCESSING``, up to
        the deadline.
        """
        assert self._client is not None
        outcome: StatusOutcome | None = None
        try:
            waited = self._status_first_check
            await self._sleep(self._status_first_check)
            while True:
                outcome = await self._client.request_status(
                    request_id, timeout=self._attempt_timeout
                )
                if outcome.terminal or waited >= self._status_deadline:
                    break
                await self._sleep(self._status_recheck)
                waited += self._status_recheck
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "pairing.conversion_status_crashed user_id=%s request_id=%s",
                user_id,
                request_id,
            )
            return "exception"
        assert outcome is not None
        level = logging.INFO if outcome.healthy else logging.WARNING
        logger.log(
            level,
            "pairing.conversion_status user_id=%s request_id=%s status=%s "
            "records=%s errors=%s warnings=%s message=%s",
            user_id,
            request_id,
            outcome.status,
            outcome.records,
            _format_counts(outcome.errors),
            _format_counts(outcome.warnings),
            outcome.message,
        )
        return outcome.status

    async def drain(self) -> None:
        """Await every scheduled upload and follow-up (tests and shutdown
        helpers)."""
        while self._tasks or self._followups:
            await asyncio.gather(
                *list(self._tasks), *list(self._followups), return_exceptions=True
            )

    async def _cancel_followups(self) -> None:
        pending = list(self._followups)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._followups.clear()

    async def aclose(self) -> None:
        """Drain rather than cancel: the CAS already fired, so a cancelled
        upload is a conversion lost for good. Bounded so shutdown cannot hang.
        Status follow-ups are only diagnostics and are cancelled outright."""
        await self._cancel_followups()
        pending = list(self._tasks)
        if pending:
            _done, still_running = await asyncio.wait(
                pending, timeout=self._drain_timeout
            )
            for task in still_running:
                task.cancel()
            if still_running:
                await asyncio.gather(*still_running, return_exceptions=True)
        self._tasks.clear()
        # An upload that finished during the drain scheduled a new follow-up.
        await self._cancel_followups()
        if self._client is not None:
            self._client.close()

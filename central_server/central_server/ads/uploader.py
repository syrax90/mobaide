"""Once-per-account "Agent paired" conversion upload.

``_complete_pairing`` calls ``schedule_first_pairing`` after the account's
``first_paired_at`` compare-and-set proved this is its first pairing ever.
The upload runs in a fire-and-forget task so a slow Google round-trip never
stalls the pairing response. Whatever the outcome, the account's attribution
columns are cleared afterwards; a stored click id or consent is what marks
an upload as pending, which is what lets ``resume_pending`` pick up uploads
a previous process never finished.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .google_ads import (
    ConversionIdentifier,
    GoogleAdsConversionClient,
    UploadOutcome,
    parse_iso_utc,
    select_identifier,
)

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database

logger = logging.getLogger(__name__)

_GOOGLE_ADS_FIELDS = (
    "google_ads_developer_token",
    "google_ads_customer_id",
    "google_ads_login_customer_id",
    "google_ads_oauth_client_id",
    "google_ads_oauth_client_secret",
    "google_ads_oauth_refresh_token",
    "google_ads_conversion_action_id",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversionUploader:
    def __init__(
        self,
        db: "Database",
        client: GoogleAdsConversionClient | None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = _utcnow,
        max_attempts: int = 3,
        backoff: Sequence[float] = (1.0, 4.0),
        attempt_timeout: float = 15.0,
        drain_timeout: float = 20.0,
    ) -> None:
        self._db = db
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._max_attempts = max(1, max_attempts)
        self._backoff = tuple(backoff) or (1.0,)
        self._attempt_timeout = attempt_timeout
        self._drain_timeout = drain_timeout
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @classmethod
    def from_config(
        cls, db: "Database", config: "CentralServerConfig"
    ) -> "ConversionUploader":
        """Build the uploader from GOOGLE_ADS_* config; disabled (no errors)
        when nothing is set, disabled with a warning when only some is."""
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
        client = GoogleAdsConversionClient(
            transport=transport,
            developer_token=config.google_ads_developer_token or "",
            customer_id=config.google_ads_customer_id or "",
            conversion_action_id=config.google_ads_conversion_action_id or "",
            login_customer_id=config.google_ads_login_customer_id,
            api_version=config.google_ads_api_version,
        )
        logger.info(
            "ads.enabled customer_id=%s action_id=%s api_version=%s "
            "login_customer_id_set=%s",
            config.google_ads_customer_id,
            config.google_ads_conversion_action_id,
            config.google_ads_api_version,
            bool(config.google_ads_login_customer_id),
        )
        return cls(db, client)

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
            row, self._clock(), email_allowed=bool(row.get("ads_consent"))
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
            if attempt > 1:
                # The backoff is long enough for the account to be deleted
                # or its attribution withdrawn; never send what the row no
                # longer holds.
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

    async def drain(self) -> None:
        """Await every scheduled upload (tests and shutdown helpers)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        """Drain rather than cancel: the CAS already fired, so a cancelled
        upload is a conversion lost for good. Bounded so shutdown cannot hang."""
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
        if self._client is not None:
            self._client.close()

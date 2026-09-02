"""``ConversionUploader``: decision rules, retries, logging hygiene, task
lifecycle and config gating — against a fake DB and a scripted transport."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from central_server.ads.google_ads import (
    AuthRefreshFailed,
    GoogleAdsConversionClient,
    TransientTransportError,
    TransportResponse,
    hash_email,
)
from central_server.ads.uploader import ConversionUploader

NOW = datetime(2026, 9, 2, 12, 18, 0, tzinfo=timezone.utc)
GCLID = "Cj0KCQjw-secret-click"
EMAIL = "Jane.Doe@gmail.com"


class FakeDb:
    def __init__(self, rows):
        self.rows = dict(rows)
        self.cleared: list[str] = []
        self.pending: list[str] = []
        self.pending_reads = 0
        self.fail_clear = False

    async def get_user_by_id(self, user_id):
        return self.rows.get(user_id)

    async def clear_user_ad_attribution(self, user_id):
        if self.fail_clear:
            raise RuntimeError("db gone")
        self.cleared.append(user_id)
        return True

    async def list_users_with_pending_ad_conversion(self):
        self.pending_reads += 1
        return list(self.pending)


class ScriptedTransport:
    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.closed = False
        self.gate: asyncio.Event | None = None

    async def post(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.gate is not None:
            await self.gate.wait()
        item = self.script.pop(0) if self.script else _ok()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def _ok():
    return TransportResponse(200, {"request-id": "req-1"}, {"results": [{}]})


def _status(code, body=None):
    if body is None:
        body = {"error": {"message": f"boom {code}"}}
    return TransportResponse(code, {"request-id": f"req-{code}"}, body)


def _row(**overrides):
    row = {
        "id": "u1",
        "email": EMAIL,
        "created_at": (NOW - timedelta(days=3)).isoformat(),
        "ad_click_id": GCLID,
        "ad_click_kind": "gclid",
        "ad_click_at": (NOW - timedelta(days=3)).isoformat(),
        "ads_consent": 1,
        "first_paired_at": NOW.isoformat(),
    }
    row.update(overrides)
    return row


def _uploader(db, transport, *, login_customer_id=None, on_sleep=None, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if on_sleep is not None:
            on_sleep()

    client = GoogleAdsConversionClient(
        transport=transport,
        developer_token="dev",
        customer_id="1234567890",
        conversion_action_id="987654321",
        login_customer_id=login_customer_id,
    )
    uploader = ConversionUploader(
        db, client, sleep=fake_sleep, clock=lambda: NOW, **kwargs
    )
    return uploader, sleeps


def _messages(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


@pytest.fixture(autouse=True)
def _capture(caplog):
    caplog.set_level(logging.INFO, logger="central_server.ads.uploader")


class TestDecisionRules:
    async def test_no_consent_and_no_click_skips_and_posts_nothing(self, caplog):
        db = FakeDb({"u1": _row(ads_consent=0, ad_click_id=None, ad_click_kind=None)})
        transport = ScriptedTransport()
        uploader, _ = _uploader(db, transport)

        assert await uploader.upload_first_pairing("u1") == "no_consent"

        assert transport.calls == []
        assert _messages(caplog, "pairing.conversion_skipped") == [
            "pairing.conversion_skipped user_id=u1 reason=no_consent"
        ]
        assert db.cleared == ["u1"]

    async def test_click_without_consent_still_uploads_the_click(self):
        """A click id holds no personal data, so the network-bound consent
        (which only licenses the hashed-email fallback) is not needed."""
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row(ads_consent=0)}), transport)
        assert await uploader.upload_first_pairing("u1") == "ok"
        assert transport.calls[0]["json"]["conversions"][0]["gclid"] == GCLID

    async def test_stale_click_without_consent_never_falls_back_to_email(self, caplog):
        row = _row(ads_consent=0, ad_click_at=(NOW - timedelta(days=91)).isoformat())
        transport = ScriptedTransport()
        db = FakeDb({"u1": row})
        uploader, _ = _uploader(db, transport)
        assert await uploader.upload_first_pairing("u1") == "no_identifier"
        assert transport.calls == []
        assert db.cleared == ["u1"]

    async def test_fresh_gclid_uploads_with_gclid_key(self, caplog):
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport()
        uploader, sleeps = _uploader(db, transport)

        assert await uploader.upload_first_pairing("u1") == "ok"

        conversion = transport.calls[0]["json"]["conversions"][0]
        assert conversion["gclid"] == GCLID
        assert "userIdentifiers" not in conversion
        assert _messages(caplog, "pairing.conversion_uploaded") == [
            "pairing.conversion_uploaded user_id=u1 identifier=gclid request_id=req-1"
        ]
        assert db.cleared == ["u1"]
        assert sleeps == []

    @pytest.mark.parametrize("kind", ["gbraid", "wbraid"])
    async def test_other_click_kinds_use_their_own_key(self, kind):
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row(ad_click_kind=kind)}), transport)
        await uploader.upload_first_pairing("u1")
        assert transport.calls[0]["json"]["conversions"][0][kind] == GCLID

    async def test_stale_click_uses_hashed_email(self, caplog):
        row = _row(ad_click_at=(NOW - timedelta(days=91)).isoformat())
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": row}), transport)

        await uploader.upload_first_pairing("u1")

        conversion = transport.calls[0]["json"]["conversions"][0]
        assert "gclid" not in conversion
        assert conversion["userIdentifiers"] == [
            {"userIdentifierSource": "FIRST_PARTY", "hashedEmail": hash_email(EMAIL)}
        ]
        assert "identifier=hashed_email" in _messages(caplog, "pairing.conversion_uploaded")[0]

    async def test_consent_without_click_uses_hashed_email(self):
        row = _row(ad_click_id=None, ad_click_kind=None, ad_click_at=None)
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": row}), transport)
        await uploader.upload_first_pairing("u1")
        assert "userIdentifiers" in transport.calls[0]["json"]["conversions"][0]

    async def test_consent_without_click_or_email_is_skipped(self, caplog):
        row = _row(ad_click_id=None, ad_click_kind=None, ad_click_at=None, email=None)
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": row}), transport)
        assert await uploader.upload_first_pairing("u1") == "no_identifier"
        assert transport.calls == []

    async def test_conversion_time_is_first_paired_at(self):
        earlier = (NOW - timedelta(days=1)).isoformat()
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row(first_paired_at=earlier)}), transport)
        await uploader.upload_first_pairing("u1")
        assert (
            transport.calls[0]["json"]["conversions"][0]["conversionDateTime"]
            == "2026-09-01 12:18:00+00:00"
        )

    async def test_conversion_time_falls_back_to_clock(self):
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row(first_paired_at=None)}), transport)
        await uploader.upload_first_pairing("u1")
        assert (
            transport.calls[0]["json"]["conversions"][0]["conversionDateTime"]
            == "2026-09-02 12:18:00+00:00"
        )

    async def test_identifiers_never_reach_the_logs(self, caplog):
        transport = ScriptedTransport(_status(400), )
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport)
        await uploader.upload_first_pairing("u1")
        stale = _row(ad_click_at=(NOW - timedelta(days=91)).isoformat(), id="u2")
        uploader2, _ = _uploader(FakeDb({"u2": stale}), ScriptedTransport())
        await uploader2.upload_first_pairing("u2")

        for secret in (GCLID, EMAIL, EMAIL.lower(), hash_email(EMAIL)):
            assert secret not in caplog.text

    async def test_login_customer_id_header_present_when_configured(self):
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport, login_customer_id="5555")
        await uploader.upload_first_pairing("u1")
        assert transport.calls[0]["headers"]["login-customer-id"] == "5555"

    async def test_login_customer_id_header_absent_when_unset(self):
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport)
        await uploader.upload_first_pairing("u1")
        assert "login-customer-id" not in transport.calls[0]["headers"]

    async def test_attempt_timeout_is_forwarded(self):
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport)
        await uploader.upload_first_pairing("u1")
        assert transport.calls[0]["timeout"] == 15.0


class TestRetries:
    async def test_500_then_200_retries_once_and_logs_one_success(self, caplog):
        transport = ScriptedTransport(_status(500), _ok())
        uploader, sleeps = _uploader(FakeDb({"u1": _row()}), transport)

        assert await uploader.upload_first_pairing("u1") == "ok"

        assert len(transport.calls) == 2
        assert sleeps == [1.0]
        assert len(_messages(caplog, "pairing.conversion_uploaded")) == 1
        assert _messages(caplog, "pairing.conversion_upload_retry") == [
            "pairing.conversion_upload_retry user_id=u1 identifier=gclid "
            "attempt=1 reason=http_5xx status=500"
        ]

    async def test_429_retries_with_backoff(self):
        transport = ScriptedTransport(_status(429, "slow"), _status(429, "slow"), _ok())
        uploader, sleeps = _uploader(FakeDb({"u1": _row()}), transport)
        assert await uploader.upload_first_pairing("u1") == "ok"
        assert sleeps == [1.0, 4.0]

    async def test_400_is_permanent_and_not_retried(self, caplog):
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport(_status(400))
        uploader, sleeps = _uploader(db, transport)

        assert await uploader.upload_first_pairing("u1") == "http_4xx"

        assert len(transport.calls) == 1 and sleeps == []
        assert _messages(caplog, "pairing.conversion_upload_failed") == [
            "pairing.conversion_upload_failed user_id=u1 identifier=gclid "
            "reason=http_4xx status=400 request_id=req-400 message=boom 400"
        ]
        assert db.cleared == ["u1"]

    async def test_partial_failure_is_permanent(self, caplog):
        body = {"results": [{}], "partialFailureError": {"message": "click expired"}}
        transport = ScriptedTransport(TransportResponse(200, {}, body))
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport)
        assert await uploader.upload_first_pairing("u1") == "partial_failure"
        assert len(transport.calls) == 1
        assert "message=click expired" in _messages(caplog, "pairing.conversion_upload_failed")[0]

    async def test_refresh_error_is_permanent(self, caplog):
        transport = ScriptedTransport(AuthRefreshFailed("RefreshError"))
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport)
        assert await uploader.upload_first_pairing("u1") == "oauth_refresh_failed"
        assert len(transport.calls) == 1
        assert "reason=oauth_refresh_failed" in _messages(caplog, "pairing.conversion_upload_failed")[0]

    async def test_retry_aborts_when_the_account_was_deleted_meanwhile(self, caplog):
        """The backoff is long enough for an immediate account deletion to
        land; the retry must not send what the row no longer holds."""
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport(_status(500), _ok())
        uploader, sleeps = _uploader(db, transport, on_sleep=lambda: db.rows.pop("u1"))

        assert await uploader.upload_first_pairing("u1") == "user_deleted"

        assert len(transport.calls) == 1 and sleeps == [1.0]
        assert "reason=user_deleted" in caplog.text
        assert _messages(caplog, "pairing.conversion_uploaded") == []

    async def test_retry_aborts_when_attribution_was_withdrawn_meanwhile(self, caplog):
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport(_status(500), _ok())

        def withdraw():
            db.rows["u1"] = _row(ad_click_id=None, ad_click_kind=None, ad_click_at=None, ads_consent=0)

        uploader, _ = _uploader(db, transport, on_sleep=withdraw)

        assert await uploader.upload_first_pairing("u1") == "attribution_withdrawn"

        assert len(transport.calls) == 1
        assert "reason=attribution_withdrawn" in caplog.text
        assert db.cleared == ["u1"]

    async def test_first_send_revalidates_under_the_send_lock(self, caplog):
        """Sends queue behind one lock; an upload that waited must not send a
        row deleted in the meantime — even on its first attempt."""
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport()
        uploader, _ = _uploader(db, transport)
        await uploader._send_lock.acquire()
        try:
            task = asyncio.create_task(uploader.upload_first_pairing("u1"))
            await asyncio.sleep(0)
            db.rows.pop("u1")
        finally:
            uploader._send_lock.release()
        assert await task == "user_deleted"
        assert transport.calls == []

    async def test_click_window_follows_the_conversion_action(self):
        """A click outside the action's click-through window is a guaranteed
        no-match, so the fallback is tried instead."""
        row = _row(ad_click_at=(NOW - timedelta(days=31)).isoformat())
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": row}), transport, click_window_days=30)
        assert await uploader.upload_first_pairing("u1") == "ok"
        conversion = transport.calls[0]["json"]["conversions"][0]
        assert "gclid" not in conversion and "userIdentifiers" in conversion

    async def test_click_window_never_exceeds_retention(self):
        transport = ScriptedTransport()
        uploader, _ = _uploader(FakeDb({"u1": _row()}), transport, click_window_days=400)
        assert uploader._click_window == timedelta(days=90)

    async def test_transient_exhaustion_clears_and_logs_retries_exhausted(self, caplog):
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport(*(TransientTransportError("ConnectionError") for _ in range(3)))
        uploader, sleeps = _uploader(db, transport)

        assert await uploader.upload_first_pairing("u1") == "retries_exhausted"

        assert len(transport.calls) == 3 and sleeps == [1.0, 4.0]
        assert "reason=retries_exhausted" in _messages(caplog, "pairing.conversion_upload_failed")[0]
        assert db.cleared == ["u1"]


class TestHousekeeping:
    async def test_clear_failure_is_logged_not_raised(self, caplog):
        db = FakeDb({"u1": _row()})
        db.fail_clear = True
        uploader, _ = _uploader(db, ScriptedTransport())
        assert await uploader.upload_first_pairing("u1") == "ok"
        assert _messages(caplog, "pairing.conversion_clear_failed") == [
            "pairing.conversion_clear_failed user_id=u1"
        ]

    async def test_user_missing_skips_without_clear(self, caplog):
        db = FakeDb({})
        uploader, _ = _uploader(db, ScriptedTransport())
        assert await uploader.upload_first_pairing("ghost") == "user_missing"
        assert db.cleared == []
        assert "reason=user_missing" in caplog.text

    async def test_disabled_uploader_never_touches_the_db(self):
        db = FakeDb({"u1": _row()})
        db.pending = ["u1"]
        uploader = ConversionUploader(db, None)
        assert uploader.enabled is False
        uploader.schedule_first_pairing("u1")
        await uploader.resume_pending()
        assert uploader._tasks == set()
        assert db.pending_reads == 0 and db.cleared == []
        await uploader.aclose()

    async def test_schedule_tracks_task_and_drain_completes(self, caplog):
        db = FakeDb({"u1": _row()})
        uploader, _ = _uploader(db, ScriptedTransport())
        uploader.schedule_first_pairing("u1")
        assert len(uploader._tasks) == 1
        await uploader.drain()
        assert uploader._tasks == set()
        assert db.cleared == ["u1"]
        assert len(_messages(caplog, "pairing.conversion_uploaded")) == 1

    async def test_resume_pending_schedules_each_pending_user_once(self, caplog):
        db = FakeDb({"u1": _row(), "u2": _row(id="u2")})
        db.pending = ["u1", "u2"]
        transport = ScriptedTransport()
        uploader, _ = _uploader(db, transport)
        await uploader.resume_pending()
        assert len(uploader._tasks) == 2
        await uploader.drain()
        assert sorted(db.cleared) == ["u1", "u2"]
        assert len(transport.calls) == 2
        assert "ads.resume_pending count=2" in caplog.text

    async def test_aclose_waits_for_an_inflight_upload(self, caplog):
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport()
        transport.gate = asyncio.Event()
        uploader, _ = _uploader(db, transport)
        uploader.schedule_first_pairing("u1")
        await asyncio.sleep(0)

        closing = asyncio.create_task(uploader.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        transport.gate.set()
        await closing

        assert len(transport.calls) == 1
        assert db.cleared == ["u1"]
        assert transport.closed
        assert len(_messages(caplog, "pairing.conversion_uploaded")) == 1

    async def test_aclose_cancels_a_stuck_upload_after_the_drain_timeout(self):
        db = FakeDb({"u1": _row()})
        transport = ScriptedTransport()
        transport.gate = asyncio.Event()  # never set
        uploader, _ = _uploader(db, transport, drain_timeout=0.01)
        uploader.schedule_first_pairing("u1")
        task = next(iter(uploader._tasks))
        await asyncio.sleep(0)

        await uploader.aclose()

        assert task.cancelled()
        assert uploader._tasks == set()
        # A cancelled upload keeps its attribution for resume_pending().
        assert db.cleared == []


class TestFromConfig:
    async def test_unconfigured_returns_disabled_uploader_without_warnings(
        self, db, config, caplog
    ):
        with caplog.at_level(logging.INFO):
            uploader = ConversionUploader.from_config(db, config)
        assert not uploader.enabled
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert "ads.disabled reason=not_configured" in caplog.text
        uploader.schedule_first_pairing("u1")
        assert uploader._tasks == set()

    async def test_partial_config_warns_and_stays_disabled(self, db, config, caplog):
        partial = replace(
            config, google_ads_developer_token="dev-token", google_ads_customer_id="1234567890"
        )
        with caplog.at_level(logging.WARNING):
            uploader = ConversionUploader.from_config(db, partial)
        assert not uploader.enabled
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [
            "ads.misconfigured missing=GOOGLE_ADS_OAUTH_CLIENT_ID,"
            "GOOGLE_ADS_OAUTH_CLIENT_SECRET,GOOGLE_ADS_OAUTH_REFRESH_TOKEN,"
            "GOOGLE_ADS_CONVERSION_ACTION_ID"
        ]
        assert "dev-token" not in caplog.text

    async def test_full_config_builds_enabled_uploader(self, db, config, caplog):
        full = replace(
            config,
            google_ads_developer_token="dev-token",
            google_ads_customer_id="1234567890",
            google_ads_login_customer_id="9876543210",
            google_ads_oauth_client_id="client-id.apps.googleusercontent.com",
            google_ads_oauth_client_secret="GOCSPX-secret",
            google_ads_oauth_refresh_token="1//refresh-secret",
            google_ads_conversion_action_id="987654321",
        )
        with caplog.at_level(logging.INFO):
            uploader = ConversionUploader.from_config(db, full)
        assert uploader.enabled
        assert (
            "ads.enabled customer_id=1234567890 action_id=987654321 "
            "api_version=v25 login_customer_id_set=True click_window_days=90"
        ) in caplog.text
        for secret in ("dev-token", "GOCSPX-secret", "refresh-secret"):
            assert secret not in caplog.text
        await uploader.aclose()

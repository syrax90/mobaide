"""Pure tests for ``ads/google_ads.py`` — no network, no credentials."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from central_server.ads.google_ads import (
    CONSENT,
    AuthRefreshFailed,
    ConversionIdentifier,
    GoogleAdsConversionClient,
    TransientTransportError,
    TransportResponse,
    build_conversion_payload,
    build_headers,
    classify_response,
    format_conversion_datetime,
    hash_email,
    normalize_email,
    parse_iso_utc,
    select_identifier,
    upload_url,
)
from central_server.db.database import _now_iso

NOW = datetime(2026, 9, 2, 12, 18, 0, tzinfo=timezone.utc)
ACTION = "customers/1234567890/conversionActions/987654321"
CONSENT_JSON = {"adUserData": "GRANTED", "adPersonalization": "DENIED"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (" Foo.Bar+x@Gmail.com ", "foobar@gmail.com"),
        ("a.b.c@googlemail.com", "abc@googlemail.com"),
        ("First.Last@Example.COM", "first.last@example.com"),
        ("x+tag@example.com", "x+tag@example.com"),
        ("plain@gmail.com", "plain@gmail.com"),
        ("no-at-sign", "no-at-sign"),
    ],
)
def test_normalize_email_vectors(raw, expected):
    assert normalize_email(raw) == expected


def test_hash_email_is_sha256_hex_of_normalized():
    digest = hash_email(" Foo.Bar+x@Gmail.com ")
    assert digest == hashlib.sha256(b"foobar@gmail.com").hexdigest()
    assert len(digest) == 64 and digest == digest.lower()


def test_format_conversion_datetime_shifts_to_utc_and_drops_microseconds():
    aware = datetime(2026, 9, 2, 14, 18, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    assert format_conversion_datetime(aware) == "2026-09-02 12:18:00+00:00"
    naive = datetime(2026, 9, 2, 12, 18, 0)
    assert format_conversion_datetime(naive) == "2026-09-02 12:18:00+00:00"


def test_parse_iso_utc_round_trips_now_iso_and_rejects_garbage():
    text = _now_iso()
    parsed = parse_iso_utc(text)
    assert parsed is not None and parsed.isoformat() == text
    assert parse_iso_utc("garbage") is None
    assert parse_iso_utc(None) is None
    assert parse_iso_utc(12) is None
    assert parse_iso_utc("2026-09-01T10:00:00") == datetime(
        2026, 9, 1, 10, tzinfo=timezone.utc
    )


@pytest.mark.parametrize("kind", ["gclid", "gbraid", "wbraid"])
def test_payload_click_kinds(kind):
    payload = build_conversion_payload(
        customer_id="1234567890",
        conversion_action_id="987654321",
        identifier=ConversionIdentifier(kind, "Cj0K-abc_123"),
        conversion_time=NOW,
    )
    assert payload == {
        "conversions": [
            {
                "conversionAction": ACTION,
                "conversionDateTime": "2026-09-02 12:18:00+00:00",
                kind: "Cj0K-abc_123",
                "consent": CONSENT_JSON,
            }
        ],
        "partialFailure": True,
    }


def test_payload_hashed_email():
    payload = build_conversion_payload(
        customer_id="1234567890",
        conversion_action_id="987654321",
        identifier=ConversionIdentifier("hashed_email", "ab" * 32),
        conversion_time=NOW,
    )
    conversion = payload["conversions"][0]
    assert conversion["userIdentifiers"] == [
        {"userIdentifierSource": "FIRST_PARTY", "hashedEmail": "ab" * 32}
    ]
    assert not {"gclid", "gbraid", "wbraid"} & conversion.keys()
    assert conversion["consent"] == CONSENT_JSON
    assert payload["partialFailure"] is True


def test_consent_matches_the_landing_banner():
    assert CONSENT == CONSENT_JSON


def test_upload_url_uses_version_and_customer():
    assert upload_url("v25", "1234567890") == (
        "https://googleads.googleapis.com/v25/customers/1234567890"
        ":uploadClickConversions"
    )


def test_headers_with_and_without_login_customer_id():
    with_login = build_headers(developer_token="dev", login_customer_id="5555")
    assert with_login == {
        "developer-token": "dev",
        "Content-Type": "application/json",
        "login-customer-id": "5555",
    }
    without = build_headers(developer_token="dev", login_customer_id=None)
    assert "login-customer-id" not in without
    assert "Authorization" not in without


def test_identifier_repr_hides_the_value():
    assert "secret-click" not in repr(ConversionIdentifier("gclid", "secret-click"))


def _row(**overrides):
    row = {
        "email": "Jane.Doe@gmail.com",
        "ad_click_id": "click-1",
        "ad_click_kind": "gclid",
        "ad_click_at": (NOW - timedelta(days=10)).isoformat(),
        "created_at": (NOW - timedelta(days=10)).isoformat(),
    }
    row.update(overrides)
    return row


class TestSelectIdentifier:
    def test_prefers_fresh_click(self):
        assert select_identifier(_row(), NOW) == ConversionIdentifier("gclid", "click-1")

    def test_click_exactly_90_days_is_still_used(self):
        row = _row(ad_click_at=(NOW - timedelta(days=90)).isoformat())
        assert select_identifier(row, NOW).kind == "gclid"

    def test_stale_click_falls_back_to_hashed_email(self):
        row = _row(ad_click_at=(NOW - timedelta(days=91)).isoformat())
        assert select_identifier(row, NOW) == ConversionIdentifier(
            "hashed_email", hash_email("Jane.Doe@gmail.com")
        )

    def test_unknown_kind_falls_back(self):
        assert select_identifier(_row(ad_click_kind="fbclid"), NOW).kind == "hashed_email"

    def test_missing_click_time_ages_from_sign_up(self):
        assert select_identifier(_row(ad_click_at=None), NOW).kind == "gclid"
        stale = _row(ad_click_at=None, created_at=(NOW - timedelta(days=91)).isoformat())
        assert select_identifier(stale, NOW) is None

    def test_future_click_time_is_capped_by_sign_up(self):
        """The click stamp is the visitor's clock; sign-up is the server's."""
        ahead = (NOW + timedelta(days=3650)).isoformat()
        assert select_identifier(_row(ad_click_at=ahead), NOW).kind == "gclid"
        old_account = _row(
            ad_click_at=ahead, created_at=(NOW - timedelta(days=91)).isoformat()
        )
        assert select_identifier(old_account, NOW) is None

    def test_consent_without_click_uses_hashed_email(self):
        row = _row(ad_click_id=None, ad_click_kind=None, ad_click_at=None)
        assert select_identifier(row, NOW).kind == "hashed_email"

    def test_email_fallback_expires_with_the_sign_up_window(self):
        row = _row(
            ad_click_id=None,
            ad_click_kind=None,
            ad_click_at=None,
            created_at=(NOW - timedelta(days=91)).isoformat(),
        )
        assert select_identifier(row, NOW) is None

    def test_nothing_usable_returns_none(self):
        row = _row(ad_click_id=None, ad_click_kind=None, ad_click_at=None, email=None)
        assert select_identifier(row, NOW) is None


def _resp(status, body, headers=None):
    return TransportResponse(status, headers if headers is not None else {"Request-Id": "req-1"}, body)


class TestClassifyResponse:
    def test_200_ok_reads_request_id_case_insensitively(self):
        outcome = classify_response(_resp(200, {"results": [{}]}))
        assert outcome.ok and outcome.reason == "ok" and outcome.request_id == "req-1"

    def test_partial_failure_is_permanent_and_keeps_only_the_top_level_message(self):
        body = {
            "results": [{}],
            "partialFailureError": {
                "code": 3,
                "message": "The click has expired",
                "details": [{"errors": [{"trigger": {"stringValue": "SECRET-GCLID"}}]}],
            },
        }
        outcome = classify_response(_resp(200, body))
        assert (outcome.ok, outcome.permanent, outcome.reason) == (False, True, "partial_failure")
        assert outcome.message == "The click has expired"
        assert "SECRET-GCLID" not in repr(outcome)

    def test_400_is_permanent_with_error_message(self):
        outcome = classify_response(
            _resp(400, {"error": {"message": "Request contains an invalid argument."}})
        )
        assert (outcome.permanent, outcome.reason, outcome.status) == (True, "http_4xx", 400)
        assert outcome.message == "Request contains an invalid argument."

    def test_401_is_permanent(self):
        assert classify_response(_resp(401, {"error": {"message": "x"}})).permanent

    def test_429_is_transient(self):
        outcome = classify_response(_resp(429, "slow down"))
        assert (outcome.permanent, outcome.reason) == (False, "http_429")

    @pytest.mark.parametrize("status", [500, 503])
    def test_5xx_is_transient(self, status):
        outcome = classify_response(_resp(status, None))
        assert (outcome.permanent, outcome.reason, outcome.status) == (False, "http_5xx", status)

    def test_non_json_200_is_permanent_bad_response(self):
        assert classify_response(_resp(200, "<html>")).reason == "bad_response"

    def test_unexpected_status_is_permanent_bad_response(self):
        outcome = classify_response(_resp(302, None, headers={}))
        assert outcome.reason == "bad_response" and outcome.request_id is None

    def test_messages_are_truncated(self):
        outcome = classify_response(_resp(400, {"error": {"message": "x" * 500}}))
        assert len(outcome.message) == 200


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response
        self.error = error
        self.closed = False

    async def post(self, url, *, json, headers, timeout):
        self.calls.append((url, json, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


def _client(transport, **kwargs):
    return GoogleAdsConversionClient(
        transport=transport,
        developer_token="dev",
        customer_id="1234567890",
        conversion_action_id="987654321",
        **kwargs,
    )


class TestClient:
    async def test_upload_posts_payload_with_headers_and_classifies(self):
        transport = FakeTransport(TransportResponse(200, {"request-id": "r"}, {"results": [{}]}))
        outcome = await _client(transport, login_customer_id="5555").upload(
            ConversionIdentifier("gclid", "c"), NOW, timeout=15.0
        )
        assert outcome.ok and outcome.request_id == "r"
        url, body, headers, timeout = transport.calls[0]
        assert url == upload_url("v25", "1234567890")
        assert body["conversions"][0]["gclid"] == "c"
        assert headers["login-customer-id"] == "5555"
        assert headers["developer-token"] == "dev"
        assert "Authorization" not in headers
        assert timeout == 15.0

    @pytest.mark.parametrize(
        "error, permanent, reason",
        [
            (TransientTransportError("ConnectionError"), False, "transport"),
            (AuthRefreshFailed("RefreshError"), True, "oauth_refresh_failed"),
            (TimeoutError(), False, "transport"),
            (RuntimeError("bug"), True, "exception"),
        ],
    )
    async def test_upload_never_raises(self, error, permanent, reason):
        outcome = await _client(FakeTransport(error=error)).upload(
            ConversionIdentifier("gclid", "c"), NOW, timeout=1.0
        )
        assert (outcome.ok, outcome.permanent, outcome.reason) == (False, permanent, reason)

    def test_close_closes_transport(self):
        transport = FakeTransport()
        _client(transport).close()
        assert transport.closed

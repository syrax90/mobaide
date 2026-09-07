"""Pure tests for ``ads/data_manager.py`` — no network, no credentials."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from central_server.ads.data_manager import (
    CONSENT,
    INGEST_URL,
    REQUEST_STATUS_URL,
    AuthRefreshFailed,
    ConversionIdentifier,
    DataManagerConversionClient,
    TransientTransportError,
    TransportResponse,
    build_destination,
    build_ingest_payload,
    classify_response,
    classify_status,
    format_event_timestamp,
    hash_email,
    normalize_email,
    parse_iso_utc,
    select_identifier,
    transaction_id,
)
from central_server.db.database import _now_iso

NOW = datetime(2026, 9, 2, 12, 18, 0, tzinfo=timezone.utc)
CONSENT_JSON = {"adUserData": "CONSENT_GRANTED", "adPersonalization": "CONSENT_DENIED"}
DESTINATION = {
    "operatingAccount": {"accountType": "GOOGLE_ADS", "accountId": "1234567890"},
    "productDestinationId": "987654321",
}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (" Foo.Bar+x@Gmail.com ", "foobar@gmail.com"),
        ("a.b.c@googlemail.com", "abc@googlemail.com"),
        ("First.Last@Example.COM", "first.last@example.com"),
        ("x+tag@example.com", "x+tag@example.com"),
        ("plain@gmail.com", "plain@gmail.com"),
        ("Jane Doe @ example.com", "janedoe@example.com"),
        ("no-at-sign", "no-at-sign"),
    ],
)
def test_normalize_email_vectors(raw, expected):
    assert normalize_email(raw) == expected


def test_hash_email_is_sha256_hex_of_normalized():
    digest = hash_email(" Foo.Bar+x@Gmail.com ")
    assert digest == hashlib.sha256(b"foobar@gmail.com").hexdigest()
    assert len(digest) == 64 and digest == digest.lower()


def test_format_event_timestamp_is_rfc3339_utc_at_whole_seconds():
    aware = datetime(2026, 9, 2, 14, 18, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    assert format_event_timestamp(aware) == "2026-09-02T12:18:00Z"
    naive = datetime(2026, 9, 2, 12, 18, 0)
    assert format_event_timestamp(naive) == "2026-09-02T12:18:00Z"


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


def test_destination_with_and_without_login_account():
    assert build_destination(customer_id="1234567890", conversion_action_id="987654321") == DESTINATION
    via_manager = build_destination(
        customer_id="1234567890", conversion_action_id="987654321", login_customer_id="5555"
    )
    assert via_manager["loginAccount"] == {"accountType": "GOOGLE_ADS", "accountId": "5555"}
    assert via_manager["operatingAccount"] == DESTINATION["operatingAccount"]


@pytest.mark.parametrize("kind", ["gclid", "gbraid", "wbraid"])
def test_payload_click_kinds(kind):
    payload = build_ingest_payload(
        customer_id="1234567890",
        conversion_action_id="987654321",
        identifier=ConversionIdentifier(kind, "Cj0K-abc_123"),
        event_time=NOW,
    )
    assert payload == {
        "destinations": [DESTINATION],
        "events": [
            {
                "eventTimestamp": "2026-09-02T12:18:00Z",
                "eventSource": "WEB",
                "transactionId": transaction_id(ConversionIdentifier(kind, "Cj0K-abc_123"), NOW),
                "consent": CONSENT_JSON,
                "adIdentifiers": {kind: "Cj0K-abc_123"},
            }
        ],
    }


def test_payload_hashed_email_declares_hex_encoding():
    payload = build_ingest_payload(
        customer_id="1234567890",
        conversion_action_id="987654321",
        identifier=ConversionIdentifier("hashed_email", "ab" * 32),
        event_time=NOW,
        login_customer_id="5555",
    )
    event = payload["events"][0]
    assert event["userData"] == {"userIdentifiers": [{"emailAddress": "ab" * 32}]}
    assert "adIdentifiers" not in event
    assert event["consent"] == CONSENT_JSON
    assert payload["encoding"] == "HEX"
    assert payload["destinations"][0]["loginAccount"]["accountId"] == "5555"
    assert "validateOnly" not in payload


def test_payload_carries_nothing_else_about_the_account():
    """The privacy policy promises click id / email hash + consent only; the
    transaction id is a digest of exactly those fields."""
    payload = build_ingest_payload(
        customer_id="1234567890",
        conversion_action_id="987654321",
        identifier=ConversionIdentifier("gclid", "c"),
        event_time=NOW,
    )
    assert set(payload) == {"destinations", "events"}
    event = payload["events"][0]
    assert set(event) == {
        "eventTimestamp", "eventSource", "transactionId", "consent", "adIdentifiers"
    }
    assert event["transactionId"] == transaction_id(ConversionIdentifier("gclid", "c"), NOW)


class TestTransactionId:
    def test_is_a_digest_of_the_identifier_and_the_pairing_instant(self):
        """Google gets the identifier and the instant (to the second) anyway,
        so a one-way digest of the two discloses nothing more — and nothing
        else goes in."""
        material = "\n".join(("gclid", "c", "2026-09-02T12:18:00.000000+00:00")).encode("utf-8")
        expected = hashlib.sha256(material).hexdigest()[:32]
        assert transaction_id(ConversionIdentifier("gclid", "c"), NOW) == expected

    def test_two_accounts_from_one_click_in_the_same_second_get_distinct_keys(self):
        """The cookie is not consumed at sign-up, so two accounts can share a
        click id; their stored first_paired_at differ in the microseconds
        the report itself rounds away."""
        first = NOW.replace(microsecond=100)
        second = NOW.replace(microsecond=200)
        assert format_event_timestamp(first) == format_event_timestamp(second)
        assert transaction_id(ConversionIdentifier("gclid", "c"), first) != transaction_id(
            ConversionIdentifier("gclid", "c"), second
        )

    def test_naive_datetimes_count_as_utc(self):
        naive = NOW.replace(tzinfo=None)
        assert transaction_id(ConversionIdentifier("gclid", "c"), naive) == transaction_id(
            ConversionIdentifier("gclid", "c"), NOW
        )

    def test_is_stable_across_calls_and_timezones(self):
        """A retry and a resumed upload must produce the same key."""
        local = NOW.astimezone(timezone(timedelta(hours=3)))
        assert transaction_id(ConversionIdentifier("gclid", "c"), NOW) == transaction_id(
            ConversionIdentifier("gclid", "c"), local
        )

    @pytest.mark.parametrize(
        "identifier, event_time",
        [
            (ConversionIdentifier("gclid", "d"), NOW),
            (ConversionIdentifier("gbraid", "c"), NOW),
            (ConversionIdentifier("hashed_email", "c"), NOW),
            (ConversionIdentifier("gclid", "c"), NOW + timedelta(seconds=1)),
        ],
    )
    def test_changes_with_any_input(self, identifier, event_time):
        assert transaction_id(identifier, event_time) != transaction_id(
            ConversionIdentifier("gclid", "c"), NOW
        )

    def test_shape(self):
        value = transaction_id(ConversionIdentifier("gclid", "c"), NOW)
        assert len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


def test_payload_validate_only_flag():
    payload = build_ingest_payload(
        customer_id="1234567890",
        conversion_action_id="987654321",
        identifier=ConversionIdentifier("gclid", "c"),
        event_time=NOW,
        validate_only=True,
    )
    assert payload["validateOnly"] is True


def test_consent_matches_the_landing_banner():
    assert CONSENT == CONSENT_JSON


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

    def test_email_fallback_needs_permission_but_the_click_does_not(self):
        assert select_identifier(_row(), NOW, email_allowed=False).kind == "gclid"
        stale = _row(ad_click_at=(NOW - timedelta(days=91)).isoformat())
        assert select_identifier(stale, NOW, email_allowed=False) is None
        assert select_identifier(stale, NOW, email_allowed=True).kind == "hashed_email"


def _resp(status, body):
    return TransportResponse(status, {}, body)


class TestClassifyResponse:
    def test_200_ok_reads_the_request_id_from_the_body(self):
        outcome = classify_response(_resp(200, {"requestId": "req-1"}))
        assert outcome.ok and outcome.reason == "ok" and outcome.request_id == "req-1"
        assert outcome.message is None

    @pytest.mark.parametrize("body", [{}, {"requestId": ""}, {"requestId": None}, {"requestId": 7}])
    def test_200_without_a_request_id_is_a_permanent_bad_response(self, body):
        """The id is the only handle on an accepted request; a 200 without
        one is a broken response, not a success."""
        outcome = classify_response(_resp(200, body))
        assert (outcome.ok, outcome.permanent, outcome.reason, outcome.status) == (
            False, True, "bad_response", 200
        )
        assert outcome.request_id is None and outcome.message == "missing_request_id"
        assert not outcome.credential_failure

    def test_field_warnings_are_counted_not_echoed(self):
        body = {
            "requestId": "req-1",
            "fieldWarnings": [{"field": "events[0].adIdentifiers.gclid", "description": "SECRET-GCLID looks odd"}],
        }
        outcome = classify_response(_resp(200, body))
        assert outcome.ok and outcome.message == "field_warnings=1"
        assert "SECRET-GCLID" not in repr(outcome)

    def test_400_is_permanent_with_status_and_message(self):
        body = {
            "error": {
                "code": 400,
                "message": "Request contains an invalid argument.",
                "status": "INVALID_ARGUMENT",
                "details": [{"fieldViolations": [{"description": "SECRET-GCLID"}]}],
            }
        }
        outcome = classify_response(_resp(400, body))
        assert (outcome.permanent, outcome.reason, outcome.status) == (True, "http_4xx", 400)
        assert outcome.message == "INVALID_ARGUMENT: Request contains an invalid argument."
        assert "SECRET-GCLID" not in repr(outcome)

    @pytest.mark.parametrize("status", [401, 403])
    def test_401_and_403_are_credential_failures(self, status):
        outcome = classify_response(
            _resp(status, {"error": {"message": "no access", "status": "PERMISSION_DENIED"}})
        )
        assert (outcome.permanent, outcome.reason, outcome.status) == (True, f"http_{status}", status)
        assert outcome.credential_failure
        assert outcome.message == "PERMISSION_DENIED: no access"

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_other_4xx_are_permanent_event_failures(self, status):
        outcome = classify_response(_resp(status, {"error": {"message": "x"}}))
        assert outcome.permanent and outcome.reason == "http_4xx"
        assert not outcome.credential_failure

    def test_429_is_transient(self):
        outcome = classify_response(_resp(429, "slow down"))
        assert (outcome.permanent, outcome.reason) == (False, "http_429")

    @pytest.mark.parametrize("status", [500, 503])
    def test_5xx_is_transient(self, status):
        outcome = classify_response(_resp(status, None))
        assert (outcome.permanent, outcome.reason, outcome.status) == (False, "http_5xx", status)

    def test_non_json_200_is_permanent_bad_response(self):
        outcome = classify_response(_resp(200, "<html>"))
        assert (outcome.permanent, outcome.reason, outcome.message) == (True, "bad_response", "<html>")

    def test_unexpected_status_is_permanent_bad_response(self):
        outcome = classify_response(_resp(302, None))
        assert outcome.reason == "bad_response" and outcome.request_id is None

    def test_messages_are_truncated(self):
        outcome = classify_response(_resp(400, {"error": {"message": "x" * 500}}))
        assert len(outcome.message) == 200


def _status_body(status, **entry):
    entry = {"destination": DESTINATION, "requestStatus": status, **entry}
    return {"requestStatusPerDestination": [entry]}


class TestClassifyStatus:
    def test_success_reads_the_record_count(self):
        outcome = classify_status(
            _resp(200, _status_body("SUCCESS", eventsIngestionStatus={"recordCount": "1"}))
        )
        assert outcome.terminal and outcome.healthy
        assert (outcome.status, outcome.records, outcome.errors) == ("SUCCESS", 1, ())

    def test_processing_is_not_terminal(self):
        outcome = classify_status(_resp(200, _status_body("PROCESSING")))
        assert not outcome.terminal and outcome.status == "PROCESSING"

    def test_failure_strips_the_reason_prefixes(self):
        body = _status_body(
            "FAILED",
            errorInfo={
                "errorCounts": [
                    {"reason": "PROCESSING_ERROR_REASON_EVENT_TOO_OLD", "recordCount": "1"}
                ]
            },
            warningInfo={
                "warningCounts": [{"reason": "PROCESSING_WARNING_REASON_UNSPECIFIED"}]
            },
        )
        outcome = classify_status(_resp(200, body))
        assert outcome.terminal and not outcome.healthy
        assert outcome.errors == (("EVENT_TOO_OLD", 1),)
        assert outcome.warnings == (("UNSPECIFIED", 0),)

    def test_partial_success_with_errors_is_unhealthy(self):
        body = _status_body(
            "PARTIAL_SUCCESS",
            errorInfo={"errorCounts": [{"reason": "X", "recordCount": 2}]},
        )
        outcome = classify_status(_resp(200, body))
        assert outcome.terminal and not outcome.healthy and outcome.errors == (("X", 2),)

    def test_empty_destination_list_counts_as_processing(self):
        outcome = classify_status(_resp(200, {"requestStatusPerDestination": []}))
        assert not outcome.terminal and outcome.status == "PROCESSING"

    @pytest.mark.parametrize("status", [404, 429, 500, 503])
    def test_not_filed_yet_and_transient_errors_keep_polling(self, status):
        outcome = classify_status(_resp(status, {"error": {"message": "later"}}))
        assert not outcome.terminal and outcome.http_status == status

    def test_403_ends_the_follow_up(self):
        outcome = classify_status(
            _resp(403, {"error": {"message": "no", "status": "PERMISSION_DENIED"}})
        )
        assert outcome.terminal and outcome.status == "http_4xx"
        assert outcome.message == "PERMISSION_DENIED: no"

    def test_non_json_200_is_terminal_bad_response(self):
        outcome = classify_status(_resp(200, "<html>"))
        assert outcome.terminal and outcome.status == "bad_response"


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.posts = []
        self.gets = []
        self.response = response
        self.error = error
        self.closed = False

    async def post(self, url, *, json, headers, timeout):
        self.posts.append((url, json, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.response

    async def get(self, url, *, params, headers, timeout):
        self.gets.append((url, params, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


def _client(transport, **kwargs):
    return DataManagerConversionClient(
        transport=transport,
        customer_id="1234567890",
        conversion_action_id="987654321",
        **kwargs,
    )


class TestClient:
    async def test_upload_posts_the_ingest_payload_and_classifies(self):
        transport = FakeTransport(TransportResponse(200, {}, {"requestId": "r"}))
        outcome = await _client(transport, login_customer_id="5555").upload(
            ConversionIdentifier("gclid", "c"), NOW, timeout=15.0
        )
        assert outcome.ok and outcome.request_id == "r"
        url, body, headers, timeout = transport.posts[0]
        assert url == INGEST_URL
        assert body["events"][0]["adIdentifiers"] == {"gclid": "c"}
        assert body["destinations"][0]["loginAccount"]["accountId"] == "5555"
        assert headers == {"Content-Type": "application/json"}
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
        assert outcome.credential_failure == (reason == "oauth_refresh_failed")

    async def test_request_status_gets_by_request_id(self):
        transport = FakeTransport(TransportResponse(200, {}, _status_body("SUCCESS")))
        outcome = await _client(transport).request_status("req-1", timeout=5.0)
        assert outcome.terminal and outcome.status == "SUCCESS"
        url, params, headers, timeout = transport.gets[0]
        assert (url, params, timeout) == (REQUEST_STATUS_URL, {"requestId": "req-1"}, 5.0)
        assert "Authorization" not in headers

    @pytest.mark.parametrize(
        "error, terminal, status",
        [
            (TransientTransportError("ConnectionError"), False, "PROCESSING"),
            (TimeoutError(), False, "PROCESSING"),
            (AuthRefreshFailed("RefreshError"), True, "oauth_refresh_failed"),
            (RuntimeError("bug"), True, "exception"),
        ],
    )
    async def test_request_status_never_raises(self, error, terminal, status):
        outcome = await _client(FakeTransport(error=error)).request_status("r", timeout=1.0)
        assert (outcome.terminal, outcome.status) == (terminal, status)

    def test_close_closes_transport(self):
        transport = FakeTransport()
        _client(transport).close()
        assert transport.closed

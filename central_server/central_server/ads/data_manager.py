"""Google Ads "Agent paired" conversion client — the pure half.

Builds the Data Manager ``events:ingest`` request, classifies the response,
reads a ``requestStatus:retrieve`` verdict and normalises/hashes emails. No
network and no google-auth import: those live in ``transport.py`` so this
module stays testable without credentials.

Why the Data Manager API rather than the Google Ads API's
``uploadClickConversions``: since 2026-06-15 that method rejects developer
tokens without prior conversion-upload history
(``CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE``) and Google names the Data
Manager API as the replacement. It needs no developer token and takes the
same click ids and hashed emails, but it is fast-fail and asynchronous: a
200 only means "accepted", and the match verdict is published through the
request-status endpoint between half an hour and a day later.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, NamedTuple, Protocol

API_BASE = "https://datamanager.googleapis.com/v1"
INGEST_URL = f"{API_BASE}/events:ingest"
REQUEST_STATUS_URL = f"{API_BASE}/requestStatus:retrieve"
DATAMANAGER_SCOPE = "https://www.googleapis.com/auth/datamanager"
CLICK_KINDS = frozenset({"gclid", "gbraid", "wbraid"})
HASHED_EMAIL = "hashed_email"
# The landing banner grants ad_storage + ad_user_data only and keeps
# personalization denied, so the upload must carry the same consent state.
CONSENT = {"adUserData": "CONSENT_GRANTED", "adPersonalization": "CONSENT_DENIED"}
# Google's click-through conversion window; an older click cannot be
# attributed, so the hashed-email path takes over.
CLICK_MAX_AGE = timedelta(days=90)
STATUS_PROCESSING = "PROCESSING"
# Outcomes where the credentials, not the event, were refused: a rejected
# refresh token, or a 401 / 403 (insufficient scope, no access to the
# account, Data Manager API not enabled). Nothing about the event can change
# that verdict, and nothing but the operator's fix can.
CREDENTIAL_REASONS = frozenset({"oauth_refresh_failed", "http_401", "http_403"})

_JSON_HEADERS = {"Content-Type": "application/json"}
_MESSAGE_LIMIT = 200
_GOOGLE_MAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_REASON_PREFIXES = ("PROCESSING_ERROR_REASON_", "PROCESSING_WARNING_REASON_")
_TRANSACTION_ID_LENGTH = 32


class TransientTransportError(Exception):
    """Network-level failure worth retrying."""


class AuthRefreshFailed(Exception):
    """The OAuth refresh token was rejected; retrying cannot help."""


class TransportResponse(NamedTuple):
    status: int
    headers: Mapping[str, str]
    body: Any


class ConversionTransport(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: dict,
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse: ...

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ConversionIdentifier:
    """What the conversion is keyed on: a click kind or ``hashed_email``.

    ``value`` is excluded from ``repr`` so an identifier can never leak into
    a log line by accident.
    """

    kind: str
    value: str = field(repr=False)


@dataclass(frozen=True)
class UploadOutcome:
    ok: bool
    permanent: bool
    reason: str
    status: int | None = None
    request_id: str | None = None
    message: str | None = None

    @property
    def credential_failure(self) -> bool:
        """Refused credentials rather than a refused event — see
        ``CREDENTIAL_REASONS``. The upload is worth keeping for the process
        that runs with the corrected token."""
        return self.reason in CREDENTIAL_REASONS


@dataclass(frozen=True)
class StatusOutcome:
    """A ``requestStatus:retrieve`` verdict; ``terminal`` is False while
    Google is still processing or has not filed the request yet."""

    status: str
    terminal: bool
    records: int | None = None
    errors: tuple[tuple[str, int], ...] = ()
    warnings: tuple[tuple[str, int], ...] = ()
    http_status: int | None = None
    message: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == "SUCCESS" and not self.errors


def normalize_email(email: str) -> str:
    """Google's user-data formatting: drop every whitespace character
    (leading, trailing and intermediate), lowercase, and for gmail.com /
    googlemail.com drop every ``.`` and the ``+suffix`` from the local part.
    Other domains keep both."""
    candidate = "".join(email.split()).lower()
    local, sep, domain = candidate.rpartition("@")
    if not sep:
        return candidate
    if domain in _GOOGLE_MAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def hash_email(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def format_event_timestamp(dt: datetime) -> str:
    """RFC 3339 in UTC at whole seconds — ``2026-09-02T12:18:00Z``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest_timestamp(dt: datetime) -> str:
    """The pairing instant at its stored microsecond precision, in UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def transaction_id(identifier: ConversionIdentifier, event_time: datetime) -> str:
    """Google's deduplication key for the event.

    A retry after a lost response, and a resumed upload after a restart,
    re-send the same event; without this key Google would count each copy.
    A one-way digest of the identifier and the pairing instant and nothing
    else, so it discloses nothing beyond what the report already carries
    (the report shows the same instant at second precision) — the privacy
    policy's "nothing else about your account" holds — and it is identical
    across retries and processes because both inputs are stored: the
    identifier on the account row, the instant in ``first_paired_at``. The
    instant goes in at its stored microseconds rather than the second the
    report shows: the ``mobaide_ads`` cookie is not consumed at sign-up, so
    two accounts created from one click that pair within the same second
    must not collapse into one key.
    """
    material = "\n".join(
        (identifier.kind, identifier.value, _digest_timestamp(event_time))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_TRANSACTION_ID_LENGTH]


def parse_iso_utc(text: Any) -> datetime | None:
    if not text or not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def select_identifier(
    row: Mapping[str, Any],
    now: datetime,
    *,
    email_allowed: bool = True,
    click_window: timedelta = CLICK_MAX_AGE,
) -> ConversionIdentifier | None:
    """Fresh click id first, hashed email as the fallback — never both.

    Anything older than the conversion window is dropped rather than
    uploaded to a guaranteed no-match. The click timestamp comes from the
    visitor's clock, so the server-stamped sign-up time caps it: age runs
    from whichever of the two is earlier, and the email fallback (matched
    by Google against a click that necessarily predates sign-up) ages from
    sign-up alone. The fallback carries personal data, so it needs the
    caller's ``email_allowed`` — the account's network-bound consent.
    ``click_window`` is the conversion action's click-through window: a
    click outside it would be uploaded to a guaranteed no-match while the
    usable fallback went untried.
    """
    created_at = parse_iso_utc(row.get("created_at"))
    click_id = row.get("ad_click_id")
    kind = row.get("ad_click_kind")
    if click_id and kind in CLICK_KINDS:
        clicked_at = parse_iso_utc(row.get("ad_click_at"))
        candidates = [stamp for stamp in (clicked_at, created_at) if stamp is not None]
        if not candidates or now - min(candidates) <= click_window:
            return ConversionIdentifier(str(kind), str(click_id))
    email = row.get("email")
    if (
        email_allowed
        and email
        and (created_at is None or now - created_at <= click_window)
    ):
        return ConversionIdentifier(HASHED_EMAIL, hash_email(str(email)))
    return None


def build_destination(
    *,
    customer_id: str,
    conversion_action_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    """The operating account must be the account that owns the conversion
    action; the login account is the manager the OAuth user reaches it
    through, when there is one."""
    destination: dict[str, Any] = {
        "operatingAccount": {"accountType": "GOOGLE_ADS", "accountId": customer_id},
        "productDestinationId": conversion_action_id,
    }
    if login_customer_id:
        destination["loginAccount"] = {
            "accountType": "GOOGLE_ADS",
            "accountId": login_customer_id,
        }
    return destination


def build_ingest_payload(
    *,
    customer_id: str,
    conversion_action_id: str,
    identifier: ConversionIdentifier,
    event_time: datetime,
    login_customer_id: str | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    """One event for one destination.

    The privacy policy promises the report carries the click id (or the
    email hash), the consent state and nothing else about the account. The
    ``transactionId`` keeps that promise: it is a digest of those very
    fields (see ``transaction_id``), there so that a re-sent event is
    deduplicated rather than counted twice.
    """
    event: dict[str, Any] = {
        "eventTimestamp": format_event_timestamp(event_time),
        "eventSource": "WEB",
        "transactionId": transaction_id(identifier, event_time),
        "consent": dict(CONSENT),
    }
    if identifier.kind == HASHED_EMAIL:
        event["userData"] = {"userIdentifiers": [{"emailAddress": identifier.value}]}
    else:
        event["adIdentifiers"] = {identifier.kind: identifier.value}
    payload: dict[str, Any] = {
        "destinations": [
            build_destination(
                customer_id=customer_id,
                conversion_action_id=conversion_action_id,
                login_customer_id=login_customer_id,
            )
        ],
        "events": [event],
    }
    if identifier.kind == HASHED_EMAIL:
        # Required with userData: says how the SHA-256 digest is encoded.
        payload["encoding"] = "HEX"
    if validate_only:
        payload["validateOnly"] = True
    return payload


def _truncate(text: Any) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= _MESSAGE_LIMIT else text[:_MESSAGE_LIMIT]


def _error_message(body: Any) -> Any:
    """``status: message`` from a google.rpc.Status body; ``details`` can
    echo uploaded values, so it is never read."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        message = error.get("message")
        status = error.get("status")
        if status and message:
            return f"{status}: {message}"
        return message or status
    return body


def classify_response(resp: TransportResponse) -> UploadOutcome:
    """Sort an ingest response into ok / retry / give-up.

    Fast-fail model: a 200 accepts the whole request for processing (the
    body carries ``requestId`` and at most field warnings) and a 4xx rejects
    it outright — there is no partial-failure envelope to inspect. A 200
    without a ``requestId`` is not evidence of acceptance: the id is the
    only handle on the request, so the response is treated as broken.
    """
    status = resp.status
    body = resp.body
    if status == 200:
        request_id = body.get("requestId") if isinstance(body, dict) else None
        if not isinstance(request_id, str) or not request_id:
            message = "missing_request_id" if isinstance(body, dict) else _truncate(body)
            return UploadOutcome(False, True, "bad_response", status, None, message)
        warnings = body.get("fieldWarnings")
        message = (
            f"field_warnings={len(warnings)}"
            if isinstance(warnings, list) and warnings
            else None
        )
        return UploadOutcome(True, False, "ok", status, request_id, message)
    message = _truncate(_error_message(body))
    if status == 429:
        return UploadOutcome(False, False, "http_429", status, None, message)
    if 500 <= status <= 599:
        return UploadOutcome(False, False, "http_5xx", status, None, message)
    if status in (401, 403):
        return UploadOutcome(False, True, f"http_{status}", status, None, message)
    if 400 <= status <= 499:
        return UploadOutcome(False, True, "http_4xx", status, None, message)
    return UploadOutcome(False, True, "bad_response", status, None, message)


def _strip_reason(reason: Any) -> str:
    text = str(reason or "UNSPECIFIED")
    for prefix in _REASON_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _counts(info: Any, key: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(info, dict) or not isinstance(info.get(key), list):
        return ()
    return tuple(
        (_strip_reason(item.get("reason")), _int(item.get("recordCount")) or 0)
        for item in info[key]
        if isinstance(item, dict)
    )


def classify_status(resp: TransportResponse) -> StatusOutcome:
    """Read the verdict for one earlier ingest request.

    Only the first destination is read — every upload names exactly one. A
    404 means Google has not filed the request yet (statuses become
    retrievable once processing starts), so it stays pending like a 429 or
    a 5xx; any other 4xx ends the follow-up.
    """
    status = resp.status
    body = resp.body
    if status == 200 and isinstance(body, dict):
        entries = body.get("requestStatusPerDestination")
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            return StatusOutcome(STATUS_PROCESSING, False, http_status=status)
        entry = entries[0]
        verdict = str(entry.get("requestStatus") or "REQUEST_STATUS_UNKNOWN")
        events = entry.get("eventsIngestionStatus")
        records = _int(events.get("recordCount")) if isinstance(events, dict) else None
        return StatusOutcome(
            verdict,
            verdict != STATUS_PROCESSING,
            records,
            _counts(entry.get("errorInfo"), "errorCounts"),
            _counts(entry.get("warningInfo"), "warningCounts"),
            status,
        )
    message = _truncate(_error_message(body))
    if status in (404, 429) or 500 <= status <= 599:
        return StatusOutcome(STATUS_PROCESSING, False, http_status=status, message=message)
    reason = "http_4xx" if 400 <= status <= 499 else "bad_response"
    return StatusOutcome(reason, True, http_status=status, message=message)


class DataManagerConversionClient:
    """One conversion action on one Google Ads account; nothing here raises."""

    def __init__(
        self,
        *,
        transport: ConversionTransport,
        customer_id: str,
        conversion_action_id: str,
        login_customer_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._customer_id = customer_id
        self._conversion_action_id = conversion_action_id
        self._login_customer_id = login_customer_id

    async def upload(
        self,
        identifier: ConversionIdentifier,
        event_time: datetime,
        *,
        timeout: float,
    ) -> UploadOutcome:
        payload = build_ingest_payload(
            customer_id=self._customer_id,
            conversion_action_id=self._conversion_action_id,
            identifier=identifier,
            event_time=event_time,
            login_customer_id=self._login_customer_id,
        )
        try:
            response = await self._transport.post(
                INGEST_URL, json=payload, headers=_JSON_HEADERS, timeout=timeout
            )
        except AuthRefreshFailed:
            return UploadOutcome(False, True, "oauth_refresh_failed")
        except TransientTransportError as exc:
            return UploadOutcome(False, False, "transport", message=_truncate(exc))
        except TimeoutError:
            return UploadOutcome(False, False, "transport", message="timeout")
        except Exception as exc:  # a transport bug must not kill the task
            return UploadOutcome(
                False, True, "exception", message=type(exc).__name__
            )
        return classify_response(response)

    async def request_status(self, request_id: str, *, timeout: float) -> StatusOutcome:
        try:
            response = await self._transport.get(
                REQUEST_STATUS_URL,
                params={"requestId": request_id},
                headers={},
                timeout=timeout,
            )
        except AuthRefreshFailed:
            return StatusOutcome("oauth_refresh_failed", True)
        except (TransientTransportError, TimeoutError) as exc:
            return StatusOutcome(
                STATUS_PROCESSING, False, message=_truncate(exc) or "timeout"
            )
        except Exception as exc:
            return StatusOutcome("exception", True, message=type(exc).__name__)
        return classify_status(response)

    def close(self) -> None:
        self._transport.close()

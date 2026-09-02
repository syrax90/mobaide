"""Google Ads offline click-conversion client — the pure half.

Builds the ``uploadClickConversions`` request, classifies the response and
normalises/hashes emails. No network and no google-auth import: those live in
``transport.py`` so this module stays testable without credentials.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, NamedTuple, Protocol

DEFAULT_API_VERSION = "v25"
API_BASE = "https://googleads.googleapis.com"
CLICK_KINDS = frozenset({"gclid", "gbraid", "wbraid"})
HASHED_EMAIL = "hashed_email"
# The landing banner grants ad_storage + ad_user_data only and keeps
# personalization denied, so the upload must carry the same consent state.
CONSENT = {"adUserData": "GRANTED", "adPersonalization": "DENIED"}
# Google's click-through conversion window; an older click cannot be
# attributed, so the hashed-email path takes over.
CLICK_MAX_AGE = timedelta(days=90)

_MESSAGE_LIMIT = 200
_GOOGLE_MAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


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


def normalize_email(email: str) -> str:
    """Google's enhanced-conversions normalisation: trim + lowercase, and for
    gmail.com / googlemail.com drop every ``.`` and the ``+suffix`` from the
    local part (both are spelled out in the API docs, with
    ``Jane.Doe+Shopping@googlemail.com`` → ``janedoe@googlemail.com``, and the
    official client-library sample strips the same two characters). Other
    domains keep both."""
    candidate = email.strip().lower()
    local, sep, domain = candidate.rpartition("@")
    if not sep:
        return candidate
    if domain in _GOOGLE_MAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def hash_email(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def format_conversion_datetime(dt: datetime) -> str:
    """``yyyy-mm-dd HH:mm:ss+00:00`` — the only shape the API accepts."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + "+00:00"


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
    row: Mapping[str, Any], now: datetime, *, email_allowed: bool = True
) -> ConversionIdentifier | None:
    """Fresh click id first, hashed email as the fallback — never both.

    Anything older than the conversion window is dropped rather than
    uploaded to a guaranteed no-match. The click timestamp comes from the
    visitor's clock, so the server-stamped sign-up time caps it: age runs
    from whichever of the two is earlier, and the email fallback (matched
    by Google against a click that necessarily predates sign-up) ages from
    sign-up alone. The fallback carries personal data, so it needs the
    caller's ``email_allowed`` — the account's network-bound consent.
    """
    created_at = parse_iso_utc(row.get("created_at"))
    click_id = row.get("ad_click_id")
    kind = row.get("ad_click_kind")
    if click_id and kind in CLICK_KINDS:
        clicked_at = parse_iso_utc(row.get("ad_click_at"))
        candidates = [stamp for stamp in (clicked_at, created_at) if stamp is not None]
        if not candidates or now - min(candidates) <= CLICK_MAX_AGE:
            return ConversionIdentifier(str(kind), str(click_id))
    email = row.get("email")
    if (
        email_allowed
        and email
        and (created_at is None or now - created_at <= CLICK_MAX_AGE)
    ):
        return ConversionIdentifier(HASHED_EMAIL, hash_email(str(email)))
    return None


def upload_url(api_version: str, customer_id: str) -> str:
    return f"{API_BASE}/{api_version}/customers/{customer_id}:uploadClickConversions"


def build_headers(
    *, developer_token: str, login_customer_id: str | None
) -> dict[str, str]:
    """Everything but ``Authorization``, which the OAuth session attaches."""
    headers = {
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    return headers


def build_conversion_payload(
    *,
    customer_id: str,
    conversion_action_id: str,
    identifier: ConversionIdentifier,
    conversion_time: datetime,
) -> dict[str, Any]:
    conversion: dict[str, Any] = {
        "conversionAction": (
            f"customers/{customer_id}/conversionActions/{conversion_action_id}"
        ),
        "conversionDateTime": format_conversion_datetime(conversion_time),
    }
    if identifier.kind == HASHED_EMAIL:
        conversion["userIdentifiers"] = [
            {"userIdentifierSource": "FIRST_PARTY", "hashedEmail": identifier.value}
        ]
    else:
        conversion[identifier.kind] = identifier.value
    conversion["consent"] = dict(CONSENT)
    return {"conversions": [conversion], "partialFailure": True}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _truncate(text: Any) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= _MESSAGE_LIMIT else text[:_MESSAGE_LIMIT]


def classify_response(resp: TransportResponse) -> UploadOutcome:
    """Sort a response into ok / retry / give-up.

    A 200 can still carry ``partialFailureError``; only its top-level
    ``message`` is kept because ``details[].errors[].trigger`` echoes the
    uploaded identifier, which must never reach the logs.
    """
    request_id = _header(resp.headers, "request-id")
    status = resp.status
    body = resp.body
    if status == 200:
        if not isinstance(body, dict):
            return UploadOutcome(
                False, True, "bad_response", status, request_id, _truncate(body)
            )
        partial = body.get("partialFailureError")
        if partial:
            message = partial.get("message") if isinstance(partial, dict) else None
            return UploadOutcome(
                False, True, "partial_failure", status, request_id, _truncate(message)
            )
        return UploadOutcome(True, False, "ok", status, request_id, None)

    message: Any = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
    elif body is not None:
        message = body
    if status == 429:
        return UploadOutcome(
            False, False, "http_429", status, request_id, _truncate(message)
        )
    if 500 <= status <= 599:
        return UploadOutcome(
            False, False, "http_5xx", status, request_id, _truncate(message)
        )
    if 400 <= status <= 499:
        return UploadOutcome(
            False, True, "http_4xx", status, request_id, _truncate(message)
        )
    return UploadOutcome(
        False, True, "bad_response", status, request_id, _truncate(message)
    )


class GoogleAdsConversionClient:
    """One conversion action on one customer; ``upload`` never raises."""

    def __init__(
        self,
        *,
        transport: ConversionTransport,
        developer_token: str,
        customer_id: str,
        conversion_action_id: str,
        login_customer_id: str | None = None,
        api_version: str = DEFAULT_API_VERSION,
    ) -> None:
        self._transport = transport
        self._customer_id = customer_id
        self._conversion_action_id = conversion_action_id
        self._url = upload_url(api_version, customer_id)
        self._headers = build_headers(
            developer_token=developer_token, login_customer_id=login_customer_id
        )

    async def upload(
        self,
        identifier: ConversionIdentifier,
        conversion_time: datetime,
        *,
        timeout: float,
    ) -> UploadOutcome:
        payload = build_conversion_payload(
            customer_id=self._customer_id,
            conversion_action_id=self._conversion_action_id,
            identifier=identifier,
            conversion_time=conversion_time,
        )
        try:
            response = await self._transport.post(
                self._url, json=payload, headers=self._headers, timeout=timeout
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

    def close(self) -> None:
        self._transport.close()

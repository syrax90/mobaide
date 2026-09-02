"""Server-issued, HMAC-signed first-party attribution cookie.

A cookie written from script on ``mobaide.com`` proves nothing about consent:
any host on the registrable domain can plant a ``Domain=mobaide.com`` cookie,
and ``get.mobaide.com`` is third-party infrastructure. So Accept asks the
server for the cookie, the server stamps and signs it, and the sign-up path
stores attribution only when the signature verifies. The key is derived from
the JWT secret, so no new configuration is needed.

Wire shape of the ``mobaide_ads`` cookie: ``<base64url(payload)>.<signature>``
with payload ``v=1&k=<kind>&t=<epoch>&id=<click id>`` (``k``/``id`` absent on
a consent-only visit). Base64url because a raw ``=`` would make Starlette
quote the cookie value. ``t`` is the landing-page arrival time the browser
reports, capped at the server clock.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
from urllib.parse import urlencode

COOKIE_NAME = "mobaide_ads"
COOKIE_MAX_AGE = 90 * 24 * 3600
_KEY_CONTEXT = b"mobaide-ads-attribution-cookie-v1"


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), _KEY_CONTEXT, hashlib.sha256).digest()


def canonical_payload(
    *, click_id: str | None, click_id_kind: str | None, clicked_at: int
) -> str:
    params: list[tuple[str, str]] = [("v", "1")]
    if click_id is not None:
        params.append(("k", str(click_id_kind)))
    params.append(("t", str(int(clicked_at))))
    if click_id is not None:
        params.append(("id", str(click_id)))
    return urlencode(params)


def sign_attribution(
    secret: str, *, click_id: str | None, click_id_kind: str | None, clicked_at: int
) -> str:
    payload = canonical_payload(
        click_id=click_id, click_id_kind=click_id_kind, clicked_at=clicked_at
    )
    digest = hmac.new(_key(secret), payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_attribution(
    secret: str,
    *,
    click_id: str | None,
    click_id_kind: str | None,
    clicked_at: int | None,
    signature: str | None,
) -> bool:
    """True only for a value this server minted; never raises on hostile input."""
    if clicked_at is None or not signature:
        return False
    if (click_id is None) != (click_id_kind is None):
        return False
    expected = sign_attribution(
        secret, click_id=click_id, click_id_kind=click_id_kind, clicked_at=clicked_at
    )
    try:
        presented = signature.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(expected.encode("ascii"), presented)


def cookie_value(
    secret: str, *, click_id: str | None, click_id_kind: str | None, clicked_at: int
) -> str:
    payload = canonical_payload(
        click_id=click_id, click_id_kind=click_id_kind, clicked_at=clicked_at
    )
    encoded = base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")
    signature = sign_attribution(
        secret, click_id=click_id, click_id_kind=click_id_kind, clicked_at=clicked_at
    )
    return f"{encoded.rstrip('=')}.{signature}"


def cookie_domain(hostname: str | None) -> str | None:
    """``Domain`` attribute: the request host, so the SPA on a sibling host
    can read the cookie — host-only for localhost and IP literals, which
    browsers refuse a Domain attribute for."""
    if not hostname or hostname.lower() == "localhost":
        return None
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return hostname
    return None

"""Server-issued, HMAC-signed first-party attribution cookie.

A cookie written from script on ``mobaide.com`` proves nothing about consent:
any host on the registrable domain can plant a ``Domain=mobaide.com`` cookie,
and ``get.mobaide.com`` is third-party infrastructure. So Accept asks the
server for the cookie, the server stamps and signs it, and the sign-up path
stores attribution only when the signature verifies. The key is derived from
the JWT secret, so no new configuration is needed.

A signature alone still does not bind the value to a visitor: an attacker's
own HTTP client can spoof the same-origin headers, mint a valid value and
have sibling-host script plant it for victims. So the payload also carries
``n``, a keyed fingerprint of the network the Accept came from, and sign-up
honours the *consent* (the permission to fall back to a hashed email) only
when it arrives from that same network. Click ids hold no personal data and
travel regardless of address changes.

Wire shape of the ``mobaide_ads`` cookie: ``<base64url(payload)>.<signature>``
with payload ``v=1&k=<kind>&t=<epoch>&id=<click id>&n=<network>`` (``k``/``id``
absent on a consent-only visit). Base64url because a raw ``=`` would make
Starlette quote the cookie value. ``t`` is the landing-page arrival time the
browser reports, capped at the server clock.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode

COOKIE_NAME = "mobaide_ads"
COOKIE_MAX_AGE = 90 * 24 * 3600
CLICK_KINDS = ("gclid", "wbraid", "gbraid")
_CLICK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_CLICKED_AT = 4_102_444_800
_KEY_CONTEXT = b"mobaide-ads-attribution-cookie-v1"
_NETWORK_CONTEXT = b"mobaide-ads-attribution-network-v1"


def _key(secret: str, context: bytes = _KEY_CONTEXT) -> bytes:
    return hmac.new(secret.encode("utf-8"), context, hashlib.sha256).digest()


def _normalize_network(client_ip: str | None) -> str:
    """The exact address for IPv4; the /64 for IPv6, whose privacy
    extensions rotate the host half within hours. IPv4-mapped IPv6 collapses
    to the IPv4 it carries."""
    if not client_ip:
        return "unknown"
    try:
        address = ipaddress.ip_address(client_ip.strip("[]"))
    except ValueError:
        return client_ip.strip().lower()
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network((address, 64), strict=False))
    return str(address)


def network_fingerprint(secret: str, client_ip: str | None) -> str:
    """Keyed, truncated hash of the client's network — unmintable without
    the key and not reversible, so it can live in the visitor's cookie."""
    digest = hmac.new(
        _key(secret, _NETWORK_CONTEXT),
        _normalize_network(client_ip).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest[:12]).decode("ascii").rstrip("=")


def canonical_payload(
    *,
    click_id: str | None,
    click_id_kind: str | None,
    clicked_at: int,
    network: str,
) -> str:
    params: list[tuple[str, str]] = [("v", "1")]
    if click_id is not None:
        params.append(("k", str(click_id_kind)))
    params.append(("t", str(int(clicked_at))))
    if click_id is not None:
        params.append(("id", str(click_id)))
    params.append(("n", network))
    return urlencode(params)


def sign_attribution(
    secret: str,
    *,
    click_id: str | None,
    click_id_kind: str | None,
    clicked_at: int,
    network: str,
) -> str:
    payload = canonical_payload(
        click_id=click_id,
        click_id_kind=click_id_kind,
        clicked_at=clicked_at,
        network=network,
    )
    digest = hmac.new(_key(secret), payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_attribution(
    secret: str,
    *,
    click_id: str | None,
    click_id_kind: str | None,
    clicked_at: int | None,
    network: str | None,
    signature: str | None,
) -> bool:
    """True only for a value this server minted; never raises on hostile input."""
    if clicked_at is None or not network or not signature:
        return False
    if (click_id is None) != (click_id_kind is None):
        return False
    try:
        expected = sign_attribution(
            secret,
            click_id=click_id,
            click_id_kind=click_id_kind,
            clicked_at=clicked_at,
            network=network,
        )
        presented = signature.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(expected.encode("ascii"), presented)


def cookie_value(
    secret: str,
    *,
    click_id: str | None,
    click_id_kind: str | None,
    clicked_at: int,
    network: str,
) -> str:
    payload = canonical_payload(
        click_id=click_id,
        click_id_kind=click_id_kind,
        clicked_at=clicked_at,
        network=network,
    )
    encoded = base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")
    signature = sign_attribution(
        secret,
        click_id=click_id,
        click_id_kind=click_id_kind,
        clicked_at=clicked_at,
        network=network,
    )
    return f"{encoded.rstrip('=')}.{signature}"


@dataclass(frozen=True)
class ParsedAttribution:
    click_id: str | None
    click_id_kind: str | None
    clicked_at: int
    network: str
    signature: str


def parse_cookie_value(value: object) -> ParsedAttribution | None:
    """Decode a ``mobaide_ads`` value the way the SPA does — shape only, no
    signature check; ``None`` for anything off."""
    if not isinstance(value, str) or len(value) > 512:
        return None
    encoded, dot, signature = value.rpartition(".")
    if not dot or not _TOKEN_RE.fullmatch(signature):
        return None
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
            "ascii"
        )
    except (ValueError, UnicodeDecodeError):
        return None
    single = {
        key: values[0]
        for key, values in parse_qs(payload, keep_blank_values=True).items()
        if len(values) == 1
    }
    if single.get("v") != "1":
        return None
    stamp = single.get("t")
    if stamp is None or not stamp.isascii() or not stamp.isdigit():
        return None
    if int(stamp) > _MAX_CLICKED_AT:
        return None
    network = single.get("n")
    if network is None or not _TOKEN_RE.fullmatch(network):
        return None
    kind = single.get("k")
    click_id = single.get("id")
    if (kind is None) != (click_id is None):
        return None
    if kind is not None and (
        kind not in CLICK_KINDS or not _CLICK_ID_RE.fullmatch(click_id or "")
    ):
        return None
    return ParsedAttribution(click_id, kind, int(stamp), network, signature)


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

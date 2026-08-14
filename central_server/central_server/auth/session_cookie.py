"""Browser session cookies for the refreshable user session.

Every name/attribute pairing lives here so the set, read and clear paths
cannot drift apart. Two rules make that non-optional:

* ``__Host-`` is conditional on ``Secure``. A browser silently drops the whole
  cookie when the prefix arrives without it, so the names are derived from
  ``user_session_cookie_secure`` rather than chosen independently.
* Starlette's ``delete_cookie`` defaults to ``secure=False``, which a browser
  rejects as a prefix violation — leaving the cookie alive. Every clear here
  spells the attributes out, and clears both the prefixed and unprefixed names
  so flipping the flag strands nothing in an existing jar.

The refresh cookie is the credential; ``signed_in`` is a readable single bit
that lets a booting SPA tell "signed out" from "signed in, needs a round trip"
without a wasted request. They are written and deleted together, always in the
same response, which is what makes it impossible for the marker to outlive or
under-live the credential.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, Response, status

if TYPE_CHECKING:
    from ..config import CentralServerConfig

REFRESH_COOKIE_SECURE = "__Host-mobaide_refresh"
REFRESH_COOKIE_INSECURE = "mobaide_refresh"
SIGNED_IN_COOKIE_SECURE = "__Host-mobaide_signed_in"
SIGNED_IN_COOKIE_INSECURE = "mobaide_signed_in"

# Presence alone is the CSRF proof. A custom header makes the request
# non-simple, which forces a CORS preflight; with no CORSMiddleware installed
# the OPTIONS gets a bare 405 and the browser never sends the real request.
# SameSite=Strict cannot carry this alone: it is scoped to the registrable
# domain, so a page on a sibling host such as get.mobaide.com (GitHub Pages)
# is same-site and its forged POST would arrive with the cookie attached.
ORIGIN_PROOF_HEADER = "X-Mobaide-Session"

_CROSS_ORIGIN_FETCH_SITES = frozenset({"cross-site", "same-site"})


def refresh_cookie_name(config: "CentralServerConfig") -> str:
    return (
        REFRESH_COOKIE_SECURE
        if config.user_session_cookie_secure
        else REFRESH_COOKIE_INSECURE
    )


def read_refresh_cookie(
    request: Request, config: "CentralServerConfig"
) -> str | None:
    """Read the refresh credential under exactly one name.

    Accepting the unprefixed name as a fallback would undo the only thing the
    ``__Host-`` prefix buys. The prefix forbids a ``Domain`` attribute, which
    is what stops a sibling host — ``get.mobaide.com`` is served from GitHub
    Pages, and any ``*.mobaide.com`` takeover or XSS qualifies — from writing
    a cookie this origin will send. The bare name carries no such protection:
    a sibling can set ``mobaide_refresh=<their token>; Domain=mobaide.com``,
    and a server that honours it adopts an attacker-chosen session for the
    victim. Session fixation, straight through the prefix.

    So a secure deployment reads only the prefixed name, and a dev deployment
    (where the prefix cannot exist, because it requires ``Secure``) reads only
    the bare one. Clearing still targets both, so flipping the flag strands
    nothing in an existing jar.
    """
    return request.cookies.get(refresh_cookie_name(config))


def _max_age(config: "CentralServerConfig") -> int | None:
    # Max-Age=0 deletes a cookie (RFC 6265 §5.2.2), so a zero TTL must omit the
    # attribute entirely and produce a browser-session cookie instead. `from_env`
    # clamps this to a positive minimum, but CentralServerConfig is constructed
    # directly by the test conftest and by web/scripts/dump_openapi.py.
    minutes = config.user_session_refresh_expire_minutes
    return minutes * 60 if minutes > 0 else None


def set_session_cookies(
    response: Response, refresh_token: str, config: "CentralServerConfig"
) -> None:
    secure = config.user_session_cookie_secure
    max_age = _max_age(config)
    response.set_cookie(
        key=refresh_cookie_name(config),
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )
    response.set_cookie(
        key=(
            SIGNED_IN_COOKIE_SECURE if secure else SIGNED_IN_COOKIE_INSECURE
        ),
        value="1",
        # Readable on purpose: this is the bit the SPA's boot path checks.
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )


def clear_session_cookies(
    response: Response, config: "CentralServerConfig"
) -> None:
    """Delete both cookies under both names, with matching attributes."""
    del config  # Deletions are unconditional; the flag may have changed.
    for name, secure in (
        (REFRESH_COOKIE_SECURE, True),
        (REFRESH_COOKIE_INSECURE, False),
    ):
        response.delete_cookie(
            key=name, httponly=True, secure=secure, samesite="strict", path="/"
        )
    for name, secure in (
        (SIGNED_IN_COOKIE_SECURE, True),
        (SIGNED_IN_COOKIE_INSECURE, False),
    ):
        response.delete_cookie(
            key=name, httponly=False, secure=secure, samesite="strict", path="/"
        )


def require_origin_proof(request: Request) -> None:
    """Reject a cookie-authenticated write that a browser could have forged.

    Only the cookie path calls this — a request carrying its refresh token in
    the body is a native client, which is not subject to CSRF and cannot be
    asked to set custom headers on a redirect-driven flow.
    """
    if request.headers.get(ORIGIN_PROOF_HEADER) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing session origin proof",
        )
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None and fetch_site.lower() in _CROSS_ORIGIN_FETCH_SITES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin session request",
        )

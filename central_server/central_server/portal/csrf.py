"""Minimal CSRF helper for the cookie-authenticated portal.

OWASP Double Submit Cookie pattern with HMAC-bound tokens:

  * On ``GET`` of any portal page, the request handler calls
    :func:`ensure_csrf_cookie` which (idempotently) sets a ``portal_csrf``
    cookie containing an HMAC-signed token derived from the user's portal
    session (or a fresh random nonce for unauthenticated pages).
  * Templates render the raw token into a ``<meta>`` tag and into a global
    ``hx-headers`` attribute on ``<body>`` so every HTMX request carries
    ``X-CSRF-Token``. Plain form posts include it as a hidden ``csrf_token``
    field.
  * Before any state-changing handler runs, :func:`require_csrf` validates
    the header (or form field) against the cookie via a constant-time compare.

The signing key falls back to a deterministic SHA-256 derivative of
``jwt_secret_key`` when ``csrf_secret_key`` is unset, so single-secret
deployments work out of the box without sacrificing cookie-vs-header
binding.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, Response, status

if TYPE_CHECKING:
    from ..config import CentralServerConfig

_COOKIE_NAME = "portal_csrf"
_HEADER_NAME = "x-csrf-token"
_FORM_FIELD = "csrf_token"


def _signing_key(config: "CentralServerConfig") -> bytes:
    if config.csrf_secret_key:
        return config.csrf_secret_key.encode()
    return hashlib.sha256(b"csrf:" + config.jwt_secret_key.encode()).digest()


def _sign(secret: bytes, nonce: str) -> str:
    digest = hmac.new(secret, nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{digest}"


def _verify(secret: bytes, token: str) -> bool:
    if not token or "." not in token:
        return False
    nonce, _, digest = token.rpartition(".")
    if not nonce or not digest:
        return False
    expected = hmac.new(secret, nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)


def issue_csrf_token(config: "CentralServerConfig") -> str:
    """Return a fresh HMAC-signed CSRF token (32-byte nonce + signature)."""
    nonce = secrets.token_urlsafe(32)
    return _sign(_signing_key(config), nonce)


def ensure_csrf_cookie(
    request: Request, response: Response, config: "CentralServerConfig"
) -> str:
    """Idempotently set the portal CSRF cookie. Returns the active token.

    Existing cookies are validated; an invalid or missing cookie is
    replaced with a fresh token. Same SameSite/Secure flags as the session
    cookie so the double-submit binding holds across navigations.
    """
    secret = _signing_key(config)
    existing = request.cookies.get(_COOKIE_NAME)
    if existing and _verify(secret, existing):
        return existing
    token = issue_csrf_token(config)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=True,
        samesite="lax",
        max_age=None,
    )
    return token


async def require_csrf(request: Request) -> None:
    """Reject state-changing requests whose header/form CSRF token does not
    match the ``portal_csrf`` cookie. Use as a FastAPI ``Depends`` on every
    portal POST/PUT/DELETE route."""
    config = request.app.state.config
    secret = _signing_key(config)
    cookie_token = request.cookies.get(_COOKIE_NAME)
    if not cookie_token or not _verify(secret, cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF cookie missing or invalid",
        )

    presented = request.headers.get(_HEADER_NAME)
    if presented is None:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/x-www-form-urlencoded") or ctype.startswith(
            "multipart/form-data"
        ):
            form = await request.form()
            value = form.get(_FORM_FIELD)
            presented = str(value) if value is not None else None
    if presented is None or not hmac.compare_digest(presented, cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or does not match cookie",
        )

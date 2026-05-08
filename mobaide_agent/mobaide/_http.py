"""Outbound HTTP helper for the mobaide agent.

Centralises ``urllib.request`` calls so a descriptive
``User-Agent: mobaide-agent/<version>`` is always set.

Cloudflare's stock Bot Fight Mode (error 1010 — "browser signature ban")
blocks the literal ``Python-urllib/X.Y`` UA at the edge with HTTP 403 before
the request reaches the origin. A self-identifying first-party UA passes the
default rule set the same way ``curl``, ``pip``, ``npm``, etc. do.
"""
from __future__ import annotations

import urllib.request
from typing import Mapping

from mobaide import __version__

USER_AGENT = f"mobaide-agent/{__version__}"


def _has_user_agent(request: urllib.request.Request) -> bool:
    """Case-insensitive check across both urllib header dicts.

    ``Request.add_header`` writes to ``headers``; ``add_unredirected_header``
    (used by some auth handlers) writes to ``unredirected_hdrs``. Either is
    visible to the HTTP handler, so both are checked.
    """
    return any(
        k.lower() == "user-agent"
        for k in (*request.headers.keys(), *request.unredirected_hdrs.keys())
    )


def urlopen(
    url_or_request: str | urllib.request.Request,
    *,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    method: str | None = None,
    timeout: float,
):
    """Wrap ``urllib.request.urlopen`` and inject a ``User-Agent`` header.

    Accepts a URL string or a pre-built ``urllib.request.Request``. If the
    caller already set a ``User-Agent``, it is preserved — tests can
    override for diagnostics.

    ``timeout`` is keyword-only and required: every outbound call in this
    codebase sets one explicitly, and forgetting one would silently block
    the asyncio event loop.
    """
    if isinstance(url_or_request, urllib.request.Request):
        if data is not None or headers is not None or method is not None:
            raise TypeError(
                "data/headers/method are only valid when passing a URL string"
            )
        request = url_or_request
    else:
        request = urllib.request.Request(
            url_or_request,
            data=data,
            headers=dict(headers) if headers else {},
            method=method,
        )

    if not _has_user_agent(request):
        request.add_header("User-Agent", USER_AGENT)

    return urllib.request.urlopen(request, timeout=timeout)

"""google-auth backed transport for the Data Manager conversion client.

The only module in the package that imports google-auth. ``AuthorizedSession``
refreshes the access token on first use and again on a 401, so the server
holds nothing but the refresh token. Calls are serialised on one lock and run
in the default executor: ``requests.Session`` and ``Credentials.refresh`` are
not documented thread-safe, and this path fires once per new account. The
lock survives cancellation: a cancelled task cannot interrupt its worker
thread, so the lock is held until that thread returns (bounded by the
request timeout) and only then does the cancellation propagate — otherwise
a shutdown that cancels a status follow-up would let the draining uploads
share the session with a request still in flight, and close it under it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .data_manager import (
    DATAMANAGER_SCOPE,
    AuthRefreshFailed,
    TransientTransportError,
    TransportResponse,
)

TOKEN_URI = "https://oauth2.googleapis.com/token"


async def _settle(future: asyncio.Future) -> None:
    """Wait for an executor future through any number of cancellations."""
    while not future.done():
        try:
            await asyncio.wait({future})
        except asyncio.CancelledError:
            continue
    if not future.cancelled():
        # Nobody else will read it; an unretrieved exception is logged at GC.
        future.exception()


class AuthorizedSessionTransport:
    def __init__(
        self, *, client_id: str, client_secret: str, refresh_token: str
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._session: Any = None
        self._lock = asyncio.Lock()

    def _get_session(self) -> Any:
        if self._session is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.credentials import Credentials

            credentials = Credentials(
                token=None,
                refresh_token=self._refresh_token,
                token_uri=TOKEN_URI,
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=[DATAMANAGER_SCOPE],
            )
            self._session = AuthorizedSession(credentials)
        return self._session

    async def post(
        self,
        url: str,
        *,
        json: dict,
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse:
        return await self._request("POST", url, json, None, headers, timeout)

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse:
        return await self._request("GET", url, None, dict(params), headers, timeout)

    async def _request(
        self,
        method: str,
        url: str,
        payload: dict | None,
        params: dict[str, str] | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse:
        async with self._lock:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                None, self._request_sync, method, url, payload, params, dict(headers), timeout
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                await _settle(future)
                raise

    def _request_sync(
        self,
        method: str,
        url: str,
        payload: dict | None,
        params: dict[str, str] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> TransportResponse:
        import requests
        from google.auth import exceptions as google_exceptions

        session = self._get_session()
        try:
            response = session.request(
                method,
                url,
                json=payload,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except google_exceptions.RefreshError as exc:
            # str(exc) can carry the token endpoint's response body; only the
            # class name travels.
            if getattr(exc, "retryable", False):
                raise TransientTransportError("RefreshError") from None
            raise AuthRefreshFailed("RefreshError") from None
        except (
            google_exceptions.TransportError,
            requests.RequestException,
            OSError,
        ) as exc:
            raise TransientTransportError(type(exc).__name__) from None
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return TransportResponse(response.status_code, dict(response.headers), body)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

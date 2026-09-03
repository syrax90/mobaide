"""``AuthorizedSessionTransport``: the serialisation lock survives a
cancelled request. The network half is replaced by a thread that blocks on
an event, so no google-auth or requests session is ever built."""
from __future__ import annotations

import asyncio
import threading

import pytest

from central_server.ads.data_manager import TransportResponse
from central_server.ads.transport import AuthorizedSessionTransport

URL = "https://datamanager.googleapis.com/v1/test"


class BlockingTransport(AuthorizedSessionTransport):
    """``_request_sync`` waits on ``release`` instead of talking to Google."""

    def __init__(self) -> None:
        super().__init__(client_id="id", client_secret="secret", refresh_token="refresh")
        self.release = threading.Event()
        self.calls: list[tuple[str, str]] = []
        self.raise_after_release: Exception | None = None

    def _request_sync(self, method, url, payload, params, headers, timeout):
        self.calls.append(("start", method))
        self.release.wait(5)
        self.calls.append(("end", method))
        if self.raise_after_release is not None:
            raise self.raise_after_release
        return TransportResponse(200, {}, {"requestId": "r", "method": method})


async def _until(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition never met"
        await asyncio.sleep(0.005)


async def test_requests_are_serialised():
    transport = BlockingTransport()
    first = asyncio.create_task(transport.get(URL, params={}, headers={}, timeout=1.0))
    second = asyncio.create_task(transport.post(URL, json={}, headers={}, timeout=1.0))
    await _until(lambda: transport.calls)
    await asyncio.sleep(0.02)
    assert transport.calls == [("start", "GET")]
    transport.release.set()
    assert (await first).body["method"] == "GET"
    assert (await second).body["method"] == "POST"
    assert transport.calls == [("start", "GET"), ("end", "GET"), ("start", "POST"), ("end", "POST")]


async def test_cancelled_request_holds_the_lock_until_its_thread_returns():
    """A shutdown cancels a status follow-up mid-GET: the pending upload must
    not start on the same session while that thread is still inside it."""
    transport = BlockingTransport()
    follow_up = asyncio.create_task(transport.get(URL, params={}, headers={}, timeout=1.0))
    await _until(lambda: transport.calls)
    follow_up.cancel()
    upload = asyncio.create_task(transport.post(URL, json={}, headers={}, timeout=1.0))
    await asyncio.sleep(0.05)

    assert transport.calls == [("start", "GET")]
    assert not follow_up.done() and not upload.done()

    transport.release.set()
    with pytest.raises(asyncio.CancelledError):
        await follow_up
    assert (await upload).body["method"] == "POST"
    assert transport.calls == [("start", "GET"), ("end", "GET"), ("start", "POST"), ("end", "POST")]


async def test_repeated_cancellation_still_waits_and_propagates_once():
    transport = BlockingTransport()
    task = asyncio.create_task(transport.get(URL, params={}, headers={}, timeout=1.0))
    await _until(lambda: transport.calls)
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0.01)
    assert not task.done()
    transport.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.calls == [("start", "GET"), ("end", "GET")]


async def test_thread_failure_after_cancellation_is_swallowed():
    """The cancelled caller gets CancelledError, never the thread's error,
    and the executor future's exception counts as retrieved."""
    transport = BlockingTransport()
    transport.raise_after_release = RuntimeError("late failure")
    task = asyncio.create_task(transport.get(URL, params={}, headers={}, timeout=1.0))
    await _until(lambda: transport.calls)
    task.cancel()
    transport.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The lock is free again: a later request goes straight through.
    transport.release.clear()
    transport.raise_after_release = None
    later = asyncio.create_task(transport.post(URL, json={}, headers={}, timeout=1.0))
    await _until(lambda: len(transport.calls) == 3)
    transport.release.set()
    assert (await later).status == 200

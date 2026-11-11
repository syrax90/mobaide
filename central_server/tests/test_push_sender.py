"""Tests for the aioapns-backed APNs sender (CLA-106).

Covers the NotificationRequest wire shape (aps alert, collapse-id ==
thread-id == session_id), the result mapping that decides token cleanup
(410 / Unregistered / BadDeviceToken), transport-error containment, lazy
per-environment client caching, and pool shutdown. The dispatcher tests
exercise everything above the ``ApnsSending`` seam with a FakeSender;
these pin the real implementation below it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from aioapns import PushType
from aioapns.common import NotificationResult

from central_server.push.sender import AioapnsSender, PushResult


class FakeApnsClient:
    def __init__(
        self,
        result: NotificationResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list = []
        self._result = result or NotificationResult("nid", "200")
        self._error = error

    async def send_notification(self, request) -> NotificationResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._result


def _sender() -> AioapnsSender:
    return AioapnsSender(
        key_content="-----BEGIN PRIVATE KEY-----\nfake\n",
        key_id="KEYID12345",
        team_id="TEAM123456",
        topic="com.mobaide.app",
    )


async def _send(sender: AioapnsSender, **overrides) -> PushResult:
    kwargs = dict(
        token="tok-1",
        title="Permission needed",
        body="Fix the bug is waiting for your approval",
        session_id="s1",
        agent_id="agent-1",
        environment="sandbox",
    )
    kwargs.update(overrides)
    return await sender.send(**kwargs)


class TestSendResultMapping:
    async def test_410_status_requests_unregister(self):
        sender = _sender()
        sender._clients["sandbox"] = FakeApnsClient(
            NotificationResult("nid", "410", description="Unregistered")
        )

        result = await _send(sender)

        assert not result.ok
        assert result.should_unregister

    @pytest.mark.parametrize("reason", ["Unregistered", "BadDeviceToken"])
    async def test_dead_token_reason_unregisters_regardless_of_status(
        self, reason
    ):
        sender = _sender()
        sender._clients["sandbox"] = FakeApnsClient(
            NotificationResult("nid", "400", description=reason)
        )

        result = await _send(sender)

        assert result.should_unregister

    async def test_transient_failure_keeps_registration(self):
        sender = _sender()
        sender._clients["sandbox"] = FakeApnsClient(
            NotificationResult("nid", "500", description="InternalServerError")
        )

        result = await _send(sender)

        assert result == PushResult(
            ok=False,
            should_unregister=False,
            status="500",
            description="InternalServerError",
        )

    async def test_transport_error_contained_as_failure(self):
        sender = _sender()
        sender._clients["sandbox"] = FakeApnsClient(
            error=ConnectionError("tls handshake failed")
        )

        result = await _send(sender)

        assert result == PushResult(ok=False, should_unregister=False)


class TestNotificationRequestShape:
    async def test_request_carries_alert_payload_and_collapse_id(self):
        sender = _sender()
        client = FakeApnsClient()
        sender._clients["sandbox"] = client

        await _send(sender)

        (request,) = client.requests
        assert request.device_token == "tok-1"
        assert request.collapse_key == "s1"
        assert request.push_type is PushType.ALERT
        assert request.message == {
            "aps": {
                "alert": {
                    "title": "Permission needed",
                    "body": "Fix the bug is waiting for your approval",
                },
                "sound": "default",
                "thread-id": "s1",
            },
            "session_id": "s1",
            "agent_id": "agent-1",
        }


class TestClientLifecycle:
    async def test_client_created_once_per_environment(self, monkeypatch):
        constructed: list[dict] = []

        class FakeAPNs:
            def __init__(self, **kwargs):
                constructed.append(kwargs)

            async def send_notification(self, request) -> NotificationResult:
                return NotificationResult("nid", "200")

        monkeypatch.setattr("aioapns.APNs", FakeAPNs)
        sender = _sender()

        await _send(sender, environment="sandbox")
        await _send(sender, environment="sandbox")
        await _send(sender, environment="production")

        assert len(constructed) == 2
        assert constructed[0]["use_sandbox"] is True
        assert constructed[1]["use_sandbox"] is False
        assert constructed[0]["key"] == "-----BEGIN PRIVATE KEY-----\nfake\n"
        assert constructed[0]["key_id"] == "KEYID12345"
        assert constructed[0]["team_id"] == "TEAM123456"
        assert constructed[0]["topic"] == "com.mobaide.app"

    async def test_close_closes_every_pool_and_survives_errors(self):
        closed: list[str] = []

        def make_client(name: str, error: Exception | None = None):
            def close() -> None:
                closed.append(name)
                if error is not None:
                    raise error

            return SimpleNamespace(pool=SimpleNamespace(close=close))

        sender = _sender()
        sender._clients["sandbox"] = make_client(
            "sandbox", RuntimeError("already closed")
        )
        sender._clients["production"] = make_client("production")

        sender.close()

        assert sorted(closed) == ["production", "sandbox"]
        assert sender._clients == {}

"""Thin APNs sender over ``aioapns``.

Isolates the aioapns dependency behind the :class:`ApnsSending` protocol so
the dispatcher (and its tests) never touch HTTP/2 or JWT signing. aioapns
caches the ES256 provider JWT internally (30-min TTL, re-signed lazily) —
never sign per-request.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

SANDBOX = "sandbox"
PRODUCTION = "production"

# APNs reasons that mean the token is permanently dead and must be dropped.
_UNREGISTERED_STATUS = "410"
_UNREGISTERED_REASONS = frozenset({"Unregistered", "BadDeviceToken"})


@dataclass(frozen=True)
class PushResult:
    ok: bool
    should_unregister: bool
    status: str = ""
    description: str | None = None


class ApnsSending(Protocol):
    async def send(
        self,
        *,
        token: str,
        title: str,
        body: str,
        session_id: str,
        agent_id: str,
        environment: str,
    ) -> PushResult: ...

    def close(self) -> None: ...


class AioapnsSender:
    """aioapns-backed sender with one lazily created client per APNs
    environment (a device row's ``push_environment`` picks sandbox vs
    production; both share the same .p8 key).

    Clients are created lazily inside ``send`` because the aioapns
    connection pool binds to the running event loop at construction.
    """

    def __init__(
        self, *, key_content: str, key_id: str, team_id: str, topic: str
    ) -> None:
        self._key_content = key_content
        self._key_id = key_id
        self._team_id = team_id
        self._topic = topic
        self._clients: dict[str, Any] = {}

    def _client(self, environment: str) -> Any:
        client = self._clients.get(environment)
        if client is None:
            from aioapns import APNs

            client = APNs(
                key=self._key_content,
                key_id=self._key_id,
                team_id=self._team_id,
                topic=self._topic,
                use_sandbox=environment == SANDBOX,
            )
            self._clients[environment] = client
        return client

    async def send(
        self,
        *,
        token: str,
        title: str,
        body: str,
        session_id: str,
        agent_id: str,
        environment: str,
    ) -> PushResult:
        from aioapns import NotificationRequest, PushType

        request = NotificationRequest(
            device_token=token,
            message={
                "aps": {
                    "alert": {"title": title, "body": body},
                    "sound": "default",
                    "thread-id": session_id,
                },
                "session_id": session_id,
                "agent_id": agent_id,
            },
            collapse_key=session_id,
            push_type=PushType.ALERT,
        )
        try:
            result = await self._client(environment).send_notification(request)
        except Exception:
            logger.warning("push.send_failed env=%s", environment, exc_info=True)
            return PushResult(ok=False, should_unregister=False)
        should_unregister = (
            result.status == _UNREGISTERED_STATUS
            or result.description in _UNREGISTERED_REASONS
        )
        return PushResult(
            ok=result.is_successful,
            should_unregister=should_unregister,
            status=result.status,
            description=result.description,
        )

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.pool.close()
            except Exception:
                logger.warning("push.pool_close_failed", exc_info=True)
        self._clients.clear()

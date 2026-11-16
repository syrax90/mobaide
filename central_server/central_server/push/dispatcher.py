"""Presence-gated APNs dispatch for the attention user-events
(``session.attention`` and ``herdr.attention``).

Presence is per device, not per account. Gating on the account instead would
let one awake client mute the notification for every other device the user
owns. Sends run in fire-and-forget tasks so a slow APNs round-trip can never
stall the agent WebSocket reader.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

from .content import attention_content
from .sender import AioapnsSender, ApnsSending, PRODUCTION, SANDBOX

if TYPE_CHECKING:
    from ..config import CentralServerConfig
    from ..db.database import Database

logger = logging.getLogger(__name__)


def _push_target(payload: dict) -> tuple[str, dict[str, str]]:
    """``(thread id, custom payload)`` for an attention payload.

    A session alert opens by ``session_id``. A herdr alert opens by server and
    pane; its thread id is a digest, because APNs limits a collapse id to 64
    bytes and pane ids have no fixed length.
    """
    agent_id = str(payload.get("agent_id", ""))
    if "pane_id" in payload:
        server_id, pane_id = str(payload["server_id"]), str(payload["pane_id"])
        digest = hashlib.sha256(
            f"{agent_id}\0{server_id}\0{pane_id}".encode()
        ).hexdigest()
        return f"herdr-{digest[:32]}", {
            "herdr_server_id": server_id,
            "herdr_pane_id": pane_id,
            "agent_id": agent_id,
        }
    session_id = str(payload["session_id"])
    return session_id, {"session_id": session_id, "agent_id": agent_id}


class PushDispatcher:
    def __init__(
        self,
        db: "Database",
        sender: ApnsSending | None,
        default_environment: str = PRODUCTION,
    ) -> None:
        self._db = db
        self._sender = sender
        self._default_environment = default_environment
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self._sender is not None

    @classmethod
    def from_config(
        cls, db: "Database", config: "CentralServerConfig"
    ) -> "PushDispatcher":
        """Build the dispatcher from APNS_* config; disabled (no errors)
        when any of the four credentials is missing."""
        default_environment = (
            SANDBOX if config.apns_use_sandbox else PRODUCTION
        )
        if not (
            config.apns_key_path
            and config.apns_key_id
            and config.apns_team_id
            and config.apns_topic
        ):
            logger.info("push.disabled reason=apns_not_configured")
            return cls(db, None, default_environment)
        key_content = Path(config.apns_key_path).read_text(encoding="utf-8")
        sender = AioapnsSender(
            key_content=key_content,
            key_id=config.apns_key_id,
            team_id=config.apns_team_id,
            topic=config.apns_topic,
        )
        logger.info(
            "push.enabled topic=%s default_env=%s",
            config.apns_topic, default_environment,
        )
        return cls(db, sender, default_environment)

    def schedule_attention(
        self,
        user_id: str,
        payload: dict,
        exclude_device_ids: Collection[str] = (),
    ) -> None:
        """Fire-and-forget an attention push fan-out for ``user_id``, skipping
        devices that already receive the event over their user-events socket."""
        if self._sender is None:
            return
        task = asyncio.create_task(
            self._send_attention(user_id, payload, frozenset(exclude_device_ids)),
            name=f"push-attention-{user_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_attention(
        self, user_id: str, payload: dict, exclude_device_ids: frozenset[str]
    ) -> None:
        assert self._sender is not None
        content = attention_content(
            payload.get("reason"),
            payload.get("title"),
            payload.get("project_name"),
            payload.get("exit_reason"),
            "herdr" if "pane_id" in payload else payload.get("kind"),
        )
        if content is None:
            return
        title, body = content
        thread_id, data = _push_target(payload)
        try:
            targets = await self._db.list_push_targets(user_id)
        except Exception:
            logger.exception("push.target_lookup_failed user_id=%s", user_id)
            return
        for row in targets:
            if row["device_id"] in exclude_device_ids:
                continue
            environment = row["push_environment"] or self._default_environment
            try:
                result = await self._sender.send(
                    token=row["push_token"],
                    title=title,
                    body=body,
                    thread_id=thread_id,
                    data=data,
                    environment=environment,
                )
            except Exception:
                logger.exception(
                    "push.send_crashed device_id=%s", row["device_id"]
                )
                continue
            if result.should_unregister:
                logger.info(
                    "push.token_unregistered device_id=%s status=%s reason=%s",
                    row["device_id"], result.status, result.description,
                )
                await self._db.clear_push_token(
                    row["device_id"], row["push_token"]
                )
            elif not result.ok:
                logger.warning(
                    "push.send_rejected device_id=%s status=%s reason=%s",
                    row["device_id"], result.status, result.description,
                )

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._sender is not None:
            self._sender.close()

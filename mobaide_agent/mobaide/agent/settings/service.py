"""The agent-wide settings: stored in ``agent_settings.json``, applied at
once, and announced with ``agent_settings.changed``."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mobaide.agent._env_writer import atomic_write

from .keep_awake import KeepAwake, detect_backend
from .models import AgentSettingsResponse, KeepAwakeStatus

logger = logging.getLogger(__name__)

EVENT_TYPE = "agent_settings.changed"
SETTINGS_FILE_NAME = "agent_settings.json"


class AgentSettingsError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


def load_settings(path: Path) -> dict[str, Any]:
    """The stored settings; a missing or corrupt file means every default."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("agent_settings.unreadable path=%s: %s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def save_setting(path: Path, key: str, value: Any) -> None:
    """Write one key and keep the others, including keys a newer agent wrote."""
    settings = load_settings(path)
    settings[key] = value
    atomic_write(path, json.dumps(settings, indent=2) + "\n")


class AgentSettingsService:
    def __init__(
        self,
        settings_file: Path,
        *,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
        keep_awake: KeepAwake | None = None,
    ) -> None:
        self._file = settings_file
        self._publish = publish
        self._keep_awake = keep_awake if keep_awake is not None else KeepAwake(*detect_backend())
        self._keep_awake.on_change = self._announce
        self._lock = asyncio.Lock()
        self._starting: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Apply the stored choice: a computer kept awake before a restart
        stays awake after it."""
        try:
            async with self._lock:
                # Read under the lock: a PUT that ran first has the last word.
                if load_settings(self._file).get("keep_awake") is not True:
                    return
                await self._keep_awake.set(True)
            self._announce()
        except Exception:
            logger.exception("agent_settings.start_failed")

    async def bridge(self) -> None:
        """For an update restart, before shutdown steps that may hang: the
        restart watchdog exits hard, with no shutdown, after 60 s."""
        await self._keep_awake.bridge()

    def start_soon(self) -> None:
        """``start`` in the background, so boot never waits for an inhibitor."""
        if self._starting is None:
            self._starting = asyncio.create_task(self.start(), name="agent-settings-start")

    async def shutdown(self, *, restarting: bool = False) -> None:
        """``restarting``: the supervisor starts the agent again at once, so
        the computer stays awake across the gap."""
        starting, self._starting = self._starting, None
        if starting is not None:
            starting.cancel()
            # wait() does not raise the task's CancelledError, so a cancel
            # aimed at this shutdown still gets through.
            await asyncio.wait({starting})
        await self._keep_awake.close(bridge=restarting)

    def status(self) -> AgentSettingsResponse:
        state, message = self._keep_awake.snapshot()
        return AgentSettingsResponse(
            keep_awake=KeepAwakeStatus(
                enabled=self._keep_awake.enabled, state=state, message=message
            )
        )

    async def set_keep_awake(self, enabled: bool) -> AgentSettingsResponse:
        """Store the choice for every client and apply it now, without a
        restart; a failure to apply is reported in the status, not raised."""
        async with self._lock:
            try:
                save_setting(self._file, "keep_awake", enabled)
            except OSError as exc:
                raise AgentSettingsError(
                    500, "storage_error", f"Cannot write agent settings: {exc.strerror or exc}"
                ) from exc
            await self._keep_awake.set(enabled)
        logger.info("agent_settings.keep_awake enabled=%s state=%s", enabled, self._keep_awake.snapshot()[0])
        self._announce()
        return self.status()

    def _announce(self) -> None:
        if self._publish is None:
            return
        try:
            self._publish(EVENT_TYPE, self.status().model_dump(mode="json"))
        except Exception:
            logger.exception("agent_settings.publish_failed")

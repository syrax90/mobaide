"""Error vocabulary of the cloud voice routes.

Every refusal answers ``{"detail": {"code", "message", ...}}``, the envelope
of the agent's voice routes, so one client decoder reads both. ``code`` is an
open vocabulary. Never 401, which clients treat as a dead session.
"""
from __future__ import annotations

from typing import Any


class CloudVoiceError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def disabled() -> CloudVoiceError:
    return CloudVoiceError(
        403, "cloud_disabled", "Cloud voice input is not available for this account."
    )


def unavailable() -> CloudVoiceError:
    return CloudVoiceError(
        503, "cloud_unavailable", "Cloud voice input is unavailable right now."
    )


def busy(message: str = "Cloud voice input is busy. Try again in a moment.") -> CloudVoiceError:
    # The agent's own code: clients retry it by themselves, with backoff.
    return CloudVoiceError(503, "voice_busy", message)


def limit_reached(resets_at: str) -> CloudVoiceError:
    return CloudVoiceError(
        429,
        "cloud_limit_reached",
        "Today's cloud voice input limit is used up.",
        resets_at=resets_at,
    )


def dictation_not_found() -> CloudVoiceError:
    return CloudVoiceError(
        404, "dictation_not_found", "This dictation expired or does not exist."
    )

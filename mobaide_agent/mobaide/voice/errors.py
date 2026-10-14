"""Voice error vocabulary shared by both transports.

Every refusal answers ``{"detail": {"code", "message", ...}}``. ``code`` is
an open vocabulary: clients treat an unknown code as a generic failure and
show ``message``. Never 401, which the mobile auth interceptors treat as an
expired token.
"""
from __future__ import annotations

from typing import Any


class VoiceError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def not_ready(message: str = "Voice input is not installed on this computer.") -> VoiceError:
    return VoiceError(409, "voice_not_ready", message)


def dictation_not_found() -> VoiceError:
    return VoiceError(404, "dictation_not_found", "This dictation expired or does not exist.")


def http_error_for(exc: BaseException) -> tuple[int, dict[str, Any]] | None:
    """Map a voice exception to ``(status, body)``; ``None`` if unrelated."""
    if isinstance(exc, VoiceError):
        return exc.status, {"detail": exc.detail()}
    return None

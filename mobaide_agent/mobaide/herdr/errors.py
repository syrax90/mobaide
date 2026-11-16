"""Herdr error vocabulary shared by both transports.

Every refusal answers ``{"detail": {"code", "message"}}``. ``code`` is an open
vocabulary: clients treat an unknown code as a generic failure and show
``message``. Never 401, which the mobile auth interceptors treat as an expired
token.
"""
from __future__ import annotations

from typing import Any


class HerdrError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


def unavailable() -> HerdrError:
    return HerdrError(503, "herdr_unavailable", "Herdr support is not available on this agent.")


def server_not_found() -> HerdrError:
    return HerdrError(404, "server_not_found", "This Herdr server is not running any more.")


def pane_not_found() -> HerdrError:
    return HerdrError(404, "pane_not_found", "This pane is closed.")


def not_supported(what: str) -> HerdrError:
    return HerdrError(409, "not_supported", f"This Herdr server cannot {what}.")


def server_unreachable(reason: str = "") -> HerdrError:
    message = "The Herdr server did not answer."
    if reason:
        message = f"{message} {reason}"
    return HerdrError(502, "server_unreachable", message)


def upstream(message: str) -> HerdrError:
    """A refusal from herdr itself, in herdr's own words."""
    return HerdrError(409, "herdr_refused", message or "Herdr refused the request.")


def busy() -> HerdrError:
    return HerdrError(503, "herdr_busy", "Too many Herdr requests are in flight. Try again.")

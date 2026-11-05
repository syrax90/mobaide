"""Tests for the SMTP boundary of :class:`EmailSender`.

``aiosmtplib.send`` is swapped for a recorder, so nothing touches a real
SMTP server. The point is the envelope: a stored address must reach exactly
one mailbox, whatever the ``To`` header would parse into.
"""
from __future__ import annotations

import logging
from email.message import EmailMessage
from typing import Any

import pytest

from central_server.auth.email import EmailSender
from central_server.config import CentralServerConfig
from tests.conftest import TEST_JWT_SECRET


class _RecordingSmtp:
    """Stands in for ``aiosmtplib.send`` and keeps every call."""

    def __init__(self) -> None:
        self.sent: list[tuple[EmailMessage, dict[str, Any]]] = []

    async def __call__(self, message: EmailMessage, **kwargs: Any) -> None:
        self.sent.append((message, kwargs))


@pytest.fixture
def smtp(monkeypatch) -> _RecordingSmtp:
    fake = _RecordingSmtp()
    monkeypatch.setattr("central_server.auth.email.aiosmtplib.send", fake)
    return fake


@pytest.fixture
def sender() -> EmailSender:
    return EmailSender(CentralServerConfig(jwt_secret_key=TEST_JWT_SECRET))


class TestRecipientGuard:
    async def test_single_address_is_the_only_envelope_recipient(
        self, sender: EmailSender, smtp: _RecordingSmtp
    ):
        await sender.send_verification_email("alice@example.com", "123456", 15)

        assert len(smtp.sent) == 1
        message, kwargs = smtp.sent[0]
        assert kwargs["recipients"] == ["alice@example.com"]
        assert message["To"] == "alice@example.com"

    @pytest.mark.parametrize(
        "stored",
        [
            "a@example.com, b@example.com",
            "Alice <a@example.com>",
            "a@example.com\r\nBcc: b@example.com",
        ],
    )
    async def test_legacy_multi_recipient_row_is_refused(
        self, sender: EmailSender, smtp: _RecordingSmtp, caplog, stored
    ):
        with caplog.at_level(logging.WARNING, logger="central_server.auth.email"):
            await sender.send_verification_email(stored, "123456", 15)

        assert smtp.sent == []
        assert "email_verification.refused_recipient" in caplog.messages

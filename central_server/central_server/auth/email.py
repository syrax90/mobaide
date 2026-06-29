"""Outbound email helper.

Thin wrapper around ``aiosmtplib`` + Jinja2 for rendering and sending all
transactional email (verification, password reset, password-changed
notification). Jinja templates live at ``central_server/templates/email/``.

Errors from the SMTP transport are logged but never re-raised — this class is
invoked from within ``fastapi.BackgroundTasks`` where exceptions would
otherwise disappear silently. Users can always press "resend" if no email
arrives.
"""
from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import TYPE_CHECKING, Protocol

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..templates_dir import resolve_templates_dir

if TYPE_CHECKING:
    from ..config import CentralServerConfig

logger = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader(str(resolve_templates_dir())),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=False,
)


class EmailSenderLike(Protocol):
    """Structural interface ``AuthService`` depends on for outbound email.

    Both the SMTP-backed :class:`EmailSender` and the in-process
    :class:`TestEmailSink` satisfy it; :func:`build_email_sender` selects the
    implementation per environment.
    """

    async def send_verification_email(
        self, to: str, code: str, ttl_minutes: int
    ) -> None: ...

    async def send_password_reset_email(
        self,
        to: str,
        code: str,
        ttl_minutes: int,
        client_ip: str | None = None,
    ) -> None: ...

    async def send_password_changed_notification(
        self, to: str, revoked_session_count: int
    ) -> None: ...

    async def send_account_deletion_requested(
        self,
        to: str,
        scheduled_for_iso: str,
        grace_days: int,
        cancel_url: str,
    ) -> None: ...

    async def send_account_deletion_completed(self, to: str) -> None: ...


class EmailSender:
    """Sends transactional email over SMTP using the configured credentials."""

    def __init__(self, config: CentralServerConfig) -> None:
        self._config = config

    async def _send_email(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
        log_prefix: str,
    ) -> None:
        """Build a multipart message and dispatch it via aiosmtplib.

        Caller is responsible for rendering ``html_body`` / ``text_body``
        (and catching template-render errors with a ``{log_prefix}.render_failed``
        log entry). This helper only handles message assembly + transport.
        Never raises — SMTP / TLS / auth errors are logged at WARNING and
        swallowed so they cannot leak out of a FastAPI BackgroundTask. Never
        logs message bodies or OTP codes — only the recipient and the SMTP host.
        """
        cfg = self._config
        message = EmailMessage()
        message["From"] = f"{cfg.smtp_from_name} <{cfg.smtp_from_address}>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        tls_context = ssl.create_default_context() if cfg.smtp_use_tls else None

        try:
            await aiosmtplib.send(
                message,
                hostname=cfg.smtp_host,
                port=cfg.smtp_port,
                username=cfg.smtp_username,
                password=cfg.smtp_password,
                use_tls=cfg.smtp_use_tls,
                start_tls=cfg.smtp_starttls,
                tls_context=tls_context,
            )
        except Exception as exc:
            logger.warning(
                "%s.send_failed to=%s host=%s:%s error=%s",
                log_prefix,
                to,
                cfg.smtp_host,
                cfg.smtp_port,
                type(exc).__name__,
            )
            return

        logger.info(
            "%s.sent to=%s host=%s:%s",
            log_prefix,
            to,
            cfg.smtp_host,
            cfg.smtp_port,
        )

    async def send_verification_email(
        self,
        to: str,
        code: str,
        ttl_minutes: int,
    ) -> None:
        """Render and send the email verification OTP. Never raises."""
        cfg = self._config
        context = {
            "code": code,
            "ttl_minutes": ttl_minutes,
            "from_name": cfg.smtp_from_name,
            "from_domain": cfg.smtp_from_domain,
        }
        try:
            html_body = _env.get_template("email/verify_email.html").render(**context)
            text_body = _env.get_template("email/verify_email.txt").render(**context)
        except Exception as exc:
            logger.exception("email_verification.render_failed: %s", exc)
            return

        await self._send_email(
            to=to,
            subject="Your verification code",
            html_body=html_body,
            text_body=text_body,
            log_prefix="email_verification",
        )

    async def send_password_reset_email(
        self,
        to: str,
        code: str,
        ttl_minutes: int,
        client_ip: str | None = None,
    ) -> None:
        """Render and send the password-reset OTP. Never raises."""
        cfg = self._config
        context = {
            "code": code,
            "ttl_minutes": ttl_minutes,
            "client_ip": client_ip or "unknown",
            "from_name": cfg.smtp_from_name,
            "from_domain": cfg.smtp_from_domain,
        }
        try:
            html_body = _env.get_template("email/reset_password.html").render(**context)
            text_body = _env.get_template("email/reset_password.txt").render(**context)
        except Exception as exc:
            logger.exception("password_reset.render_failed: %s", exc)
            return

        await self._send_email(
            to=to,
            subject=f"Reset your {cfg.smtp_from_name} password",
            html_body=html_body,
            text_body=text_body,
            log_prefix="password_reset",
        )

    async def send_password_changed_notification(
        self,
        to: str,
        revoked_session_count: int,
    ) -> None:
        """Render and send the post-reset notification. Never raises."""
        cfg = self._config
        context = {
            "revoked_session_count": revoked_session_count,
            "from_name": cfg.smtp_from_name,
            "from_domain": cfg.smtp_from_domain,
        }
        try:
            html_body = _env.get_template("email/password_changed.html").render(**context)
            text_body = _env.get_template("email/password_changed.txt").render(**context)
        except Exception as exc:
            logger.exception("password_changed.render_failed: %s", exc)
            return

        await self._send_email(
            to=to,
            subject=f"Your {cfg.smtp_from_name} password was changed",
            html_body=html_body,
            text_body=text_body,
            log_prefix="password_changed",
        )

    async def send_account_deletion_requested(
        self,
        to: str,
        scheduled_for_iso: str,
        grace_days: int,
        cancel_url: str,
    ) -> None:
        """Render and send the account-deletion-scheduled email. Never raises."""
        cfg = self._config
        context = {
            "scheduled_for": scheduled_for_iso,
            "grace_days": grace_days,
            "cancel_url": cancel_url,
            "from_name": cfg.smtp_from_name,
            "from_domain": cfg.smtp_from_domain,
        }
        try:
            html_body = _env.get_template(
                "email/account_deletion_requested.html"
            ).render(**context)
            text_body = _env.get_template(
                "email/account_deletion_requested.txt"
            ).render(**context)
        except Exception as exc:
            logger.exception("account_deletion_requested.render_failed: %s", exc)
            return

        await self._send_email(
            to=to,
            subject=f"Your {cfg.smtp_from_name} account is scheduled for deletion",
            html_body=html_body,
            text_body=text_body,
            log_prefix="account_deletion_requested",
        )

    async def send_account_deletion_completed(self, to: str) -> None:
        """Render and send the final-purge confirmation email. Never raises."""
        cfg = self._config
        context = {
            "from_name": cfg.smtp_from_name,
            "from_domain": cfg.smtp_from_domain,
        }
        try:
            html_body = _env.get_template(
                "email/account_deletion_completed.html"
            ).render(**context)
            text_body = _env.get_template(
                "email/account_deletion_completed.txt"
            ).render(**context)
        except Exception as exc:
            logger.exception("account_deletion_completed.render_failed: %s", exc)
            return

        await self._send_email(
            to=to,
            subject=f"Your {cfg.smtp_from_name} account has been deleted",
            html_body=html_body,
            text_body=text_body,
            log_prefix="account_deletion_completed",
        )


@dataclass(frozen=True)
class CapturedEmail:
    """A single email captured by ``TestEmailSink`` instead of being sent.

    ``kind`` names the template ("verification", "password_reset", …);
    ``params`` carries the call's remaining arguments by name (e.g.
    ``{"code": "123456", "ttl_minutes": 15}``) so tests and the E2E harness can
    assert on the OTP without any SMTP round-trip.
    """

    kind: str
    to: str
    params: dict[str, object]


class TestEmailSink:
    """In-process, network-free email sender for ``MOBAIDE_ENV=test``.

    Duck-typed drop-in for :class:`EmailSender`: exposes the same five async
    ``send_*`` methods ``AuthService`` calls, but appends each call to
    ``messages`` instead of opening an SMTP connection — no SMTP connection is
    ever attempted. Surfaced via ``app.state.email_sender`` so tests and the
    headless E2E client can read delivered mail (notably the verification
    code). ``config`` is accepted for symmetric construction with
    ``EmailSender`` and is otherwise unused.
    """

    # Production class, not a test case — keep pytest from collecting it
    # (its ``Test`` name prefix otherwise trips PytestCollectionWarning).
    __test__ = False

    def __init__(self, config: CentralServerConfig | None = None) -> None:
        self._config = config
        self.messages: list[CapturedEmail] = []

    async def send_verification_email(
        self, to: str, code: str, ttl_minutes: int
    ) -> None:
        self.messages.append(
            CapturedEmail(
                "verification", to, {"code": code, "ttl_minutes": ttl_minutes}
            )
        )

    async def send_password_reset_email(
        self,
        to: str,
        code: str,
        ttl_minutes: int,
        client_ip: str | None = None,
    ) -> None:
        self.messages.append(
            CapturedEmail(
                "password_reset",
                to,
                {
                    "code": code,
                    "ttl_minutes": ttl_minutes,
                    "client_ip": client_ip,
                },
            )
        )

    async def send_password_changed_notification(
        self, to: str, revoked_session_count: int
    ) -> None:
        self.messages.append(
            CapturedEmail(
                "password_changed",
                to,
                {"revoked_session_count": revoked_session_count},
            )
        )

    async def send_account_deletion_requested(
        self,
        to: str,
        scheduled_for_iso: str,
        grace_days: int,
        cancel_url: str,
    ) -> None:
        self.messages.append(
            CapturedEmail(
                "account_deletion_requested",
                to,
                {
                    "scheduled_for_iso": scheduled_for_iso,
                    "grace_days": grace_days,
                    "cancel_url": cancel_url,
                },
            )
        )

    async def send_account_deletion_completed(self, to: str) -> None:
        self.messages.append(
            CapturedEmail("account_deletion_completed", to, {})
        )


def build_email_sender(
    config: CentralServerConfig,
) -> EmailSenderLike:
    """Return the email sender for this environment.

    ``MOBAIDE_ENV=test`` selects the in-process :class:`TestEmailSink` (no SMTP
    is ever attempted); every other environment uses the real SMTP-backed
    :class:`EmailSender`. This is the single seam shared by ``create_app()``'s
    lifespan and the tests.
    """
    if config.is_test_mode:
        return TestEmailSink(config)
    return EmailSender(config)

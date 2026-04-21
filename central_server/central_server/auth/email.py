"""Outbound email helper.

Thin wrapper around ``aiosmtplib`` + Jinja2 for rendering and sending the
verification email. Kept deliberately small: a single ``send_verification_email``
method, Jinja templates co-located with the dashboard HTML at
``central_server/templates/email/``.

Errors from the SMTP transport are logged but never re-raised — this class is
invoked from within ``fastapi.BackgroundTasks`` where exceptions would
otherwise disappear silently. Users can always press "resend" if no email
arrives.
"""
from __future__ import annotations

import logging
import ssl
from email.message import EmailMessage
from typing import TYPE_CHECKING

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..templates_dir import resolve_templates_dir

if TYPE_CHECKING:
    from ..config import CentralServerConfig

logger = logging.getLogger(__name__)

# Templates co-located with the dashboard HTML under `central_server/templates/`.
_env = Environment(
    loader=FileSystemLoader(str(resolve_templates_dir())),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=False,
)


class EmailSender:
    """Sends transactional email over SMTP using the configured credentials."""

    def __init__(self, config: CentralServerConfig) -> None:
        self._config = config

    async def send_verification_email(
        self,
        to: str,
        code: str,
        ttl_minutes: int,
    ) -> None:
        """Render and send the email verification OTP.

        Never raises — connection and auth errors are logged and swallowed so
        they don't leak into a FastAPI background task.
        """
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

        message = EmailMessage()
        message["From"] = f"{cfg.smtp_from_name} <{cfg.smtp_from_address}>"
        message["To"] = to
        message["Subject"] = "Your verification code"
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
            # Structured log so ops can detect SMTP outages. Never include the
            # plaintext code — only the recipient.
            logger.warning(
                "email_verification.send_failed to=%s host=%s:%s error=%s",
                to,
                cfg.smtp_host,
                cfg.smtp_port,
                type(exc).__name__,
            )
            return

        logger.info(
            "email_verification.sent to=%s host=%s:%s",
            to,
            cfg.smtp_host,
            cfg.smtp_port,
        )

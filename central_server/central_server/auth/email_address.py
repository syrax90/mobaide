"""Single-mailbox check for addresses that end up in an outbound ``To``.

Not an RFC 5322 parser: a stored address must name exactly one mailbox, so
the header never expands into a list, display-name form, group, or injected
header line. Quoted local parts and address literals are refused, not parsed.
"""
from __future__ import annotations

import re

MAX_EMAIL_LENGTH = 254

# Everything an address-list or header parser treats as structure: list and
# group separators, display-name brackets, comments, quotes, escapes, address
# literals, whitespace, and control characters.
_FORBIDDEN = r'\s,;:<>()\[\]\\"\x00-\x1f\x7f'
_MAILBOX_RE = re.compile(
    rf"[^@{_FORBIDDEN}]+@[^@.{_FORBIDDEN}]+(?:\.[^@.{_FORBIDDEN}]+)+"
)

INVALID_EMAIL_MESSAGE = "Enter a single valid email address"


def is_single_mailbox(value: str) -> bool:
    """Whether ``value`` is one plain ``local@domain.tld`` address."""
    return len(value) <= MAX_EMAIL_LENGTH and _MAILBOX_RE.fullmatch(value) is not None


def validate_single_mailbox(value: str) -> str:
    """Pydantic ``AfterValidator`` form of :func:`is_single_mailbox`."""
    if not is_single_mailbox(value):
        raise ValueError(INVALID_EMAIL_MESSAGE)
    return value

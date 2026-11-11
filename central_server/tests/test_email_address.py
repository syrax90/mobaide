"""Tests for the single-mailbox check that guards every outbound ``To``.

The check is the only thing standing between a caller-chosen address and an
SMTP header, so the cases below are the shapes a header parser would expand
into more than one recipient (or into a second header line).
"""
from __future__ import annotations

import pytest

from central_server.auth.email_address import is_single_mailbox

# Every label stays within the DNS 63-octet limit, so only the total length
# decides the outcome.
_LONG_HEAD = "user@" + ("a" * 63 + ".") * 3


class TestIsSingleMailbox:
    @pytest.mark.parametrize(
        "address",
        [
            "alice@example.com",
            "first.last+tag@mail.example.co.uk",
            "o'brien_99@example.io",
        ],
    )
    def test_accepts_one_plain_address(self, address):
        assert is_single_mailbox(address)

    @pytest.mark.parametrize(
        "address",
        [
            "a@example.com, b@example.com",
            "a@example.com;b@example.com",
            "Alice <a@example.com>",
            "<a@example.com>",
            "friends: a@example.com, b@example.com;",
            '"a,b"@example.com',
            "a\\,b@example.com",
            "a@[192.168.0.1]",
            "a@example.com (Alice)",
            "a b@example.com",
            "a@example.com\r\nBcc: b@example.com",
            "a@example.com\nb@example.com",
            "a\x00@example.com",
            "a\x7f@example.com",
        ],
    )
    def test_rejects_anything_a_header_could_expand(self, address):
        assert not is_single_mailbox(address)

    @pytest.mark.parametrize(
        "address",
        [
            "",
            "alice",
            "@example.com",
            "alice@",
            "alice@localhost",
            "alice@.example.com",
            "alice@example..com",
            "alice@example.com.",
            "a@b@example.com",
        ],
    )
    def test_rejects_a_malformed_address(self, address):
        assert not is_single_mailbox(address)

    def test_length_limit_is_254_characters(self):
        at_limit = _LONG_HEAD + "b" * 53 + ".com"
        over_limit = _LONG_HEAD + "b" * 54 + ".com"
        assert len(at_limit) == 254
        assert len(over_limit) == 255

        assert is_single_mailbox(at_limit)
        assert not is_single_mailbox(over_limit)



"""Pure-function tests for ``central_server.pairing.device_code``.

No FastAPI / DB dependencies, so these are plain ``def`` tests that take
nothing from ``conftest.py`` — mirroring ``test_qr_signer.py``.
"""
from __future__ import annotations

import hashlib
import hmac

from central_server.pairing.device_code import (
    CODE_ALPHABET,
    CODE_LENGTH,
    format_code,
    generate_code,
    hash_device_code,
    mask_email,
    normalize_code,
)


class TestGenerateCode:
    def test_uses_only_the_confusable_free_alphabet(self):
        # Every generated code must survive a retype, so the alphabet is
        # the whole point: no vowels (cannot spell a word), no 0/O, 1/I/l.
        for _ in range(200):
            code = generate_code()
            assert len(code) == CODE_LENGTH
            assert set(code) <= set(CODE_ALPHABET)

    def test_output_is_already_normalised(self):
        # The mint hashes ``generate_code()`` directly and only then
        # applies display grouping; if the two disagreed, a correctly
        # retyped code would hash to a row that does not exist.
        code = generate_code()

        assert normalize_code(format_code(code)) == code


class TestNormalizeCode:
    def test_accepts_the_displayed_grouping(self):
        assert normalize_code("WDJB-MJHT") == "WDJBMJHT"

    def test_upper_cases_operator_input(self):
        assert normalize_code("wdjb-mjht") == "WDJBMJHT"

    def test_drops_spaces_a_retyped_code_picks_up(self):
        assert normalize_code("  wdjb mjht ") == "WDJBMJHT"

    def test_accepts_the_trailing_newline_a_paste_drags_along(self):
        # The reason this is a charset filter and not an anchored regex:
        # Python's ``$`` matches *before* a trailing newline, so a regex
        # would accept this too but reject "WDJBMJHT\n\n" — the filter
        # accepts both and the clients accept neither ambiguity.
        assert normalize_code("WDJBMJHT\n") == "WDJBMJHT"
        assert normalize_code("WDJB-MJHT\r\n") == "WDJBMJHT"

    def test_rejects_a_code_that_is_the_wrong_length(self):
        assert normalize_code("WDJBMJH") is None
        assert normalize_code("WDJBMJHTV") is None
        assert normalize_code("") is None

    def test_rejects_characters_outside_the_alphabet(self):
        # Filtering shortens the string rather than failing loudly, so a
        # digit or a vowel has to fall out through the length check —
        # otherwise "WDJBMJH0" would silently normalise to 7 characters
        # and be treated as a different (shorter) code.
        assert normalize_code("WDJB-MJH0") is None
        assert normalize_code("AEIOU-AEIOU") is None
        assert normalize_code("wdjbmjht!") == "WDJBMJHT"


class TestFormatCode:
    def test_groups_into_two_blocks_of_four(self):
        assert format_code("WDJBMJHT") == "WDJB-MJHT"


class TestHashDeviceCode:
    def test_matches_a_peppered_hmac_of_the_domain_separated_code(self):
        # Pin the construction: the pepper is the HMAC *key* and the code
        # is domain-separated, mirroring ``AuthService._hash_code``.
        expected = hmac.new(
            b"pepper",
            b"device_code:WDJBMJHT",
            hashlib.sha256,
        ).hexdigest()

        assert hash_device_code("pepper", "WDJBMJHT") == expected

    def test_pepper_actually_participates(self):
        # A 34.6-bit secret is brute-forceable from a database dump alone,
        # so a digest that ignored the pepper would be no protection.
        a = hash_device_code("secret-a", "WDJBMJHT")
        b = hash_device_code("secret-b", "WDJBMJHT")

        assert a != b
        assert a != hashlib.sha256(b"WDJBMJHT").hexdigest()

    def test_is_stable_across_calls(self):
        # The redeem looks a row up by this value; a salted-per-call digest
        # would make every code unredeemable.
        assert hash_device_code("p", "WDJBMJHT") == hash_device_code(
            "p", "WDJBMJHT"
        )

    def test_distinguishes_codes(self):
        assert hash_device_code("p", "WDJBMJHT") != hash_device_code(
            "p", "WDJBMJHV"
        )


class TestMaskEmail:
    def test_keeps_the_shape_its_owner_recognises(self):
        assert mask_email("pavel@gmail.com") == "p***l@g***.com"

    def test_never_leaks_the_whole_local_part(self):
        # This string is shown to whoever redeemed a code — someone who is
        # not necessarily the account holder.
        masked = mask_email("pavel@gmail.com")

        assert "pavel" not in masked
        assert "gmail" not in masked

    def test_collapses_a_local_part_too_short_to_mask(self):
        # First-and-last of a 2-character local part would be the whole
        # thing, so it collapses instead.
        assert mask_email("ab@x.io") == "***@x***.io"

    def test_masks_a_domain_with_no_dot(self):
        assert mask_email("a@localhost") == "***@l***t"

    def test_keeps_only_the_first_label_of_a_multi_label_domain(self):
        assert mask_email("x@y.co.uk") == "***@y***.co.uk"

    def test_returns_a_mask_for_an_address_with_no_at_sign(self):
        # The router calls this with ``(owner or {}).get("email", "")`` —
        # an empty string must not raise or echo back as an empty hint.
        assert mask_email("") == "***"
        assert mask_email("not-an-email") == "n***l"

    def test_a_control_character_in_the_domain_tail_becomes_a_question_mark(self):
        # Printed straight above the agent's [y/N] prompt, and the source
        # address is attacker-registerable — a raw CR must not survive to
        # repaint the line.
        assert mask_email("pavel@x.a\rb") == "p***l@x***.a?b"

    def test_truncates_an_over_long_domain_tail(self):
        long_tail = "a" * 40

        masked = mask_email(f"pavel@x.{long_tail}")

        assert masked == f"p***l@x***.{'a' * 32}…"

    def test_splits_on_the_last_at_sign_when_the_local_part_has_one(self):
        # ``local`` is collapsed to first+last either way, but the wrong
        # split (first ``@``) would hand the domain half a completely
        # different string. This pins that the account's real domain is
        # still what comes out.
        assert mask_email("pa@vel@gmail.com") == "p***l@g***.com"

"""Unit tests for ``claude_mobile.agent.auth.jwt_service.JwtService``.

These are internals tests. HTTP route coverage for auth lives in
``tests/test_auth.py`` — do NOT duplicate it here.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from claude_mobile.agent.auth import jwt_service as jwt_service_module
from claude_mobile.agent.auth.jwt_service import JwtService, TokenPayload

SECRET = "unit-test-secret-do-not-use-in-prod"


class TestRoundTrip:
    def test_access_token_round_trip(self):
        svc = JwtService(SECRET, access_ttl_minutes=30)
        token = svc.create_access_token("alice")

        payload = svc.decode_token(token)

        assert isinstance(payload, TokenPayload)
        assert payload.sub == "alice"
        assert payload.token_type == "access"
        assert isinstance(payload.exp, datetime)
        assert isinstance(payload.iat, datetime)

    def test_refresh_token_round_trip(self):
        svc = JwtService(SECRET)
        token = svc.create_refresh_token("bob")

        payload = svc.decode_token(token)

        assert payload.sub == "bob"
        assert payload.token_type == "refresh"

    def test_exp_and_iat_are_utc_aware(self):
        svc = JwtService(SECRET, access_ttl_minutes=30)
        token = svc.create_access_token("alice")

        payload = svc.decode_token(token)

        assert payload.exp is not None
        assert payload.exp.tzinfo is not None
        assert payload.iat.tzinfo is not None
        # Both should be UTC (offset zero)
        assert payload.exp.utcoffset() == timedelta(0)
        assert payload.iat.utcoffset() == timedelta(0)

    def test_default_ttl_issues_non_expiring_token(self):
        """Default TTL (0) means no `exp` claim — token never expires."""
        svc = JwtService(SECRET)
        token = svc.create_access_token("alice")

        payload = svc.decode_token(token)

        assert payload.exp is None


class TestTtlBehavior:
    def test_access_ttl_honors_constructor_arg(self):
        svc = JwtService(SECRET, access_ttl_minutes=5)
        token = svc.create_access_token("alice")

        payload = svc.decode_token(token)

        assert payload.exp is not None
        delta = payload.exp - payload.iat
        # Source calls datetime.now() exactly once per create_*_token, so exp - iat
        # should be the exact TTL.
        assert delta == timedelta(minutes=5)

    def test_refresh_ttl_honors_constructor_arg(self):
        svc = JwtService(SECRET, refresh_ttl_minutes=120)
        token = svc.create_refresh_token("alice")

        payload = svc.decode_token(token)

        assert payload.exp is not None
        assert payload.exp - payload.iat == timedelta(minutes=120)

    def test_refresh_ttl_longer_than_access_ttl_when_both_set(self):
        svc = JwtService(SECRET, access_ttl_minutes=30, refresh_ttl_minutes=1440)

        access_payload = svc.decode_token(svc.create_access_token("alice"))
        refresh_payload = svc.decode_token(svc.create_refresh_token("alice"))

        assert access_payload.exp is not None
        assert refresh_payload.exp is not None
        access_delta = access_payload.exp - access_payload.iat
        refresh_delta = refresh_payload.exp - refresh_payload.iat
        assert refresh_delta > access_delta

    def test_ttl_zero_omits_exp_claim(self):
        """access_ttl_minutes=0 and refresh_ttl_minutes=0 → no `exp` claim."""
        svc = JwtService(SECRET, access_ttl_minutes=0, refresh_ttl_minutes=0)

        access_payload = svc.decode_token(svc.create_access_token("alice"))
        refresh_payload = svc.decode_token(svc.create_refresh_token("alice"))

        assert access_payload.exp is None
        assert refresh_payload.exp is None


class TestExpiredToken:
    def test_expired_access_token_raises_expired_signature_error(self, monkeypatch):
        """Patch the module-level ``datetime`` symbol so token is minted in the past."""
        svc = JwtService(SECRET, access_ttl_minutes=1)

        real_datetime = jwt_service_module.datetime
        past = real_datetime.now(UTC) - timedelta(hours=2)

        class FakeDatetime:
            @staticmethod
            def now(tz=None):
                del tz  # tz is accepted for signature-compat with datetime.now
                return past

            # Preserve everything else the module might call on datetime.
            def __getattr__(self, item):  # pragma: no cover - passthrough shim
                return getattr(real_datetime, item)

        monkeypatch.setattr(jwt_service_module, "datetime", FakeDatetime())
        token = svc.create_access_token("alice")
        monkeypatch.undo()

        with pytest.raises(jwt.ExpiredSignatureError):
            svc.decode_token(token)

    def test_expired_token_via_manual_encode(self):
        """Independent expiry test: forge an expired token directly via jwt.encode."""
        svc = JwtService(SECRET)
        past = datetime.now(UTC) - timedelta(hours=1)
        payload = {
            "sub": "alice",
            "type": "access",
            "iat": past - timedelta(minutes=30),
            "exp": past,
        }
        token = jwt.encode(payload, SECRET, algorithm="HS256")

        with pytest.raises(jwt.ExpiredSignatureError):
            svc.decode_token(token)


class TestInvalidToken:
    def test_wrong_secret_raises_invalid_signature(self):
        svc_a = JwtService("secret-A")
        svc_b = JwtService("secret-B")

        token = svc_a.create_access_token("alice")

        with pytest.raises(jwt.InvalidSignatureError):
            svc_b.decode_token(token)

    def test_malformed_token_raises_invalid_token_error(self):
        svc = JwtService(SECRET)

        with pytest.raises(jwt.InvalidTokenError):
            svc.decode_token("not.a.token")

    def test_empty_string_raises_invalid_token_error(self):
        svc = JwtService(SECRET)

        with pytest.raises(jwt.InvalidTokenError):
            svc.decode_token("")


class TestAlgorithm:
    def test_custom_algorithm_hs512_round_trip(self):
        svc = JwtService(SECRET, algorithm="HS512")

        token = svc.create_access_token("alice")
        payload = svc.decode_token(token)

        assert payload.sub == "alice"
        assert payload.token_type == "access"

    def test_token_minted_with_hs256_cannot_be_decoded_as_hs512(self):
        signer = JwtService(SECRET, algorithm="HS256")
        verifier = JwtService(SECRET, algorithm="HS512")

        token = signer.create_access_token("alice")

        with pytest.raises(jwt.InvalidTokenError):
            verifier.decode_token(token)


class TestSubPreservation:
    @pytest.mark.parametrize(
        "username",
        [
            "alice",
            "user@example.com",
            "a/b",
            "user with spaces",
            "café",  # unicode
            "\u4f60\u597d",  # CJK: "hello"
        ],
    )
    def test_username_preserved_through_round_trip(self, username):
        svc = JwtService(SECRET)

        token = svc.create_access_token(username)
        payload = svc.decode_token(token)

        assert payload.sub == username

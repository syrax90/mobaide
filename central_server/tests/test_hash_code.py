"""Unit tests for :meth:`AuthService._hash_code`.

The helper HMAC-SHA256s the verification code with ``jwt_secret_key`` as
pepper. These properties are security-relevant: a DB-only leak must not
let an attacker recover the 6-digit code, and two users can't share a
hash for the same code.

The helper is pure (no DB / IO), so we build a minimally-valid
``AuthService`` with ``db=None`` — the ``_hash_code`` path never touches it.
"""
from __future__ import annotations

import hmac
import hashlib

from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from tests.conftest import TEST_JWT_SECRET


def _make_service(secret: str = TEST_JWT_SECRET) -> AuthService:
    config = CentralServerConfig(jwt_secret_key=secret)
    # ``_hash_code`` doesn't touch the DB, so ``None`` is fine here.
    return AuthService(config, db=None)  # type: ignore[arg-type]


class TestHashCodePepper:
    def test_different_secrets_produce_different_hashes(self):
        # Core pepper invariant: without the server secret, a DB-only
        # leak of ``token_hash`` is useless — the same code hashes
        # differently under a different key.
        svc_a = _make_service("secret-a" + "x" * 32)
        svc_b = _make_service("secret-b" + "x" * 32)
        assert svc_a._hash_code("user-1", "123456") != svc_b._hash_code(  # noqa: SLF001
            "user-1", "123456"
        )

    def test_matches_explicit_hmac_sha256_construction(self):
        # Regression fence: if somebody swaps the primitive (e.g. to plain
        # ``sha256`` or a different key input), this snapshot breaks.
        svc = _make_service("fixed-secret-for-test")
        expected = hmac.new(
            b"fixed-secret-for-test",
            b"user-42:987654",
            hashlib.sha256,
        ).hexdigest()
        assert svc._hash_code("user-42", "987654") == expected  # noqa: SLF001


class TestHashCodeUserBinding:
    def test_same_code_different_user_ids_produces_different_hashes(self):
        # A user should never be able to pass another user's stored hash
        # via their own code — the user_id must be part of the HMAC input.
        svc = _make_service()
        a = svc._hash_code("user-a", "123456")  # noqa: SLF001
        b = svc._hash_code("user-b", "123456")  # noqa: SLF001
        assert a != b

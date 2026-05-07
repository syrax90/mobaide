"""Tests for the QR pairing helpers on ``central_server.db.database``.

Targets:

* ``create_pairing_token(agent_id=None, agent_metadata=...)`` accepts the
  new optional shape.
* ``lookup_pairing_token_metadata`` returns the pre-claim row.
* ``claim_pairing_nonce`` is a CAS — exactly one of two concurrent
  claims wins.
* ``read_and_clear_claimed_credentials`` is single-flight.
* ``transfer_agent_owner`` cascades into ``mobile_refresh_tokens`` and
  ``pairings``.
* The schema migration left ``pairing_tokens.agent_id`` nullable and
  added the three new columns.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from central_server.db.database import Database, _hash_token


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "qr_pairing.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def owner_id(db: Database) -> str:
    return await db.create_user(email="owner@test.local", password_hash="h")


class TestSchema:
    async def test_pairing_tokens_agent_id_is_nullable(self, db: Database):
        # Migration v6 -> v7 must relax NOT NULL on pairing_tokens.agent_id.
        cur = await db.conn.execute("PRAGMA table_info(pairing_tokens)")
        rows = await cur.fetchall()
        cols = {r[1]: r for r in rows}

        assert "agent_id" in cols
        # PRAGMA table_info: column 3 = notnull (1 = NOT NULL, 0 = nullable)
        assert cols["agent_id"][3] == 0, (
            "pairing_tokens.agent_id must be nullable for QR-pairing pre-claim rows"
        )

    async def test_new_columns_exist(self, db: Database):
        cur = await db.conn.execute("PRAGMA table_info(pairing_tokens)")
        cols = {r[1] for r in await cur.fetchall()}

        for required in ("claimed_token_raw", "claimed_agent_id", "agent_metadata"):
            assert required in cols, f"missing column: {required}"


class TestCreatePairingToken:
    async def test_accepts_no_agent_id_for_qr_flow(self, db: Database):
        raw_token, expires_at = await db.create_pairing_token(
            agent_id=None,
            ttl_seconds=120,
            agent_metadata=json.dumps({"hostname": "h1"}),
        )

        assert isinstance(raw_token, str)
        assert raw_token  # non-empty
        # Returned ISO string is parseable.
        datetime.fromisoformat(expires_at)

        meta = await db.lookup_pairing_token_metadata(raw_token)
        assert meta is not None
        assert meta["agent_metadata"] == json.dumps({"hostname": "h1"})
        assert meta["consumed_at"] is None

    async def test_legacy_path_with_agent_id_still_works(
        self, db: Database, owner_id: str
    ):
        # Backwards compat: callers passing agent_id positionally must
        # continue to work — used by the non-QR pairing flow.
        agent_id, _ = await db.register_agent("legacy-agent", owner_id)

        raw_token, _ = await db.create_pairing_token(agent_id, ttl_seconds=60)

        meta = await db.lookup_pairing_token_metadata(raw_token)
        assert meta is not None
        assert meta["agent_metadata"] is None


class TestLookupPairingTokenMetadata:
    async def test_returns_none_for_unknown_nonce(self, db: Database):
        assert await db.lookup_pairing_token_metadata("nope") is None

    async def test_round_trip_metadata(self, db: Database):
        meta_payload = json.dumps({"hostname": "h", "os": "Linux"})
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60, agent_metadata=meta_payload,
        )

        out = await db.lookup_pairing_token_metadata(raw_token)

        assert out is not None
        assert out["agent_metadata"] == meta_payload
        assert out["consumed_at"] is None
        # token_hash is the SHA-256 of the raw nonce.
        assert out["token_hash"] == _hash_token(raw_token)


class TestClaimPairingNonce:
    async def test_first_claim_succeeds(
        self, db: Database, owner_id: str
    ):
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60, agent_metadata=None,
        )
        agent_id, agent_token = await db.register_agent(
            "first-pair-target", owner_id
        )

        ok = await db.claim_pairing_nonce(
            raw_token=raw_token,
            mobile_device_id="dev-1",
            claimed_agent_id=agent_id,
            claimed_token_raw=agent_token,
        )

        assert ok is True
        meta = await db.lookup_pairing_token_metadata(raw_token)
        assert meta["consumed_at"] is not None

    async def test_replay_returns_false(
        self, db: Database, owner_id: str
    ):
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60,
        )
        agent_id, _ = await db.register_agent("a", owner_id)
        await db.claim_pairing_nonce(
            raw_token=raw_token,
            mobile_device_id="dev-1",
            claimed_agent_id=agent_id,
            claimed_token_raw="tok1",
        )

        replay_ok = await db.claim_pairing_nonce(
            raw_token=raw_token,
            mobile_device_id="dev-2",
            claimed_agent_id=agent_id,
            claimed_token_raw="tok2",
        )

        assert replay_ok is False

    async def test_expired_returns_false(
        self, db: Database, owner_id: str
    ):
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60,
        )
        # Force-expire the row by rewriting expires_at to the past.
        past = datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()
        await db.conn.execute(
            "UPDATE pairing_tokens SET expires_at = ? WHERE token_hash = ?",
            (past, _hash_token(raw_token)),
        )
        await db.conn.commit()
        agent_id, _ = await db.register_agent("a", owner_id)

        ok = await db.claim_pairing_nonce(
            raw_token=raw_token,
            mobile_device_id="dev-1",
            claimed_agent_id=agent_id,
            claimed_token_raw="tok",
        )

        assert ok is False

    async def test_concurrent_claims_only_one_wins(
        self, db: Database, owner_id: str
    ):
        # CAS race: the SQL UPDATE-with-WHERE is the concurrency boundary.
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60,
        )
        agent_id, _ = await db.register_agent("a", owner_id)

        async def claim(label: str) -> bool:
            return await db.claim_pairing_nonce(
                raw_token=raw_token,
                mobile_device_id=f"dev-{label}",
                claimed_agent_id=agent_id,
                claimed_token_raw=f"tok-{label}",
            )

        results = await asyncio.gather(
            claim("A"), claim("B"), return_exceptions=False,
        )

        # Exactly one must win — that's the safety property of the CAS.
        assert sum(1 for r in results if r is True) == 1
        assert sum(1 for r in results if r is False) == 1


class TestReadAndClearClaimedCredentials:
    async def test_returns_none_for_unconsumed_row(
        self, db: Database
    ):
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60,
        )

        out = await db.read_and_clear_claimed_credentials(raw_token)

        assert out is None

    async def test_first_read_returns_credentials(
        self, db: Database, owner_id: str
    ):
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60,
        )
        agent_id, _ = await db.register_agent("a", owner_id)
        await db.claim_pairing_nonce(
            raw_token=raw_token,
            mobile_device_id="dev",
            claimed_agent_id=agent_id,
            claimed_token_raw="raw-tok-XYZ",
        )

        out = await db.read_and_clear_claimed_credentials(raw_token)

        assert out is not None
        assert out["claimed_agent_id"] == agent_id
        assert out["claimed_token_raw"] == "raw-tok-XYZ"

    async def test_second_read_returns_none_single_flight(
        self, db: Database, owner_id: str
    ):
        # The single-flight guarantee: a reconnecting WS cannot
        # re-receive the credentials, even though the row is still in
        # the DB for forensics.
        raw_token, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=60,
        )
        agent_id, _ = await db.register_agent("a", owner_id)
        await db.claim_pairing_nonce(
            raw_token=raw_token,
            mobile_device_id="dev",
            claimed_agent_id=agent_id,
            claimed_token_raw="raw-tok-ONE",
        )

        first = await db.read_and_clear_claimed_credentials(raw_token)
        second = await db.read_and_clear_claimed_credentials(raw_token)

        assert first is not None
        assert second is None


class TestTransferAgentOwner:
    async def test_updates_owner_and_returns_true(
        self, db: Database, owner_id: str
    ):
        agent_id, _ = await db.register_agent("agent-1", owner_id)
        new_owner = await db.create_user(
            email="new@test.local", password_hash="h",
        )

        ok = await db.transfer_agent_owner(agent_id, new_owner)

        assert ok is True
        assert await db.get_agent_owner(agent_id) == new_owner

    async def test_returns_false_for_unknown_agent(
        self, db: Database, owner_id: str
    ):
        ok = await db.transfer_agent_owner("no-such-agent", owner_id)

        assert ok is False

    async def test_cascades_into_mobile_refresh_tokens_and_pairings(
        self, db: Database, owner_id: str
    ):
        # Pre-existing pairing + refresh token for the prior owner; the
        # transfer must cascade-delete both so the prior owner's mobile
        # cannot continue calling the agent's refresh endpoint.
        agent_id, _ = await db.register_agent("agent-1", owner_id)
        await db.create_pairing(
            agent_id, mobile_device_id="prior-dev", device_name="Phone",
        )
        await db.store_refresh_token(
            raw_token="prior-refresh-raw",
            mobile_device_id="prior-dev",
            agent_id=agent_id,
            expire_minutes=60,
        )
        new_owner = await db.create_user(
            email="new@test.local", password_hash="h",
        )

        await db.transfer_agent_owner(agent_id, new_owner)

        pairings = await db.list_pairings_for_agent(agent_id)
        assert pairings == []
        # Refresh token must be gone — validate_and_consume_refresh
        # returns None on absent rows.
        consumed = await db.validate_and_consume_refresh("prior-refresh-raw")
        assert consumed is None

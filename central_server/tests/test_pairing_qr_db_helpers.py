"""Tests for the QR pairing helpers on ``central_server.db.database``.

Targets:

* ``create_pairing_token(agent_id=None, agent_metadata=...)`` accepts the
  new optional shape.
* ``lookup_pairing_token_metadata`` returns the pre-claim row.
* ``claim_pairing_nonce`` is a CAS — exactly one of two concurrent
  claims wins.
* ``read_and_clear_claimed_credentials`` is single-flight.
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

        for required in (
            "claimed_token_raw",
            "claimed_agent_id",
            "agent_metadata",
            "relay_url",
        ):
            assert required in cols, f"missing column: {required}"

    async def test_migration_v8_idempotent_on_populated_v7_db(
        self, tmp_path
    ):
        # Real-world upgrade scenario: a deployed DB sitting at
        # user_version=7 with live pairing_tokens rows must accept the
        # v8 migration (add relay_url column) without losing rows or
        # raising on the second reopen. This is the upgrade path the
        # deployment guide drives operators along — the existing per-
        # version migration tests pin v3->v4 and v4->v5; v7->v8 was
        # added by this change set and has no analogous coverage.
        db_path = str(tmp_path / "pre_v8.db")
        owner_email = "owner@upgrade.local"

        # Seed at v7 with a populated row that has NO relay_url.
        database = Database(db_path)
        await database.connect()
        owner = await database.create_user(
            email=owner_email, password_hash="h"
        )
        agent_id, _ = await database.register_agent(
            "v7-pre-upgrade", owner
        )
        existing_raw, _ = await database.create_pairing_token(
            agent_id=agent_id,
            ttl_seconds=300,
            agent_metadata=None,
            # Note: relay_url IS already supported at the API level
            # (the column exists post-v8). Pass None to simulate a row
            # that genuinely predates the migration semantically.
            relay_url=None,
        )
        # Roll user_version back to 7 to simulate the pre-v8 state.
        await database.conn.execute("PRAGMA user_version = 7")
        await database.conn.commit()
        await database.close()

        # First reopen — runs the v7 -> v8 -> ... -> v11 migrations. The
        # column ALTERs are wrapped in column-existence guards, so each step
        # is a no-op-or-add depending on whether the column already exists in
        # the DDL (it does, on a freshly-seeded test DB).
        database = Database(db_path)
        await database.connect()

        cur = await database.conn.execute("PRAGMA user_version")
        version_after_first_reopen = (await cur.fetchone())[0]
        assert version_after_first_reopen == 19, (
            f"after the v7->v19 migrations run, user_version must be 19; "
            f"got {version_after_first_reopen}"
        )
        # The seeded row must survive the migration. The relay_url
        # column is now present and reads back as NULL for the legacy
        # row, which is the documented fallback path.
        meta = await database.lookup_pairing_token_metadata(existing_raw)
        assert meta is not None, (
            "v8 migration destroyed the pre-existing pairing_tokens row"
        )
        assert meta["relay_url"] is None, (
            f"legacy row's relay_url must read as NULL after migration; "
            f"got {meta['relay_url']!r}"
        )
        await database.close()

        # Second reopen — already at version 15, must be a no-op.
        # A non-idempotent migration would error here with
        # ``duplicate column name: relay_url`` or ``duplicate column name:
        # agent_version`` from sqlite, exactly the failure mode the
        # column-existence guards in database.py are meant to prevent.
        # Pin idempotency so a regression that drops a guard (or moves
        # it outside the version check) is caught.
        database = Database(db_path)
        await database.connect()

        cur = await database.conn.execute("PRAGMA user_version")
        version_after_second_reopen = (await cur.fetchone())[0]
        assert version_after_second_reopen == 19

        # The seeded row is still there — second reopen is a no-op,
        # not a destructive rebuild.
        meta2 = await database.lookup_pairing_token_metadata(existing_raw)
        assert meta2 is not None, (
            "the seeded pairing_tokens row vanished on the second "
            "reopen at user_version=14; the migration is not idempotent"
        )
        await database.close()


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
            agent_id=None,
            ttl_seconds=60,
            agent_metadata=meta_payload,
            relay_url="https://relay.test",
        )

        out = await db.lookup_pairing_token_metadata(raw_token)

        assert out is not None
        assert out["agent_metadata"] == meta_payload
        assert out["consumed_at"] is None
        # token_hash is the SHA-256 of the raw nonce.
        assert out["token_hash"] == _hash_token(raw_token)
        # relay_url persisted alongside the metadata so the WS recovery
        # branch echoes the same value as the dispatch branch.
        assert out["relay_url"] == "https://relay.test"


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

    async def test_first_read_returns_persisted_relay_url(
        self, db: Database, owner_id: str
    ):
        # Recovery path uses the persisted relay_url so dispatch and
        # recovery produce byte-identical pushes. Without this field
        # in the return dict, the router falls back to recomputing
        # from ``websocket.url.scheme`` and the agent's strict
        # validation rejects mismatched schemes (the original bug).
        raw_token, _ = await db.create_pairing_token(
            agent_id=None,
            ttl_seconds=60,
            relay_url="https://relay.test",
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
        assert out["relay_url"] == "https://relay.test", (
            f"relay_url must flow through to the recovery path; got "
            f"{out.get('relay_url')!r}"
        )

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

"""Tests for the device-code + trust-pin helpers on ``Database``.

Companion to ``test_pairing_qr_db_helpers.py``: the router suite covers the
HTTP contract, this one covers the storage invariants the router leans on —
the v18 -> v19 migration, the redeem CAS and its per-redeemer replay, the
cancel-expires-the-nonce transaction, the pin's compare-and-set, and the
retention sweeps.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from central_server.db.database import Database, _hash_token

_CODE_HASH = "c0" * 32
_OTHER_CODE_HASH = "c1" * 32
_FP_A = "aa" * 32
_FP_B = "bb" * 32


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "device_codes.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def owner_id(db: Database) -> str:
    return await db.create_user(email="owner@test.local", password_hash="h")


async def _mint(db: Database, owner: str, *, code_hash=_CODE_HASH, ttl=600) -> str:
    request_id = f"req-{code_hash[:6]}"
    await db.create_device_code(
        request_id=request_id,
        code_hash=code_hash,
        user_id=owner,
        ttl_seconds=ttl,
    )
    return request_id


async def _redeem(db: Database, *, code_hash=_CODE_HASH, redeemer="redeemer-1", **kw):
    return await db.redeem_device_code(
        code_hash=code_hash,
        redeemer_id=redeemer,
        hostname=kw.get("hostname"),
        os_name=kw.get("os_name"),
        current_agent_id=kw.get("current_agent_id"),
        agent_metadata=kw.get("agent_metadata"),
        nonce_ttl_seconds=kw.get("nonce_ttl_seconds", 300),
        relay_url=kw.get("relay_url", "https://relay.test"),
    )


# The ``users`` DDL as it stood at user_version 18. Materialised verbatim
# rather than reached with ALTER TABLE ... DROP COLUMN, which SQLite
# refuses here: the head DDL carries a comment ahead of the new column and
# the rewritten definition comes out as "incomplete input".
_V18_USERS_DDL = """
CREATE TABLE users (
    id                      TEXT PRIMARY KEY,
    email                   TEXT NOT NULL UNIQUE,
    password_hash           TEXT,
    google_sub              TEXT UNIQUE,
    apple_sub               TEXT,
    role                    TEXT NOT NULL DEFAULT 'viewer',
    email_verified          INTEGER NOT NULL DEFAULT 0,
    verified_at             TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    scheduled_deletion_at   TEXT,
    deletion_requested_at   TEXT
);
"""


class TestMigration:
    async def test_v19_adds_the_table_and_the_users_column(self, tmp_path):
        db_path = str(tmp_path / "pre_v19.db")
        db = Database(db_path)
        await db.connect()
        # Roll the on-disk schema back to v18 so reopening replays the v19
        # block against a DB that predates both changes.
        await db.conn.execute("PRAGMA foreign_keys=OFF")
        await db.conn.execute("DROP INDEX idx_pairing_device_codes_live")
        await db.conn.execute("DROP TABLE pairing_device_codes")
        await db.conn.execute("DROP TABLE users")
        await db.conn.executescript(_V18_USERS_DDL)
        await db.conn.execute("PRAGMA user_version = 18")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 21

        # The column has to be ALTERed in before _SCHEMA re-runs, and the
        # new table has to come back usable alongside it.
        owner = await db.create_user(email="pre@test.local", password_hash="h")
        assert await db.pin_user_trust_key_fp(owner, _FP_A) is True
        request_id = await _mint(db, owner)
        assert await db.get_device_code(request_id, owner) is not None
        await db.close()

        # Idempotent: a third open at head is a no-op and nothing is lost.
        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 21
        assert await db.get_user_trust_key_fp(owner) == _FP_A
        assert await db.get_device_code(request_id, owner) is not None
        await db.close()

    async def test_user_reads_carry_the_pin_column(self, db, owner_id):
        # _USER_COLUMNS drives every get_user_by_*; a missed column would
        # silently drop the pin from the row the auth layer hands around.
        await db.pin_user_trust_key_fp(owner_id, _FP_A)

        user = await db.get_user_by_id(owner_id)

        assert user["trust_key_fp"] == _FP_A


class TestCreateDeviceCode:
    async def test_minting_cancels_the_account_s_earlier_live_code(
        self, db, owner_id
    ):
        first = await _mint(db, owner_id)
        await _mint(db, owner_id, code_hash=_OTHER_CODE_HASH)

        row = await db.get_device_code(first, owner_id)

        # Canceled rather than deleted, so a redeem of the stale code is
        # distinguishable from a code that never existed.
        assert row["canceled_at"] is not None
        assert await _redeem(db) is None

    async def test_another_account_s_live_code_survives(self, db, owner_id):
        other = await db.create_user(email="other@test.local", password_hash="h")
        theirs = await _mint(db, other)

        await _mint(db, owner_id, code_hash=_OTHER_CODE_HASH)

        assert (await db.get_device_code(theirs, other))["canceled_at"] is None

    async def test_a_foreign_reader_cannot_see_the_row(self, db, owner_id):
        other = await db.create_user(email="other@test.local", password_hash="h")
        request_id = await _mint(db, owner_id)

        assert await db.get_device_code(request_id, other) is None


class TestRedeem:
    async def test_binds_the_host_and_mints_a_linked_nonce(self, db, owner_id):
        request_id = await _mint(db, owner_id)

        outcome = await _redeem(db, hostname="pavel-mbp", os_name="Darwin")

        assert outcome["request_id"] == request_id
        assert outcome["user_id"] == owner_id
        assert outcome["replayed"] is False
        row = await db.get_device_code(request_id, owner_id)
        assert row["redeem_hostname"] == "pavel-mbp"
        # The raw nonce is never stored — only its digest links the rows.
        assert row["pairing_nonce_hash"] == _hash_token(outcome["nonce"])
        assert outcome["nonce"] not in str(dict(row))

    async def test_exactly_one_of_two_racing_machines_wins(self, db, owner_id):
        await _mint(db, owner_id)

        results = await asyncio.gather(
            _redeem(db, redeemer="machine-a"),
            _redeem(db, redeemer="machine-b"),
        )

        assert sum(1 for r in results if r is not None) == 1

    async def test_the_same_machine_replays_without_a_second_nonce(
        self, db, owner_id
    ):
        await _mint(db, owner_id)
        first = await _redeem(db, redeemer="machine-a")

        replay = await _redeem(db, redeemer="machine-a")

        assert replay["replayed"] is True
        assert replay["nonce"] is None
        assert replay["expires_at"] == first["expires_at"]

    async def test_a_cancel_between_the_cas_and_the_link_wins(
        self, db, owner_id, monkeypatch
    ):
        # Minting the token sits between the redeem CAS and the update that
        # links it, and the cancel that lands there reads a still-NULL
        # ``pairing_nonce_hash`` — so it expires nothing. An unguarded link
        # then attached a live nonce to a canceled request: the agent
        # prompted its operator and waited out the whole confirm TTL for a
        # confirm that could only ever 410.
        request_id = await _mint(db, owner_id)
        original = db._insert_pairing_token

        async def cancel_then_insert(**kwargs):
            await db.cancel_device_code(request_id, owner_id)
            return await original(**kwargs)

        monkeypatch.setattr(db, "_insert_pairing_token", cancel_then_insert)

        assert await _redeem(db) is None
        row = await db.get_device_code(request_id, owner_id)
        assert row["canceled_at"] is not None
        assert row["pairing_nonce_hash"] is None

    async def test_an_expired_code_cannot_be_redeemed(self, db, owner_id):
        await _mint(db, owner_id, ttl=-1)

        assert await _redeem(db) is None

    async def test_an_unknown_code_hash_is_rejected(self, db, owner_id):
        await _mint(db, owner_id)

        assert await _redeem(db, code_hash="ff" * 32) is None

    async def test_the_nonce_and_the_code_settle_together(self, db, owner_id):
        # Both writes are in one transaction, so a redeem that reports
        # success must always leave a token row the agent can wait on.
        await _mint(db, owner_id)

        outcome = await _redeem(db, agent_metadata='{"hostname": "h"}')

        token = await db.lookup_pairing_token_metadata(outcome["nonce"])
        assert token is not None
        assert token["consumed_at"] is None
        assert token["agent_metadata"] == '{"hostname": "h"}'
        assert token["relay_url"] == "https://relay.test"


class TestConfirmAndCancel:
    async def test_confirm_is_a_compare_and_set(self, db, owner_id):
        request_id = await _mint(db, owner_id)
        await _redeem(db)

        assert await db.mark_device_code_confirmed(request_id, owner_id) is True
        assert await db.mark_device_code_confirmed(request_id, owner_id) is False

    async def test_confirm_refuses_a_code_no_machine_has_redeemed(
        self, db, owner_id
    ):
        request_id = await _mint(db, owner_id)

        assert await db.mark_device_code_confirmed(request_id, owner_id) is False

    async def test_cancel_expires_the_linked_nonce_atomically(self, db, owner_id):
        request_id = await _mint(db, owner_id)
        outcome = await _redeem(db)

        assert await db.cancel_device_code(request_id, owner_id) is True

        # A confirm racing the cancel loses the CAS on expires_at, and the
        # agent's WebSocket stops waiting at its next TTL check.
        token = await db.lookup_pairing_token_metadata(outcome["nonce"])
        assert datetime.fromisoformat(token["expires_at"]) <= datetime.now(
            timezone.utc
        )
        assert (
            await db.claim_pairing_nonce(
                raw_token=outcome["nonce"],
                mobile_device_id="dev",
                claimed_agent_id="agent",
                claimed_token_raw="tok",
            )
            is False
        )

    async def test_reopening_hands_a_reservation_back(self, db, owner_id):
        # The confirm endpoint reserves before it issues credentials, so a
        # pairing it then refuses has to leave the request confirmable.
        request_id = await _mint(db, owner_id)
        await _redeem(db)
        await db.mark_device_code_confirmed(request_id, owner_id)

        assert await db.reopen_device_code(request_id, owner_id) is True
        assert await db.mark_device_code_confirmed(request_id, owner_id) is True

    async def test_reopening_cannot_revive_a_canceled_request(
        self, db, owner_id
    ):
        request_id = await _mint(db, owner_id)
        await _redeem(db)
        await db.cancel_device_code(request_id, owner_id)

        assert await db.reopen_device_code(request_id, owner_id) is False
        assert await db.mark_device_code_confirmed(request_id, owner_id) is False

    async def test_cancel_refuses_a_confirmed_request(self, db, owner_id):
        request_id = await _mint(db, owner_id)
        await _redeem(db)
        await db.mark_device_code_confirmed(request_id, owner_id)

        assert await db.cancel_device_code(request_id, owner_id) is False

    async def test_cancel_refuses_a_request_owned_by_someone_else(
        self, db, owner_id
    ):
        other = await db.create_user(email="other@test.local", password_hash="h")
        request_id = await _mint(db, owner_id)

        assert await db.cancel_device_code(request_id, other) is False


class TestTrustKeyPin:
    async def test_pin_is_taken_only_while_unset(self, db, owner_id):
        assert await db.pin_user_trust_key_fp(owner_id, _FP_A) is True

        # The IS NULL predicate is the concurrency boundary — a second
        # pairing must not be able to overwrite an established pin.
        assert await db.pin_user_trust_key_fp(owner_id, _FP_B) is False
        assert await db.get_user_trust_key_fp(owner_id) == _FP_A

    async def test_clearing_makes_the_account_pinnable_again(self, db, owner_id):
        await db.pin_user_trust_key_fp(owner_id, _FP_A)

        assert await db.clear_user_trust_key_fp(owner_id) is True
        assert await db.get_user_trust_key_fp(owner_id) is None
        assert await db.pin_user_trust_key_fp(owner_id, _FP_B) is True

    async def test_clearing_an_unpinned_account_reports_no_change(
        self, db, owner_id
    ):
        assert await db.clear_user_trust_key_fp(owner_id) is False

    async def test_agent_count_drives_the_auto_repin_escape_hatch(
        self, db, owner_id
    ):
        assert await db.count_agents_for_owner(owner_id) == 0

        await db.register_agent("a1", owner_id)

        assert await db.count_agents_for_owner(owner_id) == 1


class TestCleanupAndCascade:
    async def test_expired_unredeemed_codes_are_swept(self, db, owner_id):
        request_id = await _mint(db, owner_id, ttl=-1)

        await db.cleanup_expired_tokens()

        assert await db.get_device_code(request_id, owner_id) is None

    async def test_a_live_code_survives_the_sweep(self, db, owner_id):
        request_id = await _mint(db, owner_id)

        await db.cleanup_expired_tokens()

        assert await db.get_device_code(request_id, owner_id) is not None

    async def test_a_settled_row_lingers_only_for_the_retention_window(
        self, db, owner_id
    ):
        request_id = await _mint(db, owner_id)
        await _redeem(db)
        await db.cancel_device_code(request_id, owner_id)

        await db.cleanup_expired_tokens()
        assert await db.get_device_code(request_id, owner_id) is not None

        # A settled row still carries a peppered hash of a ~34.6-bit
        # secret, so it must not be kept indefinitely.
        stale = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        await db.conn.execute(
            "UPDATE pairing_device_codes SET canceled_at = ?", (stale,)
        )
        await db.conn.commit()
        await db.cleanup_expired_tokens()

        assert await db.get_device_code(request_id, owner_id) is None

    async def test_a_redeemed_but_unsettled_row_is_swept_past_the_retention_window(
        self, db, owner_id
    ):
        # The operator answered "no" at the agent's prompt, the agent's
        # WebSocket died, the confirm was refused by the ownership guard, or
        # the browser tab was simply closed — none of those paths sets
        # ``confirmed_at`` or ``canceled_at``, so without this sweep the row
        # (and its code hash) would live forever.
        request_id = await _mint(db, owner_id)
        await _redeem(db)

        await db.cleanup_expired_tokens()
        assert await db.get_device_code(request_id, owner_id) is not None

        stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        await db.conn.execute(
            "UPDATE pairing_device_codes SET redeemed_at = ?", (stale,)
        )
        await db.conn.commit()
        await db.cleanup_expired_tokens()

        assert await db.get_device_code(request_id, owner_id) is None

    async def test_deleting_the_account_drops_its_codes(self, db, owner_id):
        request_id = await _mint(db, owner_id)

        await db.delete_user(owner_id)

        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM pairing_device_codes WHERE request_id = ?",
            (request_id,),
        )
        assert (await cursor.fetchone())[0] == 0

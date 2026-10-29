"""Database layer behind the Google Ads "Agent paired" conversion: the v20
migration (columns + legacy backfill), the once-only first-pairing marker,
attribution storage at account creation, and the retention purge."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from central_server.db.database import Database

_AD_COLUMNS = (
    "ad_click_id",
    "ad_click_kind",
    "ad_click_at",
    "ads_consent",
    "first_paired_at",
)

# The users table as v19 left it. Rolling back by recreating the table (the
# same approach as the v19 test) rather than ``ALTER TABLE DROP COLUMN``:
# SQLite rewrites the stored CREATE text for a drop and chokes on the
# comment lines that precede the trailing columns.
_V19_USERS_COLUMNS = (
    "id, email, password_hash, google_sub, apple_sub, role, email_verified, "
    "verified_at, created_at, updated_at, scheduled_deletion_at, "
    "deletion_requested_at, trust_key_fp"
)
_V19_USERS_DDL = """
CREATE TABLE users_v19 (
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
    deletion_requested_at   TEXT,
    trust_key_fp            TEXT
);
"""


async def _user(db: Database, email: str = "owner@test.local", **kwargs) -> str:
    return await db.create_user(email=email, password_hash="hash", **kwargs)


async def _row(db: Database, user_id: str) -> dict:
    row = await db.get_user_by_id(user_id)
    assert row is not None
    return row


class TestV20Migration:
    async def test_v20_adds_the_columns_and_backfills_first_paired_at(
        self, tmp_path
    ):
        db_path = str(tmp_path / "pre_v20.db")
        db = Database(db_path)
        await db.connect()
        paired = await _user(db, "paired@test.local")
        await db.register_agent("agent", paired)
        pinned = await _user(db, "pinned@test.local")
        await db.pin_user_trust_key_fp(pinned, "ab" * 32)
        fresh = await _user(db, "fresh@test.local")
        # Roll the on-disk schema back to v19 so reopening replays the v20
        # block against populated rows.
        await db.conn.execute("PRAGMA foreign_keys=OFF")
        await db.conn.executescript(_V19_USERS_DDL)
        await db.conn.execute(
            f"INSERT INTO users_v19 ({_V19_USERS_COLUMNS}) "
            f"SELECT {_V19_USERS_COLUMNS} FROM users"
        )
        await db.conn.execute("DROP TABLE users")
        await db.conn.execute("ALTER TABLE users_v19 RENAME TO users")
        await db.conn.execute("PRAGMA user_version = 19")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 22
        cursor = await db.conn.execute("PRAGMA table_info(users)")
        assert set(_AD_COLUMNS) <= {r[1] for r in await cursor.fetchall()}
        # Accounts that already paired must not read as first-time later.
        assert (await _row(db, paired))["first_paired_at"] is not None
        assert (await _row(db, pinned))["first_paired_at"] is not None
        assert (await _row(db, fresh))["first_paired_at"] is None
        assert (await _row(db, fresh))["ads_consent"] == 0
        assert await db.mark_user_first_paired(paired) is False
        assert await db.mark_user_first_paired(fresh) is True
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 22
        await db.close()


class TestAttributionStorage:
    async def test_create_user_stores_attribution_and_defaults_consent_off(
        self, db
    ):
        plain = await _user(db, "plain@test.local")
        row = await _row(db, plain)
        assert row["ads_consent"] == 0
        assert row["ad_click_id"] is None
        assert row["first_paired_at"] is None

        attributed = await _user(
            db,
            "ads@test.local",
            ad_click_id="Cj0K_abc-123",
            ad_click_kind="gclid",
            ad_click_at="2026-09-01T10:00:00+00:00",
            ads_consent=True,
        )
        row = await _row(db, attributed)
        assert row["ad_click_id"] == "Cj0K_abc-123"
        assert row["ad_click_kind"] == "gclid"
        assert row["ad_click_at"] == "2026-09-01T10:00:00+00:00"
        assert row["ads_consent"] == 1

    async def test_mark_user_first_paired_is_true_exactly_once(self, db):
        user = await _user(db)
        assert await db.mark_user_first_paired(user) is True
        assert (await _row(db, user))["first_paired_at"] is not None
        assert await db.mark_user_first_paired(user) is False

    async def test_first_paired_survives_trust_pin_clear(self, db):
        user = await _user(db)
        await db.pin_user_trust_key_fp(user, "ab" * 32)
        assert await db.mark_user_first_paired(user) is True
        assert await db.clear_user_trust_key_fp(user) is True
        assert await db.pin_user_trust_key_fp(user, "cd" * 32) is True
        assert await db.mark_user_first_paired(user) is False

    async def test_clear_user_ad_attribution_nulls_columns_and_resets_consent(
        self, db
    ):
        user = await _user(
            db,
            ad_click_id="abc",
            ad_click_kind="wbraid",
            ad_click_at="2026-09-01T10:00:00+00:00",
            ads_consent=True,
        )
        assert await db.clear_user_ad_attribution(user) is True
        row = await _row(db, user)
        assert (
            row["ad_click_id"],
            row["ad_click_kind"],
            row["ad_click_at"],
            row["ads_consent"],
        ) == (None, None, None, 0)
        assert await db.clear_user_ad_attribution(user) is False

    @pytest.mark.parametrize(
        "claimed",
        [{"email_verified": True}, {"google_sub": "g-1"}, {"apple_sub": "a-1"}],
    )
    async def test_take_over_unverified_signup_leaves_claimed_rows_alone(
        self, db, claimed
    ):
        user = await _user(
            db, ad_click_id="abc", ad_click_kind="gclid", ads_consent=True, **claimed
        )
        assert await db.take_over_unverified_signup(user, "new-hash") is False
        row = await _row(db, user)
        assert (row["password_hash"], row["ad_click_id"], row["ads_consent"]) == (
            "hash",
            "abc",
            1,
        )

    async def test_purge_uses_click_time_then_created_at_and_skips_unconsented(
        self, db
    ):
        old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        stale_click = await _user(
            db,
            "stale@test.local",
            ad_click_id="a",
            ad_click_kind="gclid",
            ad_click_at=old,
            ads_consent=True,
        )
        stale_consent = await _user(db, "consent@test.local", ads_consent=True)
        await db.conn.execute(
            "UPDATE users SET created_at = ? WHERE id = ?", (old, stale_consent)
        )
        fresh = await _user(
            db,
            "fresh@test.local",
            ad_click_id="b",
            ad_click_kind="gclid",
            ad_click_at=datetime.now(timezone.utc).isoformat(),
            ads_consent=True,
        )
        unconsented = await _user(db, "none@test.local")
        await db.conn.execute(
            "UPDATE users SET created_at = ? WHERE id = ?", (old, unconsented)
        )
        # A skewed browser clock cannot extend retention: the click is
        # stamped years ahead, but the account itself is 91 days old.
        future_click = await _user(
            db,
            "future@test.local",
            ad_click_id="c",
            ad_click_kind="gclid",
            ad_click_at=(datetime.now(timezone.utc) + timedelta(days=3650)).isoformat(),
            ads_consent=True,
        )
        await db.conn.execute(
            "UPDATE users SET created_at = ? WHERE id = ?", (old, future_click)
        )
        # A click without network-bound consent is still attribution.
        click_only = await _user(
            db, "clickonly@test.local", ad_click_id="d", ad_click_kind="gclid", ad_click_at=old
        )
        await db.conn.commit()

        assert await db.purge_stale_ad_attribution() == 4
        assert (await _row(db, click_only))["ad_click_id"] is None
        assert (await _row(db, stale_click))["ads_consent"] == 0
        assert (await _row(db, stale_click))["ad_click_id"] is None
        assert (await _row(db, stale_consent))["ads_consent"] == 0
        assert (await _row(db, future_click))["ads_consent"] == 0
        assert (await _row(db, future_click))["ad_click_id"] is None
        assert (await _row(db, fresh))["ads_consent"] == 1
        assert (await _row(db, fresh))["ad_click_id"] == "b"
        assert await db.purge_stale_ad_attribution() == 0

    async def test_list_users_with_pending_ad_conversion(self, db):
        pending = await _user(db, "pending@test.local", ads_consent=True)
        await db.mark_user_first_paired(pending)
        await _user(db, "unpaired@test.local", ads_consent=True)
        done = await _user(db, "done@test.local", ads_consent=True)
        await db.mark_user_first_paired(done)
        await db.clear_user_ad_attribution(done)
        click_only = await _user(db, "clickonly@test.local", ad_click_id="c", ad_click_kind="gclid")
        await db.mark_user_first_paired(click_only)
        assert await db.list_users_with_pending_ad_conversion() == [pending, click_only]


class TestWithdrawal:
    async def test_withdraw_clears_every_account_created_with_the_cookie(self, db):
        first = await _user(
            db, "first@test.local", ad_click_id="a", ad_click_kind="gclid",
            ads_consent=True, ad_attribution_sig="sig-1",
        )
        second = await _user(db, "second@test.local", ads_consent=True, ad_attribution_sig="sig-1")
        other = await _user(
            db, "other@test.local", ad_click_id="b", ad_click_kind="gclid",
            ads_consent=True, ad_attribution_sig="sig-2",
        )
        assert (await _row(db, first))["ad_attribution_sig"] == "sig-1"

        assert await db.withdraw_ad_attribution("sig-1") == 2

        for user in (first, second):
            row = await _row(db, user)
            assert (row["ads_consent"], row["ad_click_id"], row["ad_attribution_sig"]) == (0, None, None)
        assert (await _row(db, other))["ad_click_id"] == "b"
        assert await db.withdraw_ad_attribution("sig-1") == 0
        assert await db.withdraw_ad_attribution("never-minted") == 0

    async def test_clear_and_purge_drop_the_signature_too(self, db):
        cleared = await _user(db, "cleared@test.local", ads_consent=True, ad_attribution_sig="sig-c")
        await db.clear_user_ad_attribution(cleared)
        assert (await _row(db, cleared))["ad_attribution_sig"] is None
        old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        stale = await _user(db, "stale@test.local", ads_consent=True, ad_attribution_sig="sig-s")
        await db.conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (old, stale))
        await db.conn.commit()
        assert await db.purge_stale_ad_attribution() == 1
        assert (await _row(db, stale))["ad_attribution_sig"] is None

"""SQLite database layer via aiosqlite.

Manages persistent state: agent registry (user-owned), mobile devices,
pairing tokens, mobile refresh tokens, and user accounts.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY,
    email                   TEXT NOT NULL UNIQUE,
    password_hash           TEXT,
    google_sub              TEXT UNIQUE,
    -- No inline UNIQUE: upgraded DBs get the column via ALTER TABLE ADD
    -- COLUMN (which cannot attach UNIQUE), so uniqueness lives in the
    -- partial index below to keep fresh and upgraded schemas identical.
    apple_sub               TEXT,
    role                    TEXT NOT NULL DEFAULT 'viewer',
    email_verified          INTEGER NOT NULL DEFAULT 0,
    verified_at             TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    scheduled_deletion_at   TEXT,
    deletion_requested_at   TEXT,
    -- Fingerprint of the relay signing key this account first paired
    -- against. Taken on the first successful claim and compared on every
    -- later one, so a client whose local pin was wiped cannot silently
    -- re-trust a rotated key. NULL until the account pairs once.
    trust_key_fp            TEXT,
    -- Google Ads click attribution copied from the web sign-up request
    -- (consent-gated on the landing page). Read once, by the conversion
    -- uploader on the first pairing, then cleared; attribution is pending
    -- while a click id or consent is stored. first_paired_at is set once,
    -- on the first successful pairing, and unlike trust_key_fp is never
    -- cleared, which is what makes it safe as the conversion trigger.
    -- No comment may sit between the last comma and the last column, and
    -- no quote may appear in these comments: SQLite rewrites this text on
    -- ALTER TABLE DROP COLUMN and both break the rewrite.
    ad_click_id             TEXT,
    ad_click_kind           TEXT,
    ad_click_at             TEXT,
    ads_consent             INTEGER NOT NULL DEFAULT 0,
    ad_attribution_sig      TEXT,
    first_paired_at         TEXT
);

-- The signature of the consent cookie an account was created with, so a
-- later Decline on the landing page can withdraw that account's stored
-- attribution without knowing who the visitor is.
CREATE INDEX IF NOT EXISTS idx_users_ad_attribution_sig
    ON users(ad_attribution_sig) WHERE ad_attribution_sig IS NOT NULL;

CREATE TABLE IF NOT EXISTS agents (
    agent_id               TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    token_hash             TEXT NOT NULL,
    owner_user_id          TEXT REFERENCES users(id),
    created_at             TEXT NOT NULL,
    last_seen_at           TEXT,
    agent_version          TEXT,
    agent_protocol_version INTEGER
);

CREATE TABLE IF NOT EXISTS mobile_devices (
    device_id        TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT,
    created_at       TEXT NOT NULL,
    last_seen_at     TEXT,
    revoked_at       TEXT,
    platform         TEXT,
    push_token       TEXT,
    push_environment TEXT
);

CREATE TABLE IF NOT EXISTS pairing_tokens (
    token_hash         TEXT PRIMARY KEY,
    agent_id           TEXT REFERENCES agents(agent_id),
    created_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    consumed_at        TEXT,
    consumed_by        TEXT,
    claimed_token_raw  TEXT,
    claimed_agent_id   TEXT REFERENCES agents(agent_id),
    agent_metadata     TEXT,
    relay_url          TEXT
);

CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
    token_hash       TEXT PRIMARY KEY,
    mobile_device_id TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT
);

CREATE TABLE IF NOT EXISTS user_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS account_deletion_cancel_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT
);

-- No FK on user_id: audit rows must outlive the user row they reference
-- (GDPR Art. 17 compliance trail). email_at_request is captured at request
-- time so the row remains meaningful after the user cascade-purge.
CREATE TABLE IF NOT EXISTS deletion_audit (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    email_at_request    TEXT NOT NULL,
    requested_at        TEXT NOT NULL,
    scheduled_for       TEXT NOT NULL,
    source              TEXT NOT NULL CHECK(source IN ('android', 'portal', 'email_link')),
    status              TEXT NOT NULL CHECK(status IN ('pending', 'cancelled', 'completed', 'failed')),
    mode                TEXT NOT NULL DEFAULT 'scheduled' CHECK(mode IN ('scheduled', 'immediate')),
    completed_at        TEXT,
    cancellation_reason TEXT
);

CREATE TABLE IF NOT EXISTS prompt_templates (
    template_id   TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    name          TEXT NOT NULL,
    body          TEXT NOT NULL,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT,
    version       INTEGER NOT NULL DEFAULT 1
);

-- Saved keyboard shortcuts pinned to the terminal control-key row.
-- ``kind`` is the SessionKind wire name (opaque to the server) and
-- ``chord_token`` is the client-side KeyChord.token() string; the server
-- never interprets either.
CREATE TABLE IF NOT EXISTS user_shortcuts (
    shortcut_id   TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    kind          TEXT NOT NULL,
    chord_token   TEXT NOT NULL,
    name          TEXT,
    position      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT,
    version       INTEGER NOT NULL DEFAULT 1
);

-- Account-scoped custom AI agent types: a display name plus the launch
-- command the relay injects server-side on session create; clients never
-- send the command. The wire session kind is ``custom_<id>``.
CREATE TABLE IF NOT EXISTS custom_agent_types (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id),
    name          TEXT NOT NULL,
    command       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT
);

-- Reversed-direction pairing: the browser mints a short typeable code,
-- the operator retypes it on the agent host. ``code_hash`` is a peppered
-- HMAC (the code is only ~34.6 bits). ``pairing_nonce_hash`` links to the
-- ``pairing_tokens`` row minted at redeem time, so the credential push
-- reuses the QR flow's WebSocket, signature and recovery slot unchanged.
-- The raw nonce is never stored here; the confirm path addresses the
-- token row by hash.
CREATE TABLE IF NOT EXISTS pairing_device_codes (
    request_id         TEXT PRIMARY KEY,
    code_hash          TEXT NOT NULL UNIQUE,
    user_id            TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    redeemed_at        TEXT,
    redeemer_id        TEXT,
    redeem_hostname    TEXT,
    redeem_os          TEXT,
    redeem_agent_id    TEXT,
    -- Set when the operator answered the agent's terminal prompt. The
    -- browser cannot confirm before it: minting is the browser's, so the
    -- terminal is the only party that can refuse a code it did not ask
    -- for, and nothing may be created or rotated until it has.
    approved_at        TEXT,
    pairing_nonce_hash TEXT,
    confirmed_at       TEXT,
    canceled_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_sub
    ON users(google_sub) WHERE google_sub IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_apple_sub
    ON users(apple_sub) WHERE apple_sub IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_mobile_devices_user
    ON mobile_devices(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pairing_tokens_agent
    ON pairing_tokens(agent_id) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pairing_tokens_claimed_agent
    ON pairing_tokens(claimed_agent_id) WHERE claimed_agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_refresh_agent_device
    ON mobile_refresh_tokens(agent_id, mobile_device_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_refresh_mobile_device
    ON mobile_refresh_tokens(mobile_device_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_refresh_user
    ON user_refresh_tokens(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_email_verification_user
    ON email_verification_tokens(user_id) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_password_reset_user
    ON password_reset_tokens(user_id) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_account_deletion_cancel_user
    ON account_deletion_cancel_tokens(user_id) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_users_scheduled_deletion
    ON users(scheduled_deletion_at) WHERE scheduled_deletion_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deletion_audit_user
    ON deletion_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_deletion_audit_pending
    ON deletion_audit(status, scheduled_for) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_prompt_templates_owner
    ON prompt_templates(owner_user_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_shortcuts_owner
    ON user_shortcuts(owner_user_id) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_shortcuts_dedupe
    ON user_shortcuts(owner_user_id, kind, chord_token) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pairing_device_codes_live
    ON pairing_device_codes(user_id)
    WHERE redeemed_at IS NULL AND canceled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_custom_agent_types_owner
    ON custom_agent_types(owner_user_id) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_agent_types_owner_name
    ON custom_agent_types(owner_user_id, name COLLATE NOCASE)
    WHERE deleted_at IS NULL;
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw: str) -> str:
    """SHA-256 hash of a pairing/refresh token (for DB storage)."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ``last_activity`` resolves both inside the CTE (as the alias) and outside
# (as the ``page`` column). ``u.created_at`` must stay qualified because the
# outer query also joins ``agents.created_at``.
_USERS_SORT_KEYS = {"registered": "u.created_at", "activity": "last_activity"}
_USERS_ACTIVITY_COLUMN = (
    ", (SELECT MAX(a.last_seen_at) FROM agents a "
    "WHERE a.owner_user_id = u.id) AS last_activity"
)


def _users_filter(q: str, verified: bool | None) -> tuple[str, list]:
    """Build the ``WHERE`` clause (with leading space, or empty) plus its
    parameters for the ``users u`` filters shared by ``count_users`` and
    ``list_users_with_agents``.

    ``q`` is a case-insensitive email substring match. It goes through
    ``instr`` rather than ``LIKE`` so ``%`` and ``_`` in the needle stay
    literal without an ESCAPE dance. Both operands are folded by SQLite's
    ``lower()`` — ASCII-only, but symmetric, so an address is always found
    by its exact spelling; folding the needle in Python instead would
    lower-case ``Ü`` on one side only and miss ``Ünal@…``.
    """
    clauses: list[str] = []
    params: list = []
    needle = q.strip()
    if needle:
        clauses.append("instr(lower(u.email), lower(?)) > 0")
        params.append(needle)
    if verified is not None:
        clauses.append("u.email_verified = ?")
        params.append(1 if verified else 0)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# Sentinel timestamp used in `mobile_refresh_tokens.expires_at` when the caller
# requests a non-expiring refresh token (expire_minutes == 0). ISO-8601 strings
# sort lexically in SQLite, so any real `datetime.now(UTC)` compares strictly
# less than this value and the existing `expires_at > now` query continues to
# match.
_NEVER_EXPIRES_AT = "9999-12-31T23:59:59.999999+00:00"


# users columns the v20 block introduced, in ALTER TABLE form. Hoisted into
# connect()'s pre-ladder guard because _SCHEMA's idx_users_ad_attribution_sig
# references one of them.
_AD_ATTRIBUTION_COLUMNS = (
    ("ad_click_id", "TEXT"),
    ("ad_click_kind", "TEXT"),
    ("ad_click_at", "TEXT"),
    ("ads_consent", "INTEGER NOT NULL DEFAULT 0"),
    ("ad_attribution_sig", "TEXT"),
    ("first_paired_at", "TEXT"),
)


class Database:
    """Async SQLite database for the central server."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL is the recommended synchronous level for WAL: commits stop
        # fsyncing individually (only checkpoints do), at the cost of possibly
        # losing the last un-checkpointed frames on OS crash/power loss —
        # acceptable for this data. Durable across application crashes.
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        # Schema migration via PRAGMA user_version
        cursor = await self._conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        version = row[0] if row else 0

        # users.apple_sub must exist before ANY older migration block runs
        # executescript(_SCHEMA): idx_users_apple_sub in _SCHEMA references
        # the column, and CREATE INDEX IF NOT EXISTS still validates the
        # referenced column even when the index already exists (same trap as
        # the owner_user_id guard below). Hoisted here — not only in the
        # version<14 block — so a pre-v13 DB whose earlier blocks re-run
        # _SCHEMA doesn't crash with "no such column: apple_sub".
        if version < 14:
            users_cursor = await self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            users_exists = await users_cursor.fetchone() is not None
            if users_exists:
                col_cursor = await self._conn.execute("PRAGMA table_info(users)")
                user_cols = {r[1] for r in await col_cursor.fetchall()}
                if "apple_sub" not in user_cols:
                    await self._conn.execute(
                        "ALTER TABLE users ADD COLUMN apple_sub TEXT"
                    )

        # Same hoist for the v20 attribution columns: idx_users_ad_attribution_sig
        # in _SCHEMA references users.ad_attribution_sig, and every block
        # below re-runs _SCHEMA, so a DB older than v20 must gain the columns
        # before the first of those re-runs, not in the v20 block itself.
        if version < 20:
            users_cursor = await self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            if await users_cursor.fetchone() is not None:
                col_cursor = await self._conn.execute("PRAGMA table_info(users)")
                user_cols = {r[1] for r in await col_cursor.fetchall()}
                for column, ddl in _AD_ATTRIBUTION_COLUMNS:
                    if column not in user_cols:
                        await self._conn.execute(
                            f"ALTER TABLE users ADD COLUMN {column} {ddl}"
                        )

        if version < 3:
            # Legacy DBs may have an `agents` table WITHOUT the `owner_user_id`
            # column. Add it first so the idx_agents_owner index in _SCHEMA
            # below doesn't fail with "no such column" — CREATE INDEX IF NOT
            # EXISTS still validates the referenced column even when the index
            # already exists.
            agents_cursor = await self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
            )
            agents_exists = await agents_cursor.fetchone() is not None
            if agents_exists:
                col_cursor = await self._conn.execute("PRAGMA table_info(agents)")
                cols = {r[1] for r in await col_cursor.fetchall()}
                if "owner_user_id" not in cols:
                    await self._conn.execute(
                        "ALTER TABLE agents ADD COLUMN owner_user_id TEXT REFERENCES users(id)"
                    )

            await self._conn.executescript(_SCHEMA)

            await self._conn.execute("PRAGMA user_version = 3")

        if version < 4:
            # Enforce invariant: admin users never own agents. Drop any
            # admin-owned agents and their dependent rows (schema has no
            # ON DELETE CASCADE, so cascade manually — mirrors delete_agent).
            admin_agent_ids_sql = (
                "SELECT a.agent_id FROM agents a "
                "JOIN users u ON a.owner_user_id = u.id "
                "WHERE u.role = 'admin'"
            )
            await self._conn.execute(
                f"DELETE FROM mobile_refresh_tokens WHERE agent_id IN ({admin_agent_ids_sql})"
            )
            await self._conn.execute(
                f"DELETE FROM pairing_tokens WHERE agent_id IN ({admin_agent_ids_sql})"
            )
            # Legacy `pairings` cleanup. The table is dropped entirely by
            # migration 13, so guard against its absence here — fresh DBs
            # only see migration 13 and the table never existed.
            pairings_exists = await self._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='pairings'"
            )
            if await pairings_exists.fetchone() is not None:
                await self._conn.execute(
                    f"DELETE FROM pairings WHERE agent_id IN ({admin_agent_ids_sql})"
                )
            await self._conn.execute(
                "DELETE FROM agents WHERE owner_user_id IN "
                "(SELECT id FROM users WHERE role = 'admin')"
            )
            await self._conn.execute("PRAGMA user_version = 4")

        if version < 5:
            # Add email_verified + verified_at to users; pre-existing rows are
            # grandfathered to email_verified=1 to avoid locking out accounts
            # created before this migration. Also creates the
            # email_verification_tokens table.
            col_cursor = await self._conn.execute("PRAGMA table_info(users)")
            user_cols = {r[1] for r in await col_cursor.fetchall()}
            if "email_verified" not in user_cols:
                await self._conn.execute(
                    "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0"
                )
                # Backfill existing users to verified so they can still log in.
                await self._conn.execute("UPDATE users SET email_verified = 1")
            if "verified_at" not in user_cols:
                await self._conn.execute(
                    "ALTER TABLE users ADD COLUMN verified_at TEXT"
                )
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 5")

        if version < 6:
            # Pure additive migration: only creates the new prompt_templates
            # table and its index. No ALTER on existing tables, so this is
            # safe and idempotent on populated dev databases.
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 6")

        if version < 7:
            # Relax pairing_tokens.agent_id NOT NULL so
            # rows can exist before the agent row does (agent is created at
            # claim time). Add claimed_token_raw / claimed_agent_id /
            # agent_metadata columns to drive the agent-initiated flow.
            #
            # SQLite cannot ALTER the NOT NULL constraint in place. Build a
            # new table with the relaxed schema, copy data, drop the old.
            # Idempotent: only runs the rebuild when the legacy NOT NULL
            # constraint is still present.
            pti_cursor = await self._conn.execute(
                "PRAGMA table_info(pairing_tokens)"
            )
            pti_rows = await pti_cursor.fetchall()
            pt_cols = {r[1]: r for r in pti_rows}
            agent_id_notnull = (
                "agent_id" in pt_cols and pt_cols["agent_id"][3] == 1
            )
            if agent_id_notnull:
                await self._conn.execute(
                    "CREATE TABLE pairing_tokens_new ("
                    "token_hash TEXT PRIMARY KEY, "
                    "agent_id TEXT REFERENCES agents(agent_id), "
                    "created_at TEXT NOT NULL, "
                    "expires_at TEXT NOT NULL, "
                    "consumed_at TEXT, "
                    "consumed_by TEXT, "
                    "claimed_token_raw TEXT, "
                    "claimed_agent_id TEXT REFERENCES agents(agent_id), "
                    "agent_metadata TEXT)"
                )
                await self._conn.execute(
                    "INSERT INTO pairing_tokens_new "
                    "(token_hash, agent_id, created_at, expires_at, "
                    "consumed_at, consumed_by) "
                    "SELECT token_hash, agent_id, created_at, expires_at, "
                    "consumed_at, consumed_by FROM pairing_tokens"
                )
                await self._conn.execute("DROP TABLE pairing_tokens")
                await self._conn.execute(
                    "ALTER TABLE pairing_tokens_new RENAME TO pairing_tokens"
                )
            else:
                # Fresh DBs already have the relaxed schema from _SCHEMA.
                # Add the three new columns if any are still missing.
                for col_name, col_ddl in (
                    ("claimed_token_raw", "TEXT"),
                    ("claimed_agent_id", "TEXT REFERENCES agents(agent_id)"),
                    ("agent_metadata", "TEXT"),
                ):
                    if col_name not in pt_cols:
                        await self._conn.execute(
                            "ALTER TABLE pairing_tokens "
                            f"ADD COLUMN {col_name} {col_ddl}"
                        )
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 7")

        if version < 8:
            # Persist the relay_url chosen at /api/pairing/initiate time
            # so the claim handler and the WS recovery handler both
            # echo the SAME url in the credential push payload. Before
            # this column existed the recovery branch recomputed
            # relay_url from ``websocket.url.scheme`` (wss → https),
            # while the initiate / claim branches recomputed it from
            # ``request.base_url`` (often http behind a reverse proxy
            # that doesn't forward X-Forwarded-Proto to uvicorn). The
            # agent enforced strict equality on the two values; a
            # mismatch silently consumed the credentials without
            # delivering them. Now both branches read the column.
            #
            # Pure additive ALTER. Idempotent: only adds the column
            # when missing. Legacy rows have relay_url = NULL; the
            # router's compatibility branch recomputes the URL for
            # those (limited blast radius — only nonces issued before
            # this upgrade).
            pti_cursor = await self._conn.execute(
                "PRAGMA table_info(pairing_tokens)"
            )
            pti_rows = await pti_cursor.fetchall()
            existing_cols = {r[1] for r in pti_rows}
            if "relay_url" not in existing_cols:
                await self._conn.execute(
                    "ALTER TABLE pairing_tokens ADD COLUMN relay_url TEXT"
                )
            await self._conn.execute("PRAGMA user_version = 8")

        if version < 9:
            # Persist the agent_version / agent_protocol_version that the
            # agent reports in its WebSocket auth handshake so the dashboard
            # Details page can surface them — and so the values survive
            # an agent disconnect (the read site is a synchronous template
            # query, not an in-memory routing-table lookup).
            #
            # Pure additive ALTER. Idempotent: only adds each column when
            # missing. Legacy rows are NULL until the agent next
            # reconnects, at which point ``update_agent_handshake_info``
            # backfills.
            col_cursor = await self._conn.execute("PRAGMA table_info(agents)")
            existing_agent_cols = {r[1] for r in await col_cursor.fetchall()}
            if "agent_version" not in existing_agent_cols:
                await self._conn.execute(
                    "ALTER TABLE agents ADD COLUMN agent_version TEXT"
                )
            if "agent_protocol_version" not in existing_agent_cols:
                await self._conn.execute(
                    "ALTER TABLE agents "
                    "ADD COLUMN agent_protocol_version INTEGER"
                )
            await self._conn.execute("PRAGMA user_version = 9")

        if version < 10:
            # Pure additive migration: creates password_reset_tokens table +
            # partial index. Safe and idempotent on populated dev DBs (mirrors
            # the version-6 block above).
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 10")

        if version < 11:
            # Account self-deletion: adds scheduled_deletion_at +
            # deletion_requested_at to users, plus the deletion_audit and
            # account_deletion_cancel_tokens tables. The audit table has no FK
            # on user_id because rows must outlive cascade-purge of the user
            # they reference.
            col_cursor = await self._conn.execute("PRAGMA table_info(users)")
            user_cols = {r[1] for r in await col_cursor.fetchall()}
            if "scheduled_deletion_at" not in user_cols:
                await self._conn.execute(
                    "ALTER TABLE users ADD COLUMN scheduled_deletion_at TEXT"
                )
            if "deletion_requested_at" not in user_cols:
                await self._conn.execute(
                    "ALTER TABLE users ADD COLUMN deletion_requested_at TEXT"
                )
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 11")

        if version < 12:
            # Add deletion_audit.mode so we can distinguish scheduled (14-day
            # grace) from immediate self-delete. Existing rows backfill to
            # 'scheduled' via the column default. SQLite ALTER TABLE cannot
            # attach a CHECK constraint, so the CHECK lives in _SCHEMA only
            # (fresh DBs); upgrades rely on application-level validation in
            # record_deletion_audit + DeleteAccountRequest.
            audit_cursor = await self._conn.execute(
                "PRAGMA table_info(deletion_audit)"
            )
            audit_cols = {r[1] for r in await audit_cursor.fetchall()}
            if "mode" not in audit_cols:
                await self._conn.execute(
                    "ALTER TABLE deletion_audit "
                    "ADD COLUMN mode TEXT NOT NULL DEFAULT 'scheduled'"
                )
            await self._conn.execute("PRAGMA user_version = 12")

        if version < 13:
            # The old `pairings` table conflated user-id with device-id and
            # was only ever populated by tests. Drop it and replace with
            # `mobile_devices`, keyed by a client-generated UUID per Android
            # install. Pre-production, so `mobile_refresh_tokens` rows are
            # dropped too — every paired device gets signed out on next
            # refresh, then re-pairs and populates a fresh row with the
            # correct device id.
            await self._conn.execute("DROP INDEX IF EXISTS idx_pairings_agent")
            await self._conn.execute("DROP TABLE IF EXISTS pairings")
            await self._conn.execute("DELETE FROM mobile_refresh_tokens")
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 13")

        if version < 14:
            # Sign in with Apple: additive users.apple_sub column; the unique
            # partial index comes from _SCHEMA (ALTER TABLE cannot add UNIQUE).
            col_cursor = await self._conn.execute("PRAGMA table_info(users)")
            user_cols = {r[1] for r in await col_cursor.fetchall()}
            if "apple_sub" not in user_cols:
                await self._conn.execute(
                    "ALTER TABLE users ADD COLUMN apple_sub TEXT"
                )
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 14")

        if version < 15:
            # APNs push: additive mobile_devices columns for the
            # device platform and its push token/environment.
            dev_cursor = await self._conn.execute(
                "PRAGMA table_info(mobile_devices)"
            )
            dev_cols = {r[1] for r in await dev_cursor.fetchall()}
            for column in ("platform", "push_token", "push_environment"):
                if column not in dev_cols:
                    await self._conn.execute(
                        f"ALTER TABLE mobile_devices ADD COLUMN {column} TEXT"
                    )
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 15")

        if version < 16:
            # Additive: user_shortcuts table + partial owner/dedupe indexes
            # (saved keyboard shortcuts). Pure _SCHEMA re-run.
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 16")

        if version < 17:
            # Additive: custom_agent_types table + partial owner/name
            # indexes (account-scoped custom AI agent types). Pure
            # _SCHEMA re-run.
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 17")

        if version < 18:
            # Additive: user_refresh_tokens table + partial owner index
            # (browser-session refresh tokens for the opt-in refreshable
            # user session). Pure _SCHEMA re-run.
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 18")

        if version < 19:
            # Device-code pairing + account-level trust pin. The new table
            # is a pure _SCHEMA re-run, but ``users.trust_key_fp`` has to be
            # ALTERed in first: an upgraded DB reaches the CREATE TABLE
            # statements only after the column exists, and no index in
            # _SCHEMA may reference a column that does not (same reason the
            # apple_sub guard is hoisted above).
            user_cursor = await self._conn.execute("PRAGMA table_info(users)")
            user_cols = {r[1] for r in await user_cursor.fetchall()}
            if "trust_key_fp" not in user_cols:
                await self._conn.execute(
                    "ALTER TABLE users ADD COLUMN trust_key_fp TEXT"
                )
            # ``approved_at`` landed after the table did, so a database
            # created by an earlier build of this same version has the table
            # without the column — CREATE TABLE IF NOT EXISTS would not add
            # it. Empty table_info means the table does not exist yet and
            # _SCHEMA below creates it complete.
            code_cursor = await self._conn.execute(
                "PRAGMA table_info(pairing_device_codes)"
            )
            code_cols = {r[1] for r in await code_cursor.fetchall()}
            if code_cols and "approved_at" not in code_cols:
                await self._conn.execute(
                    "ALTER TABLE pairing_device_codes ADD COLUMN approved_at TEXT"
                )
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 19")

        if version < 20:
            # Google Ads attribution + first-pairing marker on users. The
            # columns themselves were ALTERed in by the hoisted guard above
            # (they must predate every _SCHEMA re-run, not just this one);
            # this block owns the index, the backfill and the version bump.
            await self._conn.executescript(_SCHEMA)
            # Accounts that paired before the marker existed must not read
            # as first-time on their next claim. created_at is a stand-in;
            # only non-NULL matters.
            await self._conn.execute(
                "UPDATE users SET first_paired_at = created_at "
                "WHERE first_paired_at IS NULL AND (trust_key_fp IS NOT NULL "
                "OR id IN (SELECT owner_user_id FROM agents "
                "WHERE owner_user_id IS NOT NULL))"
            )
            await self._conn.execute("PRAGMA user_version = 20")

        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    _USER_COLUMNS = (
        "id, email, password_hash, google_sub, apple_sub, role, "
        "email_verified, verified_at, created_at, updated_at, "
        "scheduled_deletion_at, deletion_requested_at, trust_key_fp, "
        "ad_click_id, ad_click_kind, ad_click_at, ads_consent, "
        "ad_attribution_sig, first_paired_at"
    )

    async def create_user(
        self,
        email: str,
        password_hash: str | None = None,
        google_sub: str | None = None,
        apple_sub: str | None = None,
        role: str = "viewer",
        email_verified: bool = False,
        ad_click_id: str | None = None,
        ad_click_kind: str | None = None,
        ad_click_at: str | None = None,
        ads_consent: bool = False,
        ad_attribution_sig: str | None = None,
    ) -> str:
        """Create a new user. Returns user_id.

        ``email_verified=True`` is used by the admin seeder and by the
        Google/Apple OAuth paths (when the provider vouches for the address),
        which all bypass the OTP flow. The ``ad_*`` / ``ads_consent`` columns
        are written here only — attribution is a fact about how the account
        came to exist, never updated afterwards.
        """
        user_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        verified_at = now if email_verified else None
        await self.conn.execute(
            "INSERT INTO users "
            "(id, email, password_hash, google_sub, apple_sub, role, "
            "email_verified, verified_at, created_at, updated_at, "
            "ad_click_id, ad_click_kind, ad_click_at, ads_consent, "
            "ad_attribution_sig) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                email,
                password_hash,
                google_sub,
                apple_sub,
                role,
                1 if email_verified else 0,
                verified_at,
                now,
                now,
                ad_click_id,
                ad_click_kind,
                ad_click_at,
                1 if ads_consent else 0,
                ad_attribution_sig,
            ),
        )
        await self.conn.commit()
        return user_id

    async def get_user_by_email(self, email: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._USER_COLUMNS} FROM users WHERE email = ?",
            (email,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_by_google_sub(self, google_sub: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._USER_COLUMNS} FROM users WHERE google_sub = ?",
            (google_sub,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._USER_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def link_google_to_user(self, user_id: str, google_sub: str) -> None:
        """Link a Google account to an existing user."""
        now = _now_iso()
        await self.conn.execute(
            "UPDATE users SET google_sub = ?, updated_at = ? WHERE id = ?",
            (google_sub, now, user_id),
        )
        await self.conn.commit()

    async def get_user_by_apple_sub(self, apple_sub: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._USER_COLUMNS} FROM users WHERE apple_sub = ?",
            (apple_sub,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def link_apple_to_user(self, user_id: str, apple_sub: str) -> None:
        """Link an Apple account to an existing user."""
        now = _now_iso()
        await self.conn.execute(
            "UPDATE users SET apple_sub = ?, updated_at = ? WHERE id = ?",
            (apple_sub, now, user_id),
        )
        await self.conn.commit()

    async def mark_user_verified(self, user_id: str) -> None:
        """Mark a user as email-verified (idempotent)."""
        now = _now_iso()
        await self.conn.execute(
            "UPDATE users "
            "SET email_verified = 1, "
            "    verified_at = COALESCE(verified_at, ?), "
            "    updated_at = ? "
            "WHERE id = ?",
            (now, now, user_id),
        )
        await self.conn.commit()

    async def register_agent(self, name: str, owner_user_id: str) -> tuple[str, str]:
        """Register a new user-owned agent. Returns (agent_id, raw_token)."""
        agent_id = uuid.uuid4().hex[:12]
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        now = _now_iso()
        await self.conn.execute(
            "INSERT INTO agents (agent_id, name, token_hash, owner_user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (agent_id, name, token_hash, owner_user_id, now),
        )
        await self.conn.commit()
        return agent_id, raw_token

    async def verify_agent_token(self, agent_id: str, raw_token: str) -> bool:
        """Verify an agent's authentication token (SHA-256 comparison)."""
        cursor = await self.conn.execute(
            "SELECT token_hash FROM agents WHERE agent_id = ?", (agent_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        return secrets.compare_digest(_hash_token(raw_token), row["token_hash"])

    async def reregister_agent(self, agent_id: str) -> str:
        """Regenerate agent credentials. Returns new raw_token."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        await self.conn.execute(
            "UPDATE agents SET token_hash = ? WHERE agent_id = ?",
            (token_hash, agent_id),
        )
        await self.conn.commit()
        return raw_token

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent and cascade to pairing tokens and refresh tokens.

        ``pairing_tokens`` is referenced from ``agents`` via two FK columns
        (``agent_id`` for the legacy flow and ``claimed_agent_id`` for the
        QR-pairing post-claim row that survives cleanup for forensics).
        Both must be cleared before the ``agents`` DELETE, otherwise SQLite
        raises an FK constraint violation under ``PRAGMA foreign_keys=ON``.

        ``mobile_devices`` is user-scoped — devices outlive any specific
        agent — so it is intentionally not touched here.
        """
        await self.conn.execute(
            "DELETE FROM mobile_refresh_tokens WHERE agent_id = ?", (agent_id,)
        )
        await self.conn.execute(
            "DELETE FROM pairing_tokens "
            "WHERE agent_id = ? OR claimed_agent_id = ?",
            (agent_id, agent_id),
        )
        cursor = await self.conn.execute(
            "DELETE FROM agents WHERE agent_id = ?", (agent_id,)
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def delete_user(self, user_id: str) -> list[str]:
        """Delete a user and cascade-delete every owned agent + dependent row.

        Mirrors ``delete_agent``'s explicit-cascade pattern because
        ``PRAGMA foreign_keys=ON`` cascades are per-connection in SQLite
        and the dashboard needs the deleted agent IDs *before* the rows
        vanish so it can tear down their WebSocket connections.

        Order (single implicit transaction, one commit at the end):
          1. Snapshot owned agent IDs.
          2. Drop ``mobile_refresh_tokens`` / ``pairing_tokens`` rows that
             reference any of those agents.
          3. Drop the ``agents`` rows themselves.
          4. Drop ``email_verification_tokens``, ``password_reset_tokens``,
             ``account_deletion_cancel_tokens``, ``user_refresh_tokens``,
             ``mobile_devices``, ``prompt_templates``, ``user_shortcuts``,
             ``custom_agent_types`` and ``pairing_device_codes`` owned by
             the user.
          5. Drop the ``users`` row.

        ``deletion_audit`` rows are intentionally NOT cascaded — they have no
        FK on ``user_id`` and must outlive the user (Art. 17 audit trail).

        Returns the list of deleted ``agent_id``s (empty list if the user
        had none). Returns an empty list if ``user_id`` does not exist.
        """
        cursor = await self.conn.execute(
            "SELECT agent_id FROM agents WHERE owner_user_id = ?", (user_id,)
        )
        agent_ids = [row["agent_id"] async for row in cursor]

        owned_agents_subquery = (
            "SELECT agent_id FROM agents WHERE owner_user_id = ?"
        )
        await self.conn.execute(
            f"DELETE FROM mobile_refresh_tokens "
            f"WHERE agent_id IN ({owned_agents_subquery})",
            (user_id,),
        )
        await self.conn.execute(
            f"DELETE FROM pairing_tokens "
            f"WHERE agent_id IN ({owned_agents_subquery}) "
            f"   OR claimed_agent_id IN ({owned_agents_subquery})",
            (user_id, user_id),
        )
        await self.conn.execute(
            "DELETE FROM agents WHERE owner_user_id = ?", (user_id,)
        )
        await self.conn.execute(
            "DELETE FROM email_verification_tokens WHERE user_id = ?",
            (user_id,),
        )
        await self.conn.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ?",
            (user_id,),
        )
        await self.conn.execute(
            "DELETE FROM account_deletion_cancel_tokens WHERE user_id = ?",
            (user_id,),
        )
        await self.conn.execute(
            "DELETE FROM user_refresh_tokens WHERE user_id = ?", (user_id,)
        )
        await self.conn.execute(
            "DELETE FROM mobile_devices WHERE user_id = ?", (user_id,)
        )
        await self.conn.execute(
            "DELETE FROM prompt_templates WHERE owner_user_id = ?", (user_id,)
        )
        await self.conn.execute(
            "DELETE FROM user_shortcuts WHERE owner_user_id = ?", (user_id,)
        )
        await self.conn.execute(
            "DELETE FROM custom_agent_types WHERE owner_user_id = ?",
            (user_id,),
        )
        await self.conn.execute(
            "DELETE FROM pairing_device_codes WHERE user_id = ?", (user_id,)
        )
        await self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await self.conn.commit()
        return agent_ids

    _AGENT_COLUMNS = (
        "agent_id, name, owner_user_id, created_at, last_seen_at, "
        "agent_version, agent_protocol_version"
    )

    async def list_agents(self) -> list[dict]:
        """List all agents (admin view)."""
        cursor = await self.conn.execute(
            f"SELECT {self._AGENT_COLUMNS} FROM agents ORDER BY created_at DESC"
        )
        return [dict(row) async for row in cursor]

    async def list_agents_with_owner(self) -> list[dict]:
        """List all agents with owner email (admin dashboard view)."""
        cursor = await self.conn.execute(
            "SELECT a.agent_id, a.name, a.owner_user_id, a.created_at, a.last_seen_at, "
            "a.agent_version, a.agent_protocol_version, "
            "u.email AS owner_email "
            "FROM agents a LEFT JOIN users u ON a.owner_user_id = u.id "
            "ORDER BY a.created_at DESC"
        )
        return [dict(row) async for row in cursor]

    async def count_users(
        self, *, q: str = "", verified: bool | None = None
    ) -> int:
        """Count users matching the same filters ``list_users_with_agents``
        takes, so a paged listing can report its total."""
        where, params = _users_filter(q, verified)
        cursor = await self.conn.execute(
            f"SELECT COUNT(*) FROM users u{where}", params
        )
        row = await cursor.fetchone()
        return int(row[0])

    async def list_users_with_agents(
        self,
        *,
        q: str = "",
        verified: bool | None = None,
        sort: str = "registered",
        descending: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """List users joined with their owned agents in a single query,
        optionally filtered, sorted and paged.

        Returns one dict per user with an ``agents`` key holding the list
        of agent records (empty list if the user owns none). Each user's
        agents are newest-agent-first. One SQL round-trip; grouping done
        in Python.

        ``q`` / ``verified`` are the filters documented on
        ``_users_filter``. ``sort`` is ``"registered"`` (``users.created_at``)
        or ``"activity"`` (``MAX(agents.last_seen_at)`` per user, computed in
        the CTE only for that sort; ``NULL`` for users whose agents were
        never seen, which SQLite orders lowest — first ascending, last
        descending). ``descending`` applies to the sort key and to the
        ``created_at`` / ``id`` tiebreaks that make the order total, so a
        page boundary can never split or repeat a user. The defaults
        reproduce the pre-paging order, newest user first.

        ``limit`` / ``offset`` page *users*, not join rows: a CTE picks the
        page of user ids first and the agents are joined onto that page.
        ``limit=None`` returns every match (SQLite's ``LIMIT -1``).

        Shape::

            [{
                "id": str, "email": str, "role": str,
                "email_verified": int (0|1), "verified_at": str | None,
                "created_at": str,
                "agents": [{
                    "agent_id": str, "name": str, "created_at": str,
                    "last_seen_at": str | None,
                    "agent_version": str | None,
                    "agent_protocol_version": int | None,
                }, ...],
            }, ...]
        """
        try:
            key = _USERS_SORT_KEYS[sort]
        except KeyError:
            raise ValueError(f"Unknown users sort key: {sort!r}") from None
        direction = "DESC" if descending else "ASC"
        order = f"{key} {direction}, u.created_at {direction}, u.id {direction}"
        where, params = _users_filter(q, verified)
        # The correlated MAX is one index probe per matching user, paid
        # before the LIMIT, so it is only added when the sort needs it.
        activity = _USERS_ACTIVITY_COLUMN if sort == "activity" else ""
        cursor = await self.conn.execute(
            "WITH page AS ("
            f"SELECT u.id{activity} "
            f"FROM users u{where} ORDER BY {order} LIMIT ? OFFSET ?) "
            "SELECT u.id AS user_id, u.email, u.role, "
            "u.email_verified, u.verified_at, "
            "u.created_at AS user_created_at, "
            "a.agent_id, a.name AS agent_name, "
            "a.created_at AS agent_created_at, a.last_seen_at, "
            "a.agent_version, a.agent_protocol_version "
            "FROM page JOIN users u ON u.id = page.id "
            "LEFT JOIN agents a ON a.owner_user_id = u.id "
            f"ORDER BY {order}, a.created_at DESC",
            [*params, -1 if limit is None else limit, offset],
        )
        users: list[dict] = []
        by_id: dict[str, dict] = {}
        async for row in cursor:
            uid = row["user_id"]
            user = by_id.get(uid)
            if user is None:
                user = {
                    "id": uid,
                    "email": row["email"],
                    "role": row["role"],
                    "email_verified": row["email_verified"],
                    "verified_at": row["verified_at"],
                    "created_at": row["user_created_at"],
                    "agents": [],
                }
                by_id[uid] = user
                users.append(user)
            if row["agent_id"] is not None:
                user["agents"].append(
                    {
                        "agent_id": row["agent_id"],
                        "name": row["agent_name"],
                        "created_at": row["agent_created_at"],
                        "last_seen_at": row["last_seen_at"],
                        "agent_version": row["agent_version"],
                        "agent_protocol_version": row[
                            "agent_protocol_version"
                        ],
                    }
                )
        return users

    async def list_agents_for_user(self, user_id: str) -> list[dict]:
        """List agents owned by a specific user."""
        cursor = await self.conn.execute(
            f"SELECT {self._AGENT_COLUMNS} "
            "FROM agents WHERE owner_user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(row) async for row in cursor]

    async def get_agent(self, agent_id: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._AGENT_COLUMNS} FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def is_agent_owner(self, agent_id: str, user_id: str) -> bool:
        """Check if a user owns the given agent."""
        cursor = await self.conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ? AND owner_user_id = ?",
            (agent_id, user_id),
        )
        return await cursor.fetchone() is not None

    async def get_agent_owner(self, agent_id: str) -> str | None:
        """Return the owner user_id for the given agent, or None if unknown."""
        cursor = await self.conn.execute(
            "SELECT owner_user_id FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = await cursor.fetchone()
        return row["owner_user_id"] if row else None

    async def touch_agent_last_seen(
        self, agent_id: str, last_seen: datetime | None = None
    ) -> None:
        ts = last_seen.isoformat() if last_seen is not None else _now_iso()
        await self.conn.execute(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id = ?",
            (ts, agent_id),
        )
        await self.conn.commit()

    async def bulk_touch_agents_last_seen(
        self, heartbeats: list[tuple[str, datetime]]
    ) -> None:
        """Persist ``(agent_id, last_heartbeat)`` pairs in one transaction.

        Rows whose agent was deleted after the caller snapshotted its list
        are harmless no-ops.
        """
        if not heartbeats:
            return
        await self.conn.executemany(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id = ?",
            [(ts.isoformat(), agent_id) for agent_id, ts in heartbeats],
        )
        await self.conn.commit()

    async def update_agent_handshake_info(
        self,
        agent_id: str,
        agent_version: str | None,
        agent_protocol_version: int | None,
    ) -> None:
        """Record a successful agent handshake: bump ``last_seen_at`` and
        persist the version + protocol-version reported in the auth message.

        Coalesces the three writes that the connect path used to issue as
        two separate UPDATE+commit calls (``touch_agent_last_seen`` followed
        by a version-only UPDATE) into a single statement and a single
        ``commit()`` — one fsync per reconnect instead of two.

        Each of ``agent_version`` / ``agent_protocol_version`` is a no-op
        when the caller passes ``None`` (e.g., the agent omitted the field
        from its auth message), so a stale write never clobbers a
        previously-stored value. ``last_seen_at`` is always refreshed.
        """
        await self.conn.execute(
            "UPDATE agents "
            "SET last_seen_at = ?, "
            "    agent_version = COALESCE(?, agent_version), "
            "    agent_protocol_version = COALESCE(?, agent_protocol_version) "
            "WHERE agent_id = ?",
            (_now_iso(), agent_version, agent_protocol_version, agent_id),
        )
        await self.conn.commit()

    _TEMPLATE_COLUMNS = (
        "template_id, owner_user_id, name, body, is_favorite, "
        "last_used_at, created_at, updated_at, deleted_at, version"
    )

    @staticmethod
    def _row_to_template(row: aiosqlite.Row) -> dict:
        d = dict(row)
        d["is_favorite"] = bool(d["is_favorite"])
        return d

    async def create_template(
        self, owner_user_id: str, name: str, body: str
    ) -> dict:
        """Create a new prompt template owned by ``owner_user_id``."""
        template_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        await self.conn.execute(
            "INSERT INTO prompt_templates "
            "(template_id, owner_user_id, name, body, is_favorite, "
            "last_used_at, created_at, updated_at, deleted_at, version) "
            "VALUES (?, ?, ?, ?, 0, NULL, ?, ?, NULL, 1)",
            (template_id, owner_user_id, name, body, now, now),
        )
        await self.conn.commit()
        row = await self.get_template(template_id)
        assert row is not None
        return row

    async def get_template(self, template_id: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._TEMPLATE_COLUMNS} "
            "FROM prompt_templates WHERE template_id = ?",
            (template_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_template(row) if row else None

    async def _live_template_or_none(self, template_id: str) -> dict | None:
        """Read a template back after a mutation, treating a tombstone as absent.

        ``get_template`` has no ``deleted_at`` filter, so a soft-delete landing
        between a guarded UPDATE's commit and this readback would otherwise let
        the service broadcast a deleted row as ``template.updated`` —
        resurrecting it on every connected device. Returning ``None`` makes
        callers treat the template as gone.
        """
        row = await self.get_template(template_id)
        if row is None or row["deleted_at"] is not None:
            return None
        return row

    async def list_templates_for_user(self, user_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            f"SELECT {self._TEMPLATE_COLUMNS} "
            "FROM prompt_templates "
            "WHERE owner_user_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        return [self._row_to_template(row) async for row in cursor]

    async def is_template_owner(
        self, template_id: str, user_id: str
    ) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM prompt_templates "
            "WHERE template_id = ? AND owner_user_id = ? "
            "  AND deleted_at IS NULL",
            (template_id, user_id),
        )
        return await cursor.fetchone() is not None

    async def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        body: str | None = None,
        is_favorite: bool | None = None,
    ) -> dict | None:
        """Partial update. Bumps ``updated_at`` and ``version`` when at least
        one field is provided. Returns the updated row, or None if the
        template doesn't exist or has been soft-deleted."""
        sets: list[str] = []
        params: list[object] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if body is not None:
            sets.append("body = ?")
            params.append(body)
        if is_favorite is not None:
            sets.append("is_favorite = ?")
            params.append(1 if is_favorite else 0)
        if not sets:
            return await self._live_template_or_none(template_id)
        sets.append("updated_at = ?")
        params.append(_now_iso())
        sets.append("version = version + 1")
        params.append(template_id)
        cursor = await self.conn.execute(
            f"UPDATE prompt_templates SET {', '.join(sets)} "
            "WHERE template_id = ? AND deleted_at IS NULL",
            tuple(params),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        return await self._live_template_or_none(template_id)

    async def soft_delete_template(self, template_id: str) -> dict | None:
        """Mark a template as deleted. Returns the tombstone row (with
        ``deleted_at`` and bumped ``version``/``updated_at``) or None if it
        was already gone."""
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE prompt_templates "
            "SET deleted_at = ?, updated_at = ?, version = version + 1 "
            "WHERE template_id = ? AND deleted_at IS NULL",
            (now, now, template_id),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        return await self.get_template(template_id)

    async def touch_template(self, template_id: str) -> dict | None:
        """Bump ``last_used_at`` (and ``updated_at`` + ``version``) on the
        template. Used when a template is loaded into the input bar via the
        picker."""
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE prompt_templates "
            "SET last_used_at = ?, updated_at = ?, version = version + 1 "
            "WHERE template_id = ? AND deleted_at IS NULL",
            (now, now, template_id),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        return await self._live_template_or_none(template_id)

    _SHORTCUT_COLUMNS = (
        "shortcut_id, owner_user_id, kind, chord_token, name, position, "
        "created_at, updated_at, deleted_at, version"
    )

    async def create_user_shortcut(
        self,
        owner_user_id: str,
        kind: str,
        chord_token: str,
        name: str | None,
    ) -> dict:
        """Create a saved shortcut owned by ``owner_user_id``. ``position``
        is assigned as MAX(position)+1 among the owner's live rows of the
        same kind so new buttons append to the end of the control row.

        Raises ``sqlite3.IntegrityError`` when a live row with the same
        (owner, kind, chord_token) already exists (partial unique index);
        the service layer resolves that into upsert semantics."""
        shortcut_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        try:
            await self.conn.execute(
                "INSERT INTO user_shortcuts "
                "(shortcut_id, owner_user_id, kind, chord_token, name, "
                "position, created_at, updated_at, deleted_at, version) "
                "VALUES (?, ?, ?, ?, ?, "
                "(SELECT COALESCE(MAX(position) + 1, 0) FROM user_shortcuts "
                " WHERE owner_user_id = ? AND kind = ? AND deleted_at IS NULL), "
                "?, ?, NULL, 1)",
                (
                    shortcut_id, owner_user_id, kind, chord_token, name,
                    owner_user_id, kind, now, now,
                ),
            )
        except sqlite3.IntegrityError:
            # Same open-transaction hazard as create_custom_agent_type: the
            # upsert retry loop in ShortcutService can return without ever
            # committing after a lost race, which would otherwise leave the
            # aborted INSERT's implicit transaction pinning the connection.
            await self.conn.rollback()
            raise
        await self.conn.commit()
        row = await self.get_user_shortcut(shortcut_id)
        assert row is not None
        return row

    async def get_user_shortcut(self, shortcut_id: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._SHORTCUT_COLUMNS} "
            "FROM user_shortcuts WHERE shortcut_id = ?",
            (shortcut_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_live_user_shortcut_by_chord(
        self, owner_user_id: str, kind: str, chord_token: str
    ) -> dict | None:
        """Look up the owner's live row for a (kind, chord_token) pair —
        the upsert probe."""
        cursor = await self.conn.execute(
            f"SELECT {self._SHORTCUT_COLUMNS} "
            "FROM user_shortcuts "
            "WHERE owner_user_id = ? AND kind = ? AND chord_token = ? "
            "  AND deleted_at IS NULL",
            (owner_user_id, kind, chord_token),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_user_shortcuts_for_user(self, user_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            f"SELECT {self._SHORTCUT_COLUMNS} "
            "FROM user_shortcuts "
            "WHERE owner_user_id = ? AND deleted_at IS NULL "
            "ORDER BY position ASC, created_at ASC, shortcut_id ASC",
            (user_id,),
        )
        return [dict(row) async for row in cursor]

    async def is_user_shortcut_owner(
        self, shortcut_id: str, user_id: str
    ) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM user_shortcuts "
            "WHERE shortcut_id = ? AND owner_user_id = ? "
            "  AND deleted_at IS NULL",
            (shortcut_id, user_id),
        )
        return await cursor.fetchone() is not None

    async def update_user_shortcut_name(
        self, shortcut_id: str, name: str | None
    ) -> dict | None:
        """Set (or clear, with None) the display name. Bumps ``updated_at``
        and ``version``. Returns the updated row, or None if the shortcut
        doesn't exist or has been soft-deleted."""
        cursor = await self.conn.execute(
            "UPDATE user_shortcuts "
            "SET name = ?, updated_at = ?, version = version + 1 "
            "WHERE shortcut_id = ? AND deleted_at IS NULL",
            (name, _now_iso(), shortcut_id),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        row = await self.get_user_shortcut(shortcut_id)
        if row is None or row["deleted_at"] is not None:
            # A concurrent soft-delete can land between the UPDATE commit
            # and this readback. The readback has no deleted_at filter, so
            # returning the tombstone here would let the service broadcast
            # a deleted row as shortcut.updated — resurrecting the button
            # on every connected device.
            return None
        return row

    async def soft_delete_user_shortcut(
        self, shortcut_id: str
    ) -> dict | None:
        """Mark a shortcut as deleted. Returns the tombstone row (with
        ``deleted_at`` and bumped ``version``/``updated_at``) or None if it
        was already gone."""
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE user_shortcuts "
            "SET deleted_at = ?, updated_at = ?, version = version + 1 "
            "WHERE shortcut_id = ? AND deleted_at IS NULL",
            (now, now, shortcut_id),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        return await self.get_user_shortcut(shortcut_id)

    _CUSTOM_TYPE_COLUMNS = (
        "id, owner_user_id, name, command, created_at, updated_at, deleted_at"
    )

    async def create_custom_agent_type(
        self,
        owner_user_id: str,
        name: str,
        command: str,
        *,
        cap: int = 20,
    ) -> dict | None:
        """Create a custom agent type, enforcing the per-account cap in the
        same statement (INSERT ... SELECT ... WHERE COUNT < cap). aiosqlite
        serializes statements, not await sequences, so a separate
        COUNT-then-INSERT pair could interleave with another create and
        overshoot the cap; the single statement cannot.

        Returns the created row, or None when the owner already has
        ``cap`` live types. Raises ``sqlite3.IntegrityError`` when a live
        row with the same name (case-insensitive) exists for the owner
        (partial unique index)."""
        type_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        try:
            cursor = await self.conn.execute(
                "INSERT INTO custom_agent_types "
                "(id, owner_user_id, name, command, created_at, updated_at, "
                "deleted_at) "
                "SELECT ?, ?, ?, ?, ?, ?, NULL "
                "WHERE (SELECT COUNT(*) FROM custom_agent_types "
                "       WHERE owner_user_id = ? AND deleted_at IS NULL) < ? "
                "RETURNING id",
                (type_id, owner_user_id, name, command, now, now,
                 owner_user_id, cap),
            )
        except sqlite3.IntegrityError:
            # The aborted INSERT leaves the connection's implicit write
            # transaction open (legacy transaction mode); without a
            # rollback it would pin the WAL snapshot and fold the next
            # unrelated write into a stale transaction.
            await self.conn.rollback()
            raise
        # RETURNING rows must be consumed before commit() resets the
        # pending statement.
        inserted = await cursor.fetchone()
        await self.conn.commit()
        if inserted is None:
            return None
        row = await self.get_custom_agent_type(type_id)
        assert row is not None
        return row

    async def get_custom_agent_type(self, type_id: str) -> dict | None:
        cursor = await self.conn.execute(
            f"SELECT {self._CUSTOM_TYPE_COLUMNS} "
            "FROM custom_agent_types WHERE id = ?",
            (type_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_live_custom_agent_type_for_owner(
        self, type_id: str, owner_user_id: str
    ) -> dict | None:
        """Owner-scoped live lookup — the relay's launch-resolution probe."""
        cursor = await self.conn.execute(
            f"SELECT {self._CUSTOM_TYPE_COLUMNS} "
            "FROM custom_agent_types "
            "WHERE id = ? AND owner_user_id = ? AND deleted_at IS NULL",
            (type_id, owner_user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_custom_agent_types_for_user(
        self, user_id: str
    ) -> list[dict]:
        cursor = await self.conn.execute(
            f"SELECT {self._CUSTOM_TYPE_COLUMNS} "
            "FROM custom_agent_types "
            "WHERE owner_user_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at ASC, id ASC",
            (user_id,),
        )
        return [dict(row) async for row in cursor]

    async def update_custom_agent_type(
        self,
        type_id: str,
        owner_user_id: str,
        *,
        name: str | None = None,
        command: str | None = None,
    ) -> dict | None:
        """Update name and/or command (None = leave unchanged; both columns
        are NOT NULL so COALESCE is safe). Bumps ``updated_at``. Returns the
        updated row, or None when the type doesn't exist, is soft-deleted,
        or is owned by someone else. Raises ``sqlite3.IntegrityError`` when
        the new name collides case-insensitively with another live type."""
        try:
            cursor = await self.conn.execute(
                "UPDATE custom_agent_types "
                "SET name = COALESCE(?, name), "
                "    command = COALESCE(?, command), "
                "    updated_at = ? "
                "WHERE id = ? AND owner_user_id = ? AND deleted_at IS NULL",
                (name, command, _now_iso(), type_id, owner_user_id),
            )
        except sqlite3.IntegrityError:
            # See create_custom_agent_type: roll the implicit write
            # transaction back so the aborted UPDATE cannot pin the
            # connection in an open-transaction state.
            await self.conn.rollback()
            raise
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        row = await self.get_custom_agent_type(type_id)
        if row is None or row["deleted_at"] is not None:
            # Concurrent soft-delete between the UPDATE commit and this
            # readback — never hand a tombstone to the service, it would
            # broadcast a deleted type as custom_type.updated.
            return None
        return row

    async def soft_delete_custom_agent_type(
        self, type_id: str, owner_user_id: str
    ) -> dict | None:
        """Tombstone a custom agent type AND soft-delete the owner's live
        saved shortcuts scoped to its ``custom_<id>`` kind, in one
        transaction (single commit). The shortcut rows get the same
        ``version`` bump semantics as ``soft_delete_user_shortcut`` so
        client-side version reconciliation stays coherent; the service
        deliberately broadcasts only one ``custom_type.deleted`` event
        (clients purge their local shortcut caches for the kind).

        Returns the type tombstone row, or None if the type was already
        gone or is owned by someone else."""
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE custom_agent_types "
            "SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND owner_user_id = ? AND deleted_at IS NULL",
            (now, now, type_id, owner_user_id),
        )
        if cursor.rowcount != 1:
            await self.conn.commit()
            return None
        await self.conn.execute(
            "UPDATE user_shortcuts "
            "SET deleted_at = ?, updated_at = ?, version = version + 1 "
            "WHERE owner_user_id = ? AND kind = ? AND deleted_at IS NULL",
            (now, now, owner_user_id, f"custom_{type_id}"),
        )
        await self.conn.commit()
        return await self.get_custom_agent_type(type_id)

    async def create_pairing_token(
        self,
        agent_id: str | None = None,
        ttl_seconds: int = 300,
        agent_metadata: str | None = None,
        relay_url: str | None = None,
    ) -> tuple[str, str]:
        """Create a pairing token. Returns (raw_token, expires_at ISO).

        ``agent_id`` is None for the QR pairing flow: the agent row
        is created at claim time. ``agent_metadata`` is a JSON string blob
        carrying initiate-time hints (hostname, os, current_agent_id) used
        by the claim handler to distinguish first-pair from re-pair.

        ``relay_url`` is the public base URL the agent will see in the
        claim push. Persisted at initiate time so the claim and
        WS-recovery branches both read the same value rather than each
        recomputing it from a request context that can disagree behind
        a reverse proxy.
        """
        result = await self._insert_pairing_token(
            agent_id=agent_id,
            ttl_seconds=ttl_seconds,
            agent_metadata=agent_metadata,
            relay_url=relay_url,
        )
        await self.conn.commit()
        return result

    async def _insert_pairing_token(
        self,
        agent_id: str | None = None,
        ttl_seconds: int = 300,
        agent_metadata: str | None = None,
        relay_url: str | None = None,
    ) -> tuple[str, str]:
        """``create_pairing_token`` without the commit.

        The device-code redeem mints its nonce inside the same transaction
        as the code's CAS, so the two either both land or neither does.
        """
        raw_token = secrets.token_urlsafe(24)  # 192 bits
        token_hash = _hash_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        await self.conn.execute(
            "INSERT INTO pairing_tokens "
            "(token_hash, agent_id, created_at, expires_at, agent_metadata, relay_url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                token_hash,
                agent_id,
                now.isoformat(),
                expires_at.isoformat(),
                agent_metadata,
                relay_url,
            ),
        )
        return raw_token, expires_at.isoformat()

    async def consume_pairing_token(
        self, raw_token: str, mobile_device_id: str
    ) -> str | None:
        """Atomic compare-and-swap: consume a pairing token.

        Returns agent_id on success, None if token is invalid/expired/consumed.
        """
        token_hash = _hash_token(raw_token)
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE pairing_tokens "
            "SET consumed_at = ?, consumed_by = ? "
            "WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?",
            (now, mobile_device_id, token_hash, now),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        cursor2 = await self.conn.execute(
            "SELECT agent_id FROM pairing_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        row = await cursor2.fetchone()
        return row["agent_id"] if row else None

    async def lookup_pairing_token_metadata(
        self, raw_token: str
    ) -> dict | None:
        """Read pre-claim state for a QR pairing nonce.

        Returns a dict with ``token_hash, expires_at, agent_metadata,
        consumed_at, relay_url`` or None if the row does not exist. Used
        by the claim handler before CAS to inspect ``agent_metadata``
        (which encodes re-pair vs. first-pair intent) and the persisted
        ``relay_url`` (echoed in the credential push), and by the WS
        handler to validate nonce existence + TTL and read the same
        persisted ``relay_url`` for the recovery push.
        """
        return await self.lookup_pairing_token_metadata_by_hash(
            _hash_token(raw_token)
        )

    async def lookup_pairing_token_metadata_by_hash(
        self, token_hash: str
    ) -> dict | None:
        """``lookup_pairing_token_metadata`` for a caller that has the hash.

        The device-code confirm path never sees the raw nonce — the code
        row stores only ``pairing_nonce_hash`` — so it addresses the token
        row directly.
        """
        cursor = await self.conn.execute(
            "SELECT token_hash, expires_at, agent_metadata, consumed_at, relay_url "
            "FROM pairing_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def claim_pairing_nonce(
        self,
        raw_token: str,
        mobile_device_id: str,
        claimed_agent_id: str,
        claimed_token_raw: str,
    ) -> bool:
        """Atomic CAS that consumes the nonce AND records the claim outcome.

        Combines ``consume_pairing_token``'s consume-only-if-unconsumed CAS
        with the persistence of ``claimed_agent_id`` / ``claimed_token_raw``
        so the row carries the credentials the WS push will deliver. The
        single UPDATE-with-WHERE is the concurrency boundary; two parallel
        claims race here and exactly one wins.

        Returns True on success, False if the row was already consumed,
        expired, or never existed.
        """
        return await self.claim_pairing_nonce_by_hash(
            token_hash=_hash_token(raw_token),
            mobile_device_id=mobile_device_id,
            claimed_agent_id=claimed_agent_id,
            claimed_token_raw=claimed_token_raw,
        )

    async def claim_pairing_nonce_by_hash(
        self,
        token_hash: str,
        mobile_device_id: str,
        claimed_agent_id: str,
        claimed_token_raw: str,
    ) -> bool:
        """``claim_pairing_nonce`` for a caller that has the hash."""
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE pairing_tokens "
            "SET consumed_at = ?, "
            "    consumed_by = ?, "
            "    claimed_agent_id = ?, "
            "    claimed_token_raw = ? "
            "WHERE token_hash = ? "
            "  AND consumed_at IS NULL "
            "  AND expires_at > ?",
            (
                now,
                mobile_device_id,
                claimed_agent_id,
                claimed_token_raw,
                token_hash,
                now,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def read_and_clear_claimed_credentials(
        self, raw_token: str
    ) -> dict | None:
        """Single-flight read of credentials to deliver, then clear.

        Atomically reads ``(claimed_agent_id, claimed_token_raw,
        relay_url)`` for a consumed row AND clears ``claimed_token_raw``
        to NULL so a second reader (e.g., a reconnecting WS) cannot
        re-deliver. Returns None if the row is not consumed, has no
        claimed token, or doesn't exist.

        Note: ``claimed_agent_id`` and ``relay_url`` remain populated
        for forensics / replay; only the raw token is cleared at rest.
        ``relay_url`` is the one persisted at initiate time and is the
        single source of truth across the dispatch and recovery paths.
        """
        token_hash = _hash_token(raw_token)
        cursor = await self.conn.execute(
            "SELECT claimed_agent_id, claimed_token_raw, relay_url "
            "FROM pairing_tokens "
            "WHERE token_hash = ? "
            "  AND consumed_at IS NOT NULL "
            "  AND claimed_token_raw IS NOT NULL",
            (token_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        agent_id = row["claimed_agent_id"]
        raw = row["claimed_token_raw"]
        relay_url = row["relay_url"]
        clear_cursor = await self.conn.execute(
            "UPDATE pairing_tokens SET claimed_token_raw = NULL "
            "WHERE token_hash = ? AND claimed_token_raw IS NOT NULL",
            (token_hash,),
        )
        await self.conn.commit()
        if clear_cursor.rowcount != 1:
            return None
        return {
            "claimed_agent_id": agent_id,
            "claimed_token_raw": raw,
            "relay_url": relay_url,
        }

    async def create_device_code(
        self,
        request_id: str,
        code_hash: str,
        user_id: str,
        ttl_seconds: int,
    ) -> str:
        """Mint a device code, retiring the account's earlier live ones.

        One live code per account is a guessing-surface control as much as
        a UX one: the ~34.6-bit code space is only comfortable while the
        set of valid codes stays tiny. Superseded rows are marked canceled
        rather than deleted so a redeem of a stale code reports "canceled"
        instead of the indistinguishable "unknown".

        Returns the ``expires_at`` ISO string.
        """
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        await self.conn.execute(
            "UPDATE pairing_device_codes SET canceled_at = ? "
            "WHERE user_id = ? AND redeemed_at IS NULL AND canceled_at IS NULL",
            (now.isoformat(), user_id),
        )
        await self.conn.execute(
            "INSERT INTO pairing_device_codes "
            "(request_id, code_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                request_id,
                code_hash,
                user_id,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        await self.conn.commit()
        return expires_at.isoformat()

    async def redeem_device_code(
        self,
        code_hash: str,
        redeemer_id: str,
        hostname: str | None,
        os_name: str | None,
        current_agent_id: str | None,
        agent_metadata: str | None,
        nonce_ttl_seconds: int,
        relay_url: str | None,
    ) -> dict | None:
        """Bind a code to the redeeming host and mint its pairing nonce.

        Returns ``{request_id, user_id, nonce, expires_at, replayed}``, or
        ``None`` when the code is unknown, expired, canceled, or already
        held by a *different* redeemer.

        Recognises a retry from the same ``redeemer_id`` instead of
        rejecting it as a second machine racing the code, returning
        ``replayed=True``. Note what that does *not* buy: the raw nonce is
        never stored server-side, so a replay comes back with
        ``nonce=None`` and the caller can only continue if it still holds
        its own local copy. A redeem whose response was lost in transit is
        therefore not recoverable — the operator has to mint a new code.

        The claim itself is a single CAS ``UPDATE``, matching
        ``claim_pairing_nonce``: two hosts racing the same code resolve to
        exactly one winner.
        """
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE pairing_device_codes "
            "SET redeemed_at = ?, "
            "    redeemer_id = ?, "
            "    redeem_hostname = ?, "
            "    redeem_os = ?, "
            "    redeem_agent_id = ? "
            "WHERE code_hash = ? "
            "  AND redeemed_at IS NULL "
            "  AND canceled_at IS NULL "
            "  AND expires_at > ?",
            (
                now,
                redeemer_id,
                hostname,
                os_name,
                current_agent_id,
                code_hash,
                now,
            ),
        )
        if cursor.rowcount != 1:
            await self.conn.commit()
            return await self._replay_device_code_redeem(code_hash, redeemer_id)

        raw_nonce, expires_at = await self._insert_pairing_token(
            ttl_seconds=nonce_ttl_seconds,
            agent_metadata=agent_metadata,
            relay_url=relay_url,
        )
        # Guarded on ``canceled_at``, not just the code hash. Minting the
        # token is a separate statement from the CAS above, so a DELETE can
        # land in between: it would read ``pairing_nonce_hash`` still NULL,
        # cancel the request, and expire nothing — and an unguarded link
        # here would then attach a live nonce to a canceled row, leaving the
        # agent waiting out the full confirm TTL for a confirm that can only
        # ever 410. Losing the guard rolls the nonce back with it.
        row_cursor = await self.conn.execute(
            "UPDATE pairing_device_codes SET pairing_nonce_hash = ? "
            "WHERE code_hash = ? AND redeemer_id = ? AND canceled_at IS NULL",
            (_hash_token(raw_nonce), code_hash, redeemer_id),
        )
        if row_cursor.rowcount != 1:
            await self.conn.rollback()
            return None
        await self.conn.commit()
        lookup = await self.conn.execute(
            "SELECT request_id, user_id FROM pairing_device_codes "
            "WHERE code_hash = ?",
            (code_hash,),
        )
        row = await lookup.fetchone()
        if row is None:  # pragma: no cover - row just updated
            return None
        return {
            "request_id": row["request_id"],
            "user_id": row["user_id"],
            "nonce": raw_nonce,
            "expires_at": expires_at,
            "replayed": False,
        }

    async def _replay_device_code_redeem(
        self, code_hash: str, redeemer_id: str
    ) -> dict | None:
        """Re-answer a redeem the same host already won, else ``None``.

        The caller gets the *hash* back and resolves the WebSocket URL from
        the nonce it persisted locally before the first attempt.
        """
        cursor = await self.conn.execute(
            "SELECT request_id, user_id, pairing_nonce_hash, redeemer_id, "
            "       canceled_at, confirmed_at "
            "FROM pairing_device_codes WHERE code_hash = ?",
            (code_hash,),
        )
        row = await cursor.fetchone()
        if row is None or row["redeemer_id"] != redeemer_id:
            return None
        if row["canceled_at"] is not None or row["confirmed_at"] is not None:
            return None
        if row["pairing_nonce_hash"] is None:
            return None
        token = await self.lookup_pairing_token_metadata_by_hash(
            row["pairing_nonce_hash"]
        )
        if token is None or token["consumed_at"] is not None:
            return None
        return {
            "request_id": row["request_id"],
            "user_id": row["user_id"],
            "nonce": None,
            "expires_at": token["expires_at"],
            "replayed": True,
        }

    async def get_device_code(
        self, request_id: str, user_id: str
    ) -> dict | None:
        """Read an owned device-code row, or ``None`` for a foreign/absent id."""
        cursor = await self.conn.execute(
            "SELECT request_id, user_id, created_at, expires_at, redeemed_at, "
            "       redeem_hostname, redeem_os, redeem_agent_id, approved_at, "
            "       pairing_nonce_hash, confirmed_at, canceled_at "
            "FROM pairing_device_codes WHERE request_id = ? AND user_id = ?",
            (request_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def approve_device_code(
        self, code_hash: str, redeemer_id: str
    ) -> bool:
        """Record that the redeeming host's operator said yes.

        Anonymous like the redeem, and authorised the same way: only the
        machine holding both the code and the ``redeemer_id`` it redeemed
        under can approve. Idempotent, so a resumed ``pair`` run can send it
        again without having to know whether the first one landed.
        """
        cursor = await self.conn.execute(
            "UPDATE pairing_device_codes SET approved_at = ? "
            "WHERE code_hash = ? AND redeemer_id = ? "
            "  AND redeemed_at IS NOT NULL "
            "  AND approved_at IS NULL "
            "  AND confirmed_at IS NULL "
            "  AND canceled_at IS NULL",
            (_now_iso(), code_hash, redeemer_id),
        )
        await self.conn.commit()
        if cursor.rowcount == 1:
            return True
        lookup = await self.conn.execute(
            "SELECT approved_at, canceled_at FROM pairing_device_codes "
            "WHERE code_hash = ? AND redeemer_id = ?",
            (code_hash, redeemer_id),
        )
        row = await lookup.fetchone()
        return bool(row and row["approved_at"] and not row["canceled_at"])

    async def mark_device_code_confirmed(
        self, request_id: str, user_id: str
    ) -> bool:
        """CAS a redeemed code to confirmed. False if it already moved on.

        The confirm endpoint runs this *before* issuing credentials, so
        this statement and :meth:`cancel_device_code`'s — single statements
        over the same connection, guarded on the same two columns — are
        what makes confirm and cancel mutually exclusive. A confirm that is
        then refused calls :meth:`reopen_device_code`.
        """
        cursor = await self.conn.execute(
            "UPDATE pairing_device_codes SET confirmed_at = ? "
            "WHERE request_id = ? AND user_id = ? "
            "  AND redeemed_at IS NOT NULL "
            "  AND confirmed_at IS NULL "
            "  AND canceled_at IS NULL",
            (_now_iso(), request_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def reopen_device_code(self, request_id: str, user_id: str) -> bool:
        """Undo a confirm reservation whose pairing was then refused.

        Only the reserving request can reach this, and a cancel cannot have
        landed in between — cancel's CAS requires ``confirmed_at IS NULL``,
        which the reservation held for the whole attempt.
        """
        cursor = await self.conn.execute(
            "UPDATE pairing_device_codes SET confirmed_at = NULL "
            "WHERE request_id = ? AND user_id = ? "
            "  AND confirmed_at IS NOT NULL "
            "  AND canceled_at IS NULL",
            (request_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def cancel_device_code(self, request_id: str, user_id: str) -> bool:
        """Cancel an owned code and expire its nonce in the same transaction.

        A confirm racing this loses on the ``confirmed_at IS NULL AND
        canceled_at IS NULL`` guard below, which it reserves through
        :meth:`mark_device_code_confirmed` before issuing any credentials.

        Expiring the ``pairing_tokens`` row rather than leaving it to time
        out is what stops the agent's WebSocket hanging for the rest of the
        window: it gives up at its next TTL check.
        """
        now = _now_iso()
        cursor = await self.conn.execute(
            "SELECT pairing_nonce_hash FROM pairing_device_codes "
            "WHERE request_id = ? AND user_id = ? "
            "  AND confirmed_at IS NULL AND canceled_at IS NULL",
            (request_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        update = await self.conn.execute(
            "UPDATE pairing_device_codes SET canceled_at = ? "
            "WHERE request_id = ? AND user_id = ? "
            "  AND confirmed_at IS NULL AND canceled_at IS NULL",
            (now, request_id, user_id),
        )
        if update.rowcount != 1:
            await self.conn.commit()
            return False
        if row["pairing_nonce_hash"] is not None:
            await self.conn.execute(
                "UPDATE pairing_tokens SET expires_at = ? "
                "WHERE token_hash = ? AND consumed_at IS NULL",
                (now, row["pairing_nonce_hash"]),
            )
        await self.conn.commit()
        return True

    async def get_user_trust_key_fp(self, user_id: str) -> str | None:
        cursor = await self.conn.execute(
            "SELECT trust_key_fp FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row["trust_key_fp"] if row else None

    async def pin_user_trust_key_fp(self, user_id: str, fingerprint: str) -> bool:
        """Take the account's trust pin if it is still unset.

        Two first pairings racing produce one winner and one no-op, and
        neither can overwrite an established pin.
        """
        cursor = await self.conn.execute(
            "UPDATE users SET trust_key_fp = ?, updated_at = ? "
            "WHERE id = ? AND trust_key_fp IS NULL",
            (fingerprint, _now_iso(), user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def clear_user_trust_key_fp(self, user_id: str) -> bool:
        """Drop the account's trust pin (recovery after a key rotation)."""
        cursor = await self.conn.execute(
            "UPDATE users SET trust_key_fp = NULL, updated_at = ? "
            "WHERE id = ? AND trust_key_fp IS NOT NULL",
            (_now_iso(), user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def mark_user_first_paired(self, user_id: str) -> bool:
        """Stamp the account's first-ever pairing; True for exactly one caller.

        Same compare-and-set shape as the trust pin, but never cleared: the
        pin resets when an account drops to zero agents, and the "Agent
        paired" conversion must fire once per account, not once per epoch.
        """
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE users SET first_paired_at = ?, updated_at = ? "
            "WHERE id = ? AND first_paired_at IS NULL",
            (now, now, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def clear_user_ad_attribution(self, user_id: str) -> bool:
        """Drop the sign-up click attribution once it has served its purpose."""
        cursor = await self.conn.execute(
            "UPDATE users SET ad_click_id = NULL, ad_click_kind = NULL, "
            "ad_click_at = NULL, ads_consent = 0, ad_attribution_sig = NULL, "
            "updated_at = ? "
            "WHERE id = ? AND (ads_consent = 1 OR ad_click_id IS NOT NULL)",
            (_now_iso(), user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def withdraw_ad_attribution(self, signature: str) -> int:
        """Decline on the landing page: drop the attribution from every account
        created with the cookie carrying ``signature``. Returns the count."""
        cursor = await self.conn.execute(
            "UPDATE users SET ad_click_id = NULL, ad_click_kind = NULL, "
            "ad_click_at = NULL, ads_consent = 0, ad_attribution_sig = NULL, "
            "updated_at = ? WHERE ad_attribution_sig = ?",
            (_now_iso(), signature),
        )
        await self.conn.commit()
        return cursor.rowcount

    _AD_ATTRIBUTION_RETENTION_DAYS = 90

    async def purge_stale_ad_attribution(self) -> int:
        """Enforce the 90-day retention the privacy policy promises.

        Age runs from the click or from sign-up, whichever is earlier: the
        click timestamp comes from the visitor's clock (the request model
        accepts values decades out), so the server-stamped ``created_at`` is
        the latest reference retention may use. ISO strings compare
        lexicographically, as everywhere else in this file.
        """
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self._AD_ATTRIBUTION_RETENTION_DAYS)).isoformat()
        cursor = await self.conn.execute(
            "UPDATE users SET ad_click_id = NULL, ad_click_kind = NULL, "
            "ad_click_at = NULL, ads_consent = 0, ad_attribution_sig = NULL, "
            "updated_at = ? "
            "WHERE (ads_consent = 1 OR ad_click_id IS NOT NULL) "
            "AND (created_at < ? OR ad_click_at < ?)",
            (now.isoformat(), cutoff, cutoff),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def list_users_with_pending_ad_conversion(self) -> list[str]:
        """Accounts whose first pairing landed but whose upload never finished."""
        cursor = await self.conn.execute(
            "SELECT id FROM users WHERE first_paired_at IS NOT NULL "
            "AND (ads_consent = 1 OR ad_click_id IS NOT NULL)"
        )
        rows = await cursor.fetchall()
        return [row["id"] for row in rows]

    async def count_agents_for_owner(self, user_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM agents WHERE owner_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def get_pending_pairing_tokens(self, agent_id: str) -> list[dict]:
        now = _now_iso()
        cursor = await self.conn.execute(
            "SELECT token_hash, created_at, expires_at "
            "FROM pairing_tokens "
            "WHERE agent_id = ? AND consumed_at IS NULL AND expires_at > ? "
            "ORDER BY created_at DESC",
            (agent_id, now),
        )
        return [dict(row) async for row in cursor]

    # Retention window for consumed verification-token rows. Kept deliberately
    # long enough (24h) to help with live-incident forensics but short enough
    # that stale code hashes don't pile up as a brute-force surface on a DB leak.
    _VERIFICATION_TOKEN_RETENTION_SECONDS = 24 * 3600

    # A successful WS delivery clears `claimed_token_raw` synchronously,
    # but the queue path can fail
    # (no waiter, dropped connection) leaving the raw token persisted.
    # The 60s retention is generous enough that a transient WS reconnect
    # within the QR's TTL recovers the credentials, but bounded so a raw
    # token is never durably at rest.
    _PAIRING_CLAIMED_TOKEN_RETENTION_SECONDS = 60

    async def cleanup_expired_tokens(self) -> int:
        """Delete expired pairing, device-code, email-verification,
        password-reset and user-session refresh tokens; clear stale claimed
        pairing-token rows.

        Verification tokens are cleaned in two sweeps:
          - expired but never consumed rows are dropped immediately;
          - successfully consumed rows are dropped after the retention
            window so old code hashes don't linger as brute-force fodder
            on a DB dump.

        For QR pairing, a third sweep nulls
        ``pairing_tokens.claimed_token_raw`` for consumed rows older than
        the claimed-token retention window. The row itself is kept (audit
        trail), but the literal agent token is wiped so a subsequent DB
        leak does not yield a usable RELAY_AGENT_TOKEN.

        Returns the total number of deleted rows across all sweeps. The
        third sweep contributes its rowcount when it null-updates a row.
        """
        now = _now_iso()
        retention_cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self._VERIFICATION_TOKEN_RETENTION_SECONDS)
        ).isoformat()
        pairing_token_cutoff = (
            datetime.now(timezone.utc)
            - timedelta(
                seconds=self._PAIRING_CLAIMED_TOKEN_RETENTION_SECONDS
            )
        ).isoformat()
        pairing_cursor = await self.conn.execute(
            "DELETE FROM pairing_tokens WHERE expires_at <= ? AND consumed_at IS NULL",
            (now,),
        )
        expired_cursor = await self.conn.execute(
            "DELETE FROM email_verification_tokens "
            "WHERE expires_at <= ? AND consumed_at IS NULL",
            (now,),
        )
        consumed_cursor = await self.conn.execute(
            "DELETE FROM email_verification_tokens "
            "WHERE consumed_at IS NOT NULL AND consumed_at <= ?",
            (retention_cutoff,),
        )
        # Password-reset tokens follow the same two-sweep policy as
        # verification tokens (expired-unconsumed dropped immediately, then
        # consumed rows after the retention window).
        reset_expired_cursor = await self.conn.execute(
            "DELETE FROM password_reset_tokens "
            "WHERE expires_at <= ? AND consumed_at IS NULL",
            (now,),
        )
        reset_consumed_cursor = await self.conn.execute(
            "DELETE FROM password_reset_tokens "
            "WHERE consumed_at IS NOT NULL AND consumed_at <= ?",
            (retention_cutoff,),
        )
        # User-session refresh tokens rotate on every use, so unlike
        # ``mobile_refresh_tokens`` (whose default TTL is "never expires", one
        # row per pairing) this table gains a row per refresh — several per
        # hour per open browser session. Without a sweep it grows without
        # bound. Deleting a revoked row is not observable: a replay misses the
        # ``revoked_at IS NULL`` predicate either way.
        user_refresh_expired_cursor = await self.conn.execute(
            "DELETE FROM user_refresh_tokens "
            "WHERE expires_at <= ? AND revoked_at IS NULL",
            (now,),
        )
        user_refresh_revoked_cursor = await self.conn.execute(
            "DELETE FROM user_refresh_tokens "
            "WHERE revoked_at IS NOT NULL AND revoked_at <= ?",
            (retention_cutoff,),
        )
        claimed_clear_cursor = await self.conn.execute(
            "UPDATE pairing_tokens SET claimed_token_raw = NULL "
            "WHERE consumed_at IS NOT NULL "
            "  AND claimed_token_raw IS NOT NULL "
            "  AND consumed_at <= ?",
            (pairing_token_cutoff,),
        )
        # Device codes carry a peppered hash of a ~34.6-bit secret, so a
        # settled row is pure brute-force fodder on a dump. Unredeemed rows
        # go as soon as they expire; settled ones (confirmed or canceled)
        # follow the same retention window as consumed OTP rows.
        device_code_expired_cursor = await self.conn.execute(
            "DELETE FROM pairing_device_codes "
            "WHERE expires_at <= ? AND redeemed_at IS NULL",
            (now,),
        )
        device_code_settled_cursor = await self.conn.execute(
            "DELETE FROM pairing_device_codes "
            "WHERE (confirmed_at IS NOT NULL AND confirmed_at <= ?) "
            "   OR (canceled_at IS NOT NULL AND canceled_at <= ?)",
            (retention_cutoff, retention_cutoff),
        )
        # Redeemed but never settled: the operator answered no at the
        # agent's prompt, the agent's WebSocket died, the confirm was
        # refused by the ownership guard, or the browser tab was simply
        # closed. None of those paths notifies the relay, so without this
        # sweep the row — and its code hash — would live forever. The
        # linked nonce is dead long before the retention cutoff, so this
        # can never delete a request that is still completable.
        device_code_stranded_cursor = await self.conn.execute(
            "DELETE FROM pairing_device_codes "
            "WHERE redeemed_at IS NOT NULL "
            "  AND confirmed_at IS NULL "
            "  AND canceled_at IS NULL "
            "  AND redeemed_at <= ?",
            (retention_cutoff,),
        )
        await self.conn.commit()
        return (
            pairing_cursor.rowcount
            + expired_cursor.rowcount
            + consumed_cursor.rowcount
            + reset_expired_cursor.rowcount
            + reset_consumed_cursor.rowcount
            + user_refresh_expired_cursor.rowcount
            + user_refresh_revoked_cursor.rowcount
            + claimed_clear_cursor.rowcount
            + device_code_expired_cursor.rowcount
            + device_code_settled_cursor.rowcount
            + device_code_stranded_cursor.rowcount
        )

    async def create_verification_token(
        self,
        user_id: str,
        code_hash: str,
        ttl_seconds: int,
    ) -> str:
        """Insert a new verification token. Returns the ``expires_at`` ISO string.

        The caller supplies the already-hashed code (HMAC-SHA256 with a
        server-side pepper). Any outstanding unconsumed tokens for this
        user are invalidated first so only one live code exists at a time —
        resend semantics MUST issue a fresh code.
        """
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        now_iso = now.isoformat()
        await self.conn.execute(
            "UPDATE email_verification_tokens "
            "SET consumed_at = ? "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (now_iso, user_id),
        )
        await self.conn.execute(
            "INSERT INTO email_verification_tokens "
            "(token_hash, user_id, created_at, expires_at, attempts) "
            "VALUES (?, ?, ?, ?, 0)",
            (code_hash, user_id, now_iso, expires_at.isoformat()),
        )
        await self.conn.commit()
        return expires_at.isoformat()

    async def consume_verification_token(
        self,
        user_id: str,
        code_hash: str,
        max_attempts: int,
    ) -> str:
        """Atomically consume a verification token for ``user_id``.

        Returns one of:
          - ``"ok"``        — code matched, token now consumed.
          - ``"invalid"``   — no matching unconsumed unexpired token.
          - ``"expired"``   — a token exists for the user but is past TTL.
          - ``"exhausted"`` — the user's active token has hit ``max_attempts``.

        On ``"invalid"`` we also bump the ``attempts`` counter of the user's
        active token so that brute-force tries count toward the cap.
        """
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE email_verification_tokens "
            "SET consumed_at = ? "
            "WHERE token_hash = ? AND user_id = ? "
            "  AND consumed_at IS NULL AND expires_at > ? AND attempts < ?",
            (now, code_hash, user_id, now, max_attempts),
        )
        await self.conn.commit()
        if cursor.rowcount == 1:
            return "ok"

        # Miss — figure out why by looking at the active token (if any).
        active_cursor = await self.conn.execute(
            "SELECT expires_at, attempts FROM email_verification_tokens "
            "WHERE user_id = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        active = await active_cursor.fetchone()
        if active is None:
            return "invalid"
        if active["expires_at"] <= now:
            return "expired"
        if active["attempts"] >= max_attempts:
            return "exhausted"
        # Token exists but the supplied hash doesn't match — count the attempt.
        await self.conn.execute(
            "UPDATE email_verification_tokens "
            "SET attempts = attempts + 1 "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (user_id,),
        )
        await self.conn.commit()
        return "invalid"

    async def invalidate_verification_tokens_for_user(self, user_id: str) -> None:
        """Mark all active verification tokens for a user as consumed (used on resend)."""
        now = _now_iso()
        await self.conn.execute(
            "UPDATE email_verification_tokens "
            "SET consumed_at = ? "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (now, user_id),
        )
        await self.conn.commit()

    async def create_password_reset_token(
        self,
        user_id: str,
        code_hash: str,
        ttl_seconds: int,
    ) -> str:
        """Insert a new password-reset token. Returns ``expires_at`` ISO string.

        Mirrors ``create_verification_token``: any outstanding unconsumed reset
        tokens for this user are invalidated first so only one live code
        exists at a time. The caller supplies the already-hashed code
        (HMAC-SHA256 with the JWT-secret pepper).
        """
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        now_iso = now.isoformat()
        await self.conn.execute(
            "UPDATE password_reset_tokens "
            "SET consumed_at = ? "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (now_iso, user_id),
        )
        await self.conn.execute(
            "INSERT INTO password_reset_tokens "
            "(token_hash, user_id, created_at, expires_at, attempts) "
            "VALUES (?, ?, ?, ?, 0)",
            (code_hash, user_id, now_iso, expires_at.isoformat()),
        )
        await self.conn.commit()
        return expires_at.isoformat()

    async def consume_password_reset_token(
        self,
        user_id: str,
        code_hash: str,
        max_attempts: int,
    ) -> str:
        """Atomically consume a password-reset token for ``user_id``.

        Returns ``"ok"`` / ``"invalid"`` / ``"expired"`` / ``"exhausted"``
        with the same semantics as ``consume_verification_token`` — single
        atomic CAS, attempt counter bumped on miss, no TOCTOU.
        """
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE password_reset_tokens "
            "SET consumed_at = ? "
            "WHERE token_hash = ? AND user_id = ? "
            "  AND consumed_at IS NULL AND expires_at > ? AND attempts < ?",
            (now, code_hash, user_id, now, max_attempts),
        )
        await self.conn.commit()
        if cursor.rowcount == 1:
            return "ok"

        active_cursor = await self.conn.execute(
            "SELECT expires_at, attempts FROM password_reset_tokens "
            "WHERE user_id = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        active = await active_cursor.fetchone()
        if active is None:
            return "invalid"
        if active["expires_at"] <= now:
            return "expired"
        if active["attempts"] >= max_attempts:
            return "exhausted"
        await self.conn.execute(
            "UPDATE password_reset_tokens "
            "SET attempts = attempts + 1 "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (user_id,),
        )
        await self.conn.commit()
        return "invalid"

    async def update_user_password(
        self, user_id: str, password_hash: str
    ) -> None:
        """Replace the stored Argon2id hash for a user."""
        now = _now_iso()
        await self.conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, user_id),
        )
        await self.conn.commit()

    async def revoke_all_refresh_tokens_for_user(self, user_id: str) -> int:
        """Logically revoke every active mobile refresh token tied to this user.

        ``mobile_refresh_tokens`` has no direct ``user_id`` column — sessions
        are scoped to ``(mobile_device_id, agent_id)`` pairs. We join through
        ``agents.owner_user_id`` (indexed) to find every agent the user owns
        and revoke any of its outstanding sessions. Returns the rowcount.

        Logical revocation (``revoked_at`` set) rather than DELETE preserves
        the audit trail and matches the existing ``revoke_mobile_tokens``
        helper.
        """
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE mobile_refresh_tokens SET revoked_at = ? "
            "WHERE revoked_at IS NULL "
            "  AND agent_id IN (SELECT agent_id FROM agents WHERE owner_user_id = ?)",
            (now, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def set_scheduled_deletion(
        self,
        user_id: str,
        *,
        scheduled_for: str,
        requested_at: str,
    ) -> None:
        """Mark a user for soft-delete on the given ISO timestamp.

        Idempotent — re-running with a later ``scheduled_for`` extends the
        window. The caller is responsible for writing a fresh
        ``deletion_audit`` row alongside this update.
        """
        now = _now_iso()
        await self.conn.execute(
            "UPDATE users "
            "SET scheduled_deletion_at = ?, "
            "    deletion_requested_at = ?, "
            "    updated_at = ? "
            "WHERE id = ?",
            (scheduled_for, requested_at, now, user_id),
        )
        await self.conn.commit()

    async def clear_scheduled_deletion(self, user_id: str) -> None:
        """Cancel a pending soft-delete. Idempotent."""
        now = _now_iso()
        await self.conn.execute(
            "UPDATE users "
            "SET scheduled_deletion_at = NULL, "
            "    deletion_requested_at = NULL, "
            "    updated_at = ? "
            "WHERE id = ?",
            (now, user_id),
        )
        await self.conn.commit()

    async def list_users_due_for_purge(self, now_iso: str) -> list[dict]:
        """Return users whose scheduled deletion has elapsed.

        Each row carries ``id``, ``email``, and ``scheduled_deletion_at`` so
        the sweeper can re-check the timestamp atomically per row (race-safe
        against a concurrent cancel that lands between scan and delete).
        """
        cursor = await self.conn.execute(
            "SELECT id, email, scheduled_deletion_at "
            "FROM users "
            "WHERE scheduled_deletion_at IS NOT NULL "
            "  AND scheduled_deletion_at <= ?",
            (now_iso,),
        )
        return [dict(row) async for row in cursor]

    async def record_deletion_audit(
        self,
        *,
        user_id: str,
        email_at_request: str,
        requested_at: str,
        scheduled_for: str,
        source: str,
        mode: str = "scheduled",
    ) -> int:
        """Insert a new ``deletion_audit`` row in ``pending`` state.

        ``mode`` is ``'scheduled'`` (14-day grace) or ``'immediate'`` (cascade
        purge now). Returns the row's auto-increment ``id`` so the caller can
        later transition it via ``update_deletion_audit_status``.
        """
        if mode not in ("scheduled", "immediate"):
            raise ValueError(f"Invalid deletion mode: {mode!r}")
        cursor = await self.conn.execute(
            "INSERT INTO deletion_audit "
            "(user_id, email_at_request, requested_at, scheduled_for, source, status, mode) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, email_at_request, requested_at, scheduled_for, source, mode),
        )
        await self.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def update_deletion_audit_status(
        self,
        audit_id: int,
        status: str,
        *,
        completed_at: str | None = None,
        cancellation_reason: str | None = None,
    ) -> None:
        """Transition a deletion audit row to a terminal state."""
        await self.conn.execute(
            "UPDATE deletion_audit "
            "SET status = ?, "
            "    completed_at = COALESCE(?, completed_at), "
            "    cancellation_reason = COALESCE(?, cancellation_reason) "
            "WHERE id = ?",
            (status, completed_at, cancellation_reason, audit_id),
        )
        await self.conn.commit()

    async def find_pending_audit_for_user(self, user_id: str) -> dict | None:
        """Return the most-recent ``pending`` audit row for the user, if any.

        Used by ``cancel_account_deletion`` to stamp ``cancelled`` and by the
        sweeper to look up the audit row before purge.
        """
        cursor = await self.conn.execute(
            "SELECT id, user_id, email_at_request, requested_at, scheduled_for, "
            "       source, status, mode, completed_at, cancellation_reason "
            "FROM deletion_audit "
            "WHERE user_id = ? AND status = 'pending' "
            "ORDER BY requested_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_deletion_cancel_token(
        self, user_id: str, ttl_seconds: int
    ) -> str:
        """Issue a single-use cancel-deletion token for the email link.

        Returns the raw token; only its SHA-256 hash is persisted. Mirrors
        ``create_password_reset_token``'s "invalidate active tokens, then
        insert" pattern so only one live cancel token exists per user.
        """
        raw = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw)
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        now_iso = now.isoformat()
        await self.conn.execute(
            "UPDATE account_deletion_cancel_tokens "
            "SET consumed_at = ? "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (now_iso, user_id),
        )
        await self.conn.execute(
            "INSERT INTO account_deletion_cancel_tokens "
            "(token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, user_id, now_iso, expires_at.isoformat()),
        )
        await self.conn.commit()
        return raw

    async def consume_deletion_cancel_token(self, raw_token: str) -> str | None:
        """Atomically consume a cancel-deletion token. Returns the user_id on
        success, ``None`` on invalid / expired / already-consumed."""
        token_hash = _hash_token(raw_token)
        now = _now_iso()
        cursor = await self.conn.execute(
            "UPDATE account_deletion_cancel_tokens "
            "SET consumed_at = ? "
            "WHERE token_hash = ? "
            "  AND consumed_at IS NULL "
            "  AND expires_at > ?",
            (now, token_hash, now),
        )
        await self.conn.commit()
        if cursor.rowcount != 1:
            return None
        lookup = await self.conn.execute(
            "SELECT user_id FROM account_deletion_cancel_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        row = await lookup.fetchone()
        return row["user_id"] if row else None

    async def upsert_mobile_device(
        self,
        device_id: str,
        user_id: str,
        name: str | None = None,
        platform: str | None = None,
    ) -> str | None:
        """Register a device on first sight, or refresh/hand over its row.

        Returns the previous owner's ``user_id`` when the call moved the
        device from a different user (a handover), else ``None``. The
        call always succeeds.

        ``mobile_device_id`` is a stable per-install UUID that survives
        sign-out, so a phone re-registers under whichever account is
        currently signed in. Ownership is last-login-wins: the caller
        proves account access (a valid user JWT) and device possession
        (the per-install 122-bit random UUID is never exposed to other
        users), so an authenticated claim always takes the device over.
        The handover revokes every live refresh token on the device and
        wipes its push registration, so the previous account can neither
        refresh its mobile sessions nor keep alerting hardware it no
        longer uses. Already-issued access JWTs stay valid until they
        need a refresh — the same soft-sign-out semantics as an explicit
        device revoke (see ``devices/router.py``).

        Re-registering a previously revoked device under the same owner
        clears ``revoked_at`` — possession of a valid user JWT proves
        account control (Bitwarden semantics).

        Concurrency: a competing claim by another user can interleave
        between this handover and the caller's subsequent refresh-token
        mint, leaving one account briefly holding a live token for a row
        owned by another. Benign under last-login-wins — the next mint
        flips the row again — so no locking is added, matching the rest
        of the DB layer.
        """
        now = _now_iso()
        # The WHERE clause on the ON CONFLICT branch restricts the
        # in-place update to the current owner: an existing row owned by
        # a different user causes the UPDATE to be skipped, the INSERT is
        # abandoned (PK conflict already triggered the ON CONFLICT path),
        # and rowcount stays 0 — routing that case to the handover below.
        # See SQLite UPSERT spec — https://sqlite.org/lang_upsert.html.
        cursor = await self.conn.execute(
            "INSERT INTO mobile_devices "
            "(device_id, user_id, name, created_at, last_seen_at, revoked_at, "
            " platform) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "    name = COALESCE(excluded.name, mobile_devices.name), "
            "    last_seen_at = excluded.last_seen_at, "
            "    revoked_at = NULL, "
            "    platform = COALESCE(excluded.platform, mobile_devices.platform) "
            "WHERE mobile_devices.user_id = excluded.user_id",
            (device_id, user_id, name, now, now, platform),
        )
        await self.conn.commit()
        if cursor.rowcount == 1:
            return None
        # rowcount 0: the ON CONFLICT update was skipped because the row
        # belongs to a different user, or the row already matched our
        # state. Disambiguate.
        cursor = await self.conn.execute(
            "SELECT user_id FROM mobile_devices WHERE device_id = ?",
            (device_id,),
        )
        row = await cursor.fetchone()
        if row is None or row["user_id"] == user_id:
            return None
        previous_owner = row["user_id"]
        # Different user: hand the device over. The token revoke and the
        # owner flip share one commit. The push token is cleared so the
        # new owner's account can never keep alerting the prior owner's
        # APNs registration.
        await self._revoke_live_refresh_tokens(device_id, now)
        await self.conn.execute(
            "UPDATE mobile_devices SET "
            "    user_id = ?, name = ?, created_at = ?, last_seen_at = ?, "
            "    revoked_at = NULL, platform = COALESCE(?, platform), "
            "    push_token = NULL, push_environment = NULL "
            "WHERE device_id = ?",
            (user_id, name, now, now, platform, device_id),
        )
        await self.conn.commit()
        return previous_owner

    async def list_user_devices(self, user_id: str) -> list[dict]:
        """Active devices for a user, most-recently-seen first."""
        cursor = await self.conn.execute(
            "SELECT device_id, name, created_at, last_seen_at, platform "
            "FROM mobile_devices "
            "WHERE user_id = ? AND revoked_at IS NULL "
            "ORDER BY COALESCE(last_seen_at, created_at) DESC",
            (user_id,),
        )
        return [dict(row) async for row in cursor]

    async def revoke_mobile_device(
        self, device_id: str, user_id: str
    ) -> bool:
        """Revoke a device. Returns True iff the device belonged to the user
        and was previously active. Clears the push token so a revoked device
        stops receiving pushes even if it is later re-paired."""
        cursor = await self.conn.execute(
            "UPDATE mobile_devices SET revoked_at = ?, "
            "    push_token = NULL, push_environment = NULL "
            "WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
            (_now_iso(), device_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def set_push_token(
        self,
        device_id: str,
        user_id: str,
        platform: str,
        token: str,
        environment: str,
    ) -> bool:
        """Store/replace the push token for a device the user owns.

        Returns ``False`` when the device does not exist, belongs to another
        user, or is revoked — the caller maps that to HTTP 404.
        """
        cursor = await self.conn.execute(
            "UPDATE mobile_devices SET "
            "    platform = ?, push_token = ?, push_environment = ? "
            "WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
            (platform, token, environment, device_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def clear_push_token_for_owner(
        self, device_id: str, user_id: str
    ) -> bool:
        """Clear the push token for a device the user owns (sign-out path).

        Returns ``False`` when the device does not exist, belongs to another
        user, or is revoked — the caller maps that to HTTP 404.
        """
        cursor = await self.conn.execute(
            "UPDATE mobile_devices SET "
            "    push_token = NULL, push_environment = NULL "
            "WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
            (device_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def clear_push_token(self, device_id: str, token: str) -> None:
        """Drop a dead push token (APNs 410/Unregistered).

        Token-matched so a rotation that lands while a send is in flight
        never wipes the freshly registered token.
        """
        await self.conn.execute(
            "UPDATE mobile_devices SET "
            "    push_token = NULL, push_environment = NULL "
            "WHERE device_id = ? AND push_token = ?",
            (device_id, token),
        )
        await self.conn.commit()

    async def clear_push_tokens_for_user(self, user_id: str) -> int:
        """Drop every push registration the user owns (account-deletion path).

        Unlike the owner-scoped helpers this is not scoped to
        ``revoked_at IS NULL``, and still cannot over-report: the only
        statement that *sets* ``revoked_at`` nulls both push columns in the
        same UPDATE, and ``set_push_token`` refuses revoked rows.
        """
        cursor = await self.conn.execute(
            "UPDATE mobile_devices SET "
            "    push_token = NULL, push_environment = NULL "
            "WHERE user_id = ? AND push_token IS NOT NULL",
            (user_id,),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def list_push_targets(self, user_id: str) -> list[dict]:
        """Active devices of a user that registered a push token."""
        cursor = await self.conn.execute(
            "SELECT device_id, platform, push_token, push_environment "
            "FROM mobile_devices "
            "WHERE user_id = ? AND revoked_at IS NULL "
            "  AND push_token IS NOT NULL",
            (user_id,),
        )
        return [dict(row) async for row in cursor]

    async def touch_mobile_device_last_seen(self, device_id: str) -> None:
        """Bump ``last_seen_at`` on every token mint (best-effort)."""
        await self.conn.execute(
            "UPDATE mobile_devices SET last_seen_at = ? "
            "WHERE device_id = ? AND revoked_at IS NULL",
            (_now_iso(), device_id),
        )
        await self.conn.commit()

    async def _revoke_live_refresh_tokens(
        self, device_id: str, now: str
    ) -> int:
        """Mark every live refresh token for the device revoked, without
        committing — callers group the write into their own commit."""
        cursor = await self.conn.execute(
            "UPDATE mobile_refresh_tokens SET revoked_at = ? "
            "WHERE mobile_device_id = ? AND revoked_at IS NULL",
            (now, device_id),
        )
        return cursor.rowcount

    async def revoke_all_refresh_tokens_for_device(self, device_id: str) -> int:
        """Revoke every active refresh token for the given device across all
        agents. Returns rowcount."""
        count = await self._revoke_live_refresh_tokens(device_id, _now_iso())
        await self.conn.commit()
        return count

    async def store_refresh_token(
        self,
        raw_token: str,
        mobile_device_id: str,
        agent_id: str,
        expire_minutes: int,
    ) -> None:
        token_hash = _hash_token(raw_token)
        now = datetime.now(timezone.utc)
        if expire_minutes == 0:
            expires_at_iso = _NEVER_EXPIRES_AT
        else:
            expires_at_iso = datetime.fromtimestamp(
                now.timestamp() + expire_minutes * 60, tz=timezone.utc
            ).isoformat()
        await self.conn.execute(
            "INSERT INTO mobile_refresh_tokens "
            "(token_hash, mobile_device_id, agent_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_hash, mobile_device_id, agent_id, now.isoformat(), expires_at_iso),
        )
        await self.conn.commit()

    async def validate_and_consume_refresh(
        self, raw_token: str
    ) -> tuple[str, str] | None:
        """Validate a refresh token. Returns (mobile_device_id, agent_id) or None.

        Consumes (revokes) the token on success — caller must issue a new one.
        """
        token_hash = _hash_token(raw_token)
        now = _now_iso()
        cursor = await self.conn.execute(
            "SELECT mobile_device_id, agent_id FROM mobile_refresh_tokens "
            "WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
            (token_hash, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        # Rotate: revoke the consumed token so the caller must issue a fresh one.
        await self.conn.execute(
            "UPDATE mobile_refresh_tokens SET revoked_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        await self.conn.commit()
        return row["mobile_device_id"], row["agent_id"]

    async def store_user_refresh_token(
        self, raw_token: str, user_id: str, expire_minutes: int
    ) -> None:
        """Persist a hashed user-session refresh token.

        Mirrors ``store_refresh_token`` on the mobile axis, including the
        year-9999 sentinel for the never-expires case, so both families
        answer the same ``expires_at > now`` predicate.
        """
        now = datetime.now(timezone.utc)
        if expire_minutes == 0:
            expires_at_iso = _NEVER_EXPIRES_AT
        else:
            expires_at_iso = datetime.fromtimestamp(
                now.timestamp() + expire_minutes * 60, tz=timezone.utc
            ).isoformat()
        await self.conn.execute(
            "INSERT INTO user_refresh_tokens "
            "(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(raw_token), user_id, now.isoformat(), expires_at_iso),
        )
        await self.conn.commit()

    async def validate_and_consume_user_refresh(
        self, raw_token: str
    ) -> str | None:
        """Validate a user-session refresh token, returning its ``user_id``.

        Rotates: the presented token is revoked on success, so the caller
        must issue a replacement. Replaying a consumed token returns None —
        there is deliberately no reuse-detection cascade here, matching
        ``validate_and_consume_refresh``.
        """
        token_hash = _hash_token(raw_token)
        now = _now_iso()
        cursor = await self.conn.execute(
            "SELECT user_id FROM user_refresh_tokens "
            "WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?",
            (token_hash, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await self.conn.execute(
            "UPDATE user_refresh_tokens SET revoked_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        await self.conn.commit()
        return row["user_id"]

    async def revoke_user_refresh_token(self, raw_token: str) -> bool:
        """Revoke one user-session refresh token, expired or not.

        Sign-out, unlike rotation: it must retire the credential the browser
        presented whatever its state, so there is no ``expires_at`` predicate
        and no rotation. Reports whether a live row was actually revoked, but
        callers treat sign-out as idempotent regardless.
        """
        cursor = await self.conn.execute(
            "UPDATE user_refresh_tokens SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (_now_iso(), _hash_token(raw_token)),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def _revoke_live_user_refresh_tokens(
        self, user_id: str, now: str
    ) -> int:
        """Revoke every live user-session refresh token, without committing —
        callers group the write into their own commit."""
        cursor = await self.conn.execute(
            "UPDATE user_refresh_tokens SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        return cursor.rowcount

    async def revoke_all_user_refresh_tokens(self, user_id: str) -> int:
        """Revoke every active user-session refresh token for an account.

        Called wherever the mobile axis already drops its refresh tokens —
        password reset and account deletion — so a credential change ends
        browser sessions too. Already-issued access JWTs stay valid until
        they expire (there is no JWT denylist), which is why the refreshable
        session uses a short access TTL.
        """
        count = await self._revoke_live_user_refresh_tokens(user_id, _now_iso())
        await self.conn.commit()
        return count

    async def revoke_mobile_tokens(
        self, mobile_device_id: str, agent_id: str
    ) -> None:
        """Revoke all refresh tokens for a device+agent pair."""
        await self.conn.execute(
            "UPDATE mobile_refresh_tokens SET revoked_at = ? "
            "WHERE mobile_device_id = ? AND agent_id = ? AND revoked_at IS NULL",
            (_now_iso(), mobile_device_id, agent_id),
        )
        await self.conn.commit()

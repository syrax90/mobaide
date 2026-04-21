"""SQLite database layer via aiosqlite.

Manages persistent state: agent registry (user-owned), pairings,
pairing tokens, mobile refresh tokens, and user accounts.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

# -- Schema -------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT,
    google_sub     TEXT UNIQUE,
    role           TEXT NOT NULL DEFAULT 'viewer',
    email_verified INTEGER NOT NULL DEFAULT 0,
    verified_at    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    token_hash    TEXT NOT NULL,
    owner_user_id TEXT REFERENCES users(id),
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS pairings (
    pairing_id       TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL REFERENCES agents(agent_id),
    mobile_device_id TEXT NOT NULL,
    device_name      TEXT,
    created_at       TEXT NOT NULL,
    revoked_at       TEXT
);

CREATE TABLE IF NOT EXISTS pairing_tokens (
    token_hash  TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL REFERENCES agents(agent_id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT,
    consumed_by TEXT
);

CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
    token_hash       TEXT PRIMARY KEY,
    mobile_device_id TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_google_sub
    ON users(google_sub) WHERE google_sub IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_pairings_agent
    ON pairings(agent_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pairing_tokens_agent
    ON pairing_tokens(agent_id) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_refresh_agent_device
    ON mobile_refresh_tokens(agent_id, mobile_device_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_email_verification_user
    ON email_verification_tokens(user_id) WHERE consumed_at IS NULL;
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw: str) -> str:
    """SHA-256 hash of a pairing/refresh token (for DB storage)."""
    return hashlib.sha256(raw.encode()).hexdigest()


class Database:
    """Async SQLite database for the central server."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        # Schema migration via PRAGMA user_version
        cursor = await self._conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        version = row[0] if row else 0

        if version < 3:
            # Legacy DBs may have an `agents` table WITHOUT the `owner_user_id`
            # column (created by pre-CLA-34 code that never set user_version).
            # Add it first so the idx_agents_owner index in _SCHEMA below
            # doesn't fail with "no such column" — CREATE INDEX IF NOT EXISTS
            # still validates the referenced column even when the index exists.
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

            # Fresh DB or after column added: create all tables/indexes (IF NOT EXISTS is safe)
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
            await self._conn.execute(
                f"DELETE FROM pairings WHERE agent_id IN ({admin_agent_ids_sql})"
            )
            await self._conn.execute(
                "DELETE FROM agents WHERE owner_user_id IN "
                "(SELECT id FROM users WHERE role = 'admin')"
            )
            await self._conn.execute("PRAGMA user_version = 4")

        if version < 5:
            # Add email_verified + verified_at to users (pre-existing rows are
            # grandfathered to email_verified=1 to avoid locking out dev accounts
            # that were created before this migration existed) and create the
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
            # Creates the new email_verification_tokens table + index if missing
            # (IF NOT EXISTS is a no-op on already-present tables).
            await self._conn.executescript(_SCHEMA)
            await self._conn.execute("PRAGMA user_version = 5")

        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    # -- Users -----------------------------------------------------------------

    _USER_COLUMNS = (
        "id, email, password_hash, google_sub, role, "
        "email_verified, verified_at, created_at, updated_at"
    )

    async def create_user(
        self,
        email: str,
        password_hash: str | None = None,
        google_sub: str | None = None,
        role: str = "viewer",
        email_verified: bool = False,
    ) -> str:
        """Create a new user. Returns user_id.

        ``email_verified=True`` is used by the admin seeder and by the
        Google OAuth path (when Google vouches for the address), which both
        bypass the OTP flow.
        """
        user_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        verified_at = now if email_verified else None
        await self.conn.execute(
            "INSERT INTO users "
            "(id, email, password_hash, google_sub, role, email_verified, verified_at, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                email,
                password_hash,
                google_sub,
                role,
                1 if email_verified else 0,
                verified_at,
                now,
                now,
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

    # -- Agents ----------------------------------------------------------------

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
        """Delete an agent and cascade to pairings, tokens, refresh tokens."""
        await self.conn.execute(
            "DELETE FROM mobile_refresh_tokens WHERE agent_id = ?", (agent_id,)
        )
        await self.conn.execute(
            "DELETE FROM pairing_tokens WHERE agent_id = ?", (agent_id,)
        )
        await self.conn.execute(
            "DELETE FROM pairings WHERE agent_id = ?", (agent_id,)
        )
        cursor = await self.conn.execute(
            "DELETE FROM agents WHERE agent_id = ?", (agent_id,)
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def list_agents(self) -> list[dict]:
        """List all agents (admin view)."""
        cursor = await self.conn.execute(
            "SELECT agent_id, name, owner_user_id, created_at, last_seen_at "
            "FROM agents ORDER BY created_at DESC"
        )
        return [dict(row) async for row in cursor]

    async def list_agents_with_owner(self) -> list[dict]:
        """List all agents with owner email (admin dashboard view)."""
        cursor = await self.conn.execute(
            "SELECT a.agent_id, a.name, a.owner_user_id, a.created_at, a.last_seen_at, "
            "u.email AS owner_email "
            "FROM agents a LEFT JOIN users u ON a.owner_user_id = u.id "
            "ORDER BY a.created_at DESC"
        )
        return [dict(row) async for row in cursor]

    async def list_agents_for_user(self, user_id: str) -> list[dict]:
        """List agents owned by a specific user."""
        cursor = await self.conn.execute(
            "SELECT agent_id, name, owner_user_id, created_at, last_seen_at "
            "FROM agents WHERE owner_user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(row) async for row in cursor]

    async def get_agent(self, agent_id: str) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT agent_id, name, owner_user_id, created_at, last_seen_at "
            "FROM agents WHERE agent_id = ?",
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

    async def touch_agent_last_seen(self, agent_id: str) -> None:
        await self.conn.execute(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id = ?",
            (_now_iso(), agent_id),
        )
        await self.conn.commit()

    # -- Pairing tokens --------------------------------------------------------

    async def create_pairing_token(
        self, agent_id: str, ttl_seconds: int
    ) -> tuple[str, str]:
        """Create a pairing token. Returns (raw_token, expires_at ISO)."""
        raw_token = secrets.token_urlsafe(24)  # 192 bits
        token_hash = _hash_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        await self.conn.execute(
            "INSERT INTO pairing_tokens (token_hash, agent_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, agent_id, now.isoformat(), expires_at.isoformat()),
        )
        await self.conn.commit()
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
        # Fetch the agent_id for the consumed token.
        cursor2 = await self.conn.execute(
            "SELECT agent_id FROM pairing_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        row = await cursor2.fetchone()
        return row["agent_id"] if row else None

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

    async def cleanup_expired_tokens(self) -> int:
        """Delete expired pairing AND email-verification tokens.

        Verification tokens are cleaned in two sweeps:
          - expired but never consumed rows are dropped immediately;
          - successfully consumed rows are dropped after the retention
            window so old code hashes don't linger as brute-force fodder
            on a DB dump.

        Returns the total number of deleted rows across all sweeps.
        """
        now = _now_iso()
        retention_cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self._VERIFICATION_TOKEN_RETENTION_SECONDS)
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
        await self.conn.commit()
        return (
            pairing_cursor.rowcount
            + expired_cursor.rowcount
            + consumed_cursor.rowcount
        )

    # -- Email verification tokens --------------------------------------------

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
        # Invalidate any outstanding tokens for this user.
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
        # Atomic CAS: consume iff matching hash + not consumed + not expired
        # + attempts still below the cap. ``attempts`` is compared against the
        # pre-increment value, which is what we want here (callers increment
        # only on mismatch).
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

    # -- Pairings --------------------------------------------------------------

    async def create_pairing(
        self,
        agent_id: str,
        mobile_device_id: str,
        device_name: str | None = None,
    ) -> str:
        """Create a pairing record. Returns pairing_id."""
        pairing_id = uuid.uuid4().hex[:12]
        await self.conn.execute(
            "INSERT INTO pairings (pairing_id, agent_id, mobile_device_id, device_name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pairing_id, agent_id, mobile_device_id, device_name, _now_iso()),
        )
        await self.conn.commit()
        return pairing_id

    async def list_pairings_for_agent(self, agent_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT pairing_id, mobile_device_id, device_name, created_at, revoked_at "
            "FROM pairings WHERE agent_id = ? ORDER BY created_at DESC",
            (agent_id,),
        )
        return [dict(row) async for row in cursor]

    async def revoke_pairing(self, pairing_id: str) -> bool:
        cursor = await self.conn.execute(
            "UPDATE pairings SET revoked_at = ? WHERE pairing_id = ? AND revoked_at IS NULL",
            (_now_iso(), pairing_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def is_mobile_paired_to_agent(
        self, mobile_device_id: str, agent_id: str
    ) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM pairings "
            "WHERE mobile_device_id = ? AND agent_id = ? AND revoked_at IS NULL "
            "LIMIT 1",
            (mobile_device_id, agent_id),
        )
        return await cursor.fetchone() is not None

    # -- Mobile refresh tokens -------------------------------------------------

    async def store_refresh_token(
        self,
        raw_token: str,
        mobile_device_id: str,
        agent_id: str,
        expire_minutes: int,
    ) -> None:
        token_hash = _hash_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            now.timestamp() + expire_minutes * 60, tz=timezone.utc
        )
        await self.conn.execute(
            "INSERT INTO mobile_refresh_tokens "
            "(token_hash, mobile_device_id, agent_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_hash, mobile_device_id, agent_id, now.isoformat(), expires_at.isoformat()),
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
        # Revoke the used token (rotation).
        await self.conn.execute(
            "UPDATE mobile_refresh_tokens SET revoked_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        await self.conn.commit()
        return row["mobile_device_id"], row["agent_id"]

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

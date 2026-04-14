"""SQLite database layer via aiosqlite.

Manages persistent state: agent registry, pairings, pairing tokens,
and mobile refresh tokens.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import aiosqlite
import bcrypt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT
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

CREATE INDEX IF NOT EXISTS idx_pairings_agent
    ON pairings(agent_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pairing_tokens_agent
    ON pairing_tokens(agent_id) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_refresh_agent_device
    ON mobile_refresh_tokens(agent_id, mobile_device_id) WHERE revoked_at IS NULL;
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
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    # -- Agents ----------------------------------------------------------------

    async def register_agent(self, name: str) -> tuple[str, str]:
        """Register a new agent. Returns (agent_id, raw_token)."""
        agent_id = uuid.uuid4().hex[:12]
        raw_token = secrets.token_urlsafe(32)
        token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
        now = _now_iso()
        await self.conn.execute(
            "INSERT INTO agents (agent_id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (agent_id, name, token_hash, now),
        )
        await self.conn.commit()
        return agent_id, raw_token

    async def verify_agent_token(self, agent_id: str, raw_token: str) -> bool:
        """Verify an agent's authentication token."""
        cursor = await self.conn.execute(
            "SELECT token_hash FROM agents WHERE agent_id = ?", (agent_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        return bcrypt.checkpw(raw_token.encode(), row["token_hash"].encode())

    async def list_agents(self) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT agent_id, name, created_at, last_seen_at FROM agents ORDER BY created_at DESC"
        )
        return [dict(row) async for row in cursor]

    async def get_agent(self, agent_id: str) -> dict | None:
        cursor = await self.conn.execute(
            "SELECT agent_id, name, created_at, last_seen_at FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

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

    async def cleanup_expired_tokens(self) -> int:
        now = _now_iso()
        cursor = await self.conn.execute(
            "DELETE FROM pairing_tokens WHERE expires_at <= ? AND consumed_at IS NULL",
            (now,),
        )
        await self.conn.commit()
        return cursor.rowcount

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

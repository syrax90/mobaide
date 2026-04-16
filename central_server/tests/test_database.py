"""Tests for the Database layer."""
from __future__ import annotations

import pytest

from central_server.db.database import Database


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


class TestAgentCRUD:
    async def test_register_and_verify(self, db: Database):
        agent_id, raw_token = await db.register_agent("my-agent")
        assert len(agent_id) == 12
        assert len(raw_token) > 20
        assert await db.verify_agent_token(agent_id, raw_token) is True
        assert await db.verify_agent_token(agent_id, "wrong") is False

    async def test_list_agents(self, db: Database):
        await db.register_agent("a1")
        await db.register_agent("a2")
        agents = await db.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"a1", "a2"}

    async def test_get_agent(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        agent = await db.get_agent(agent_id)
        assert agent is not None
        assert agent["name"] == "test"
        assert await db.get_agent("nonexistent") is None

    async def test_touch_last_seen(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        await db.touch_agent_last_seen(agent_id)
        agent = await db.get_agent(agent_id)
        assert agent["last_seen_at"] is not None


class TestPairingTokens:
    async def test_create_and_consume(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, expires_at = await db.create_pairing_token(agent_id, 300)
        assert len(raw_token) > 20
        assert expires_at is not None

        # Consume
        result = await db.consume_pairing_token(raw_token, "mobile-1")
        assert result == agent_id

    async def test_single_use(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        raw_token, _ = await db.create_pairing_token(agent_id, 300)

        # First consumption succeeds
        assert await db.consume_pairing_token(raw_token, "mobile-1") == agent_id
        # Second consumption fails (already consumed)
        assert await db.consume_pairing_token(raw_token, "mobile-2") is None

    async def test_expired_token(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        # TTL = 0 seconds → already expired
        raw_token, _ = await db.create_pairing_token(agent_id, 0)
        assert await db.consume_pairing_token(raw_token, "mobile-1") is None

    async def test_invalid_token(self, db: Database):
        assert await db.consume_pairing_token("bogus-token", "mobile-1") is None

    async def test_cleanup_expired(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        await db.create_pairing_token(agent_id, 0)  # Already expired
        await db.create_pairing_token(agent_id, 300)  # Still valid
        deleted = await db.cleanup_expired_tokens()
        assert deleted == 1

    async def test_pending_tokens(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        await db.create_pairing_token(agent_id, 300)
        await db.create_pairing_token(agent_id, 300)
        pending = await db.get_pending_pairing_tokens(agent_id)
        assert len(pending) == 2


class TestPairings:
    async def test_create_and_list(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        pairing_id = await db.create_pairing(agent_id, "device-1", "Pixel 7")
        assert len(pairing_id) == 12

        pairings = await db.list_pairings_for_agent(agent_id)
        assert len(pairings) == 1
        assert pairings[0]["mobile_device_id"] == "device-1"
        assert pairings[0]["device_name"] == "Pixel 7"

    async def test_is_paired(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False
        await db.create_pairing(agent_id, "device-1")
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is True

    async def test_revoke(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        pairing_id = await db.create_pairing(agent_id, "device-1")
        assert await db.revoke_pairing(pairing_id) is True
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False
        # Second revoke returns False
        assert await db.revoke_pairing(pairing_id) is False


class TestUserCRUD:
    async def test_create_and_get_by_email(self, db: Database):
        user_id = await db.create_user(
            email="user@example.com",
            password_hash="$argon2id$fake",
            role="viewer",
        )
        assert len(user_id) == 12
        user = await db.get_user_by_email("user@example.com")
        assert user is not None
        assert user["id"] == user_id
        assert user["email"] == "user@example.com"
        assert user["role"] == "viewer"
        assert user["password_hash"] == "$argon2id$fake"
        assert user["google_sub"] is None

    async def test_get_by_id(self, db: Database):
        user_id = await db.create_user(email="user@example.com")
        user = await db.get_user_by_id(user_id)
        assert user is not None
        assert user["id"] == user_id

    async def test_get_by_google_sub(self, db: Database):
        user_id = await db.create_user(
            email="google@example.com", google_sub="g-12345"
        )
        user = await db.get_user_by_google_sub("g-12345")
        assert user is not None
        assert user["id"] == user_id
        assert user["email"] == "google@example.com"

    async def test_get_nonexistent(self, db: Database):
        assert await db.get_user_by_email("nobody@example.com") is None
        assert await db.get_user_by_id("nonexistent") is None
        assert await db.get_user_by_google_sub("nosub") is None

    async def test_duplicate_email_raises(self, db: Database):
        await db.create_user(email="dup@example.com")
        with pytest.raises(Exception):  # aiosqlite raises IntegrityError
            await db.create_user(email="dup@example.com")

    async def test_link_google_to_user(self, db: Database):
        user_id = await db.create_user(
            email="user@example.com", password_hash="hash"
        )
        assert await db.get_user_by_google_sub("g-abc") is None

        await db.link_google_to_user(user_id, "g-abc")
        user = await db.get_user_by_google_sub("g-abc")
        assert user is not None
        assert user["id"] == user_id
        assert user["google_sub"] == "g-abc"

    async def test_schema_migration_version(self, db: Database):
        """Verify that PRAGMA user_version is set after migration."""
        cursor = await db.conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        assert row[0] == 2


class TestRefreshTokens:
    async def test_store_and_validate(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        await db.store_refresh_token("refresh-abc", "device-1", agent_id, 60)
        result = await db.validate_and_consume_refresh("refresh-abc")
        assert result == ("device-1", agent_id)

    async def test_rotation(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        await db.store_refresh_token("refresh-abc", "device-1", agent_id, 60)
        # First use succeeds
        assert await db.validate_and_consume_refresh("refresh-abc") is not None
        # Second use fails (revoked after first use)
        assert await db.validate_and_consume_refresh("refresh-abc") is None

    async def test_revoke_all(self, db: Database):
        agent_id, _ = await db.register_agent("test")
        await db.store_refresh_token("r1", "device-1", agent_id, 60)
        await db.store_refresh_token("r2", "device-1", agent_id, 60)
        await db.revoke_mobile_tokens("device-1", agent_id)
        assert await db.validate_and_consume_refresh("r1") is None
        assert await db.validate_and_consume_refresh("r2") is None

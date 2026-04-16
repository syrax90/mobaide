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


async def _create_test_user(db: Database) -> str:
    """Helper: create a test user, return user_id."""
    return await db.create_user(email="owner@test.local", password_hash="hash")


class TestAgentCRUD:
    async def test_register_and_verify(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("my-agent", owner)
        assert len(agent_id) == 12
        assert len(raw_token) > 20
        assert await db.verify_agent_token(agent_id, raw_token) is True
        assert await db.verify_agent_token(agent_id, "wrong") is False

    async def test_list_agents(self, db: Database):
        owner = await _create_test_user(db)
        await db.register_agent("a1", owner)
        await db.register_agent("a2", owner)
        agents = await db.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"a1", "a2"}
        # All agents have owner_user_id
        for a in agents:
            assert a["owner_user_id"] == owner

    async def test_get_agent(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        agent = await db.get_agent(agent_id)
        assert agent is not None
        assert agent["name"] == "test"
        assert agent["owner_user_id"] == owner
        assert await db.get_agent("nonexistent") is None

    async def test_touch_last_seen(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.touch_agent_last_seen(agent_id)
        agent = await db.get_agent(agent_id)
        assert agent["last_seen_at"] is not None

    async def test_list_agents_for_user(self, db: Database):
        owner1 = await db.create_user(email="user1@test.local", password_hash="h")
        owner2 = await db.create_user(email="user2@test.local", password_hash="h")
        await db.register_agent("a1", owner1)
        await db.register_agent("a2", owner1)
        await db.register_agent("b1", owner2)

        user1_agents = await db.list_agents_for_user(owner1)
        assert len(user1_agents) == 2
        user2_agents = await db.list_agents_for_user(owner2)
        assert len(user2_agents) == 1
        assert user2_agents[0]["name"] == "b1"

    async def test_list_agents_with_owner(self, db: Database):
        owner = await _create_test_user(db)
        await db.register_agent("a1", owner)
        agents = await db.list_agents_with_owner()
        assert len(agents) == 1
        assert agents[0]["owner_email"] == "owner@test.local"

    async def test_is_agent_owner(self, db: Database):
        owner = await _create_test_user(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("test", owner)
        assert await db.is_agent_owner(agent_id, owner) is True
        assert await db.is_agent_owner(agent_id, other) is False
        assert await db.is_agent_owner("nonexistent", owner) is False

    async def test_reregister_agent(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, old_token = await db.register_agent("test", owner)
        new_token = await db.reregister_agent(agent_id)
        assert new_token != old_token
        assert len(new_token) > 20
        # Old token no longer works
        assert await db.verify_agent_token(agent_id, old_token) is False
        # New token works
        assert await db.verify_agent_token(agent_id, new_token) is True

    async def test_delete_agent(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.create_pairing(agent_id, "device-1")
        await db.create_pairing_token(agent_id, 300)
        await db.store_refresh_token("r1", "device-1", agent_id, 60)

        assert await db.delete_agent(agent_id) is True
        assert await db.get_agent(agent_id) is None
        # Cascade: pairings, tokens, refresh tokens are gone
        assert await db.list_pairings_for_agent(agent_id) == []
        assert await db.get_pending_pairing_tokens(agent_id) == []
        assert await db.validate_and_consume_refresh("r1") is None

    async def test_delete_nonexistent_returns_false(self, db: Database):
        assert await db.delete_agent("nonexistent") is False


class TestPairingTokens:
    async def test_create_and_consume(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        raw_token, expires_at = await db.create_pairing_token(agent_id, 300)
        assert len(raw_token) > 20
        assert expires_at is not None

        result = await db.consume_pairing_token(raw_token, "mobile-1")
        assert result == agent_id

    async def test_single_use(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        raw_token, _ = await db.create_pairing_token(agent_id, 300)

        assert await db.consume_pairing_token(raw_token, "mobile-1") == agent_id
        assert await db.consume_pairing_token(raw_token, "mobile-2") is None

    async def test_expired_token(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        raw_token, _ = await db.create_pairing_token(agent_id, 0)
        assert await db.consume_pairing_token(raw_token, "mobile-1") is None

    async def test_invalid_token(self, db: Database):
        assert await db.consume_pairing_token("bogus-token", "mobile-1") is None

    async def test_cleanup_expired(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.create_pairing_token(agent_id, 0)
        await db.create_pairing_token(agent_id, 300)
        deleted = await db.cleanup_expired_tokens()
        assert deleted == 1

    async def test_pending_tokens(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.create_pairing_token(agent_id, 300)
        await db.create_pairing_token(agent_id, 300)
        pending = await db.get_pending_pairing_tokens(agent_id)
        assert len(pending) == 2


class TestPairings:
    async def test_create_and_list(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        pairing_id = await db.create_pairing(agent_id, "device-1", "Pixel 7")
        assert len(pairing_id) == 12

        pairings = await db.list_pairings_for_agent(agent_id)
        assert len(pairings) == 1
        assert pairings[0]["mobile_device_id"] == "device-1"
        assert pairings[0]["device_name"] == "Pixel 7"

    async def test_is_paired(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False
        await db.create_pairing(agent_id, "device-1")
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is True

    async def test_revoke(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        pairing_id = await db.create_pairing(agent_id, "device-1")
        assert await db.revoke_pairing(pairing_id) is True
        assert await db.is_mobile_paired_to_agent("device-1", agent_id) is False
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
        assert row[0] == 3


class TestRefreshTokens:
    async def test_store_and_validate(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("refresh-abc", "device-1", agent_id, 60)
        result = await db.validate_and_consume_refresh("refresh-abc")
        assert result == ("device-1", agent_id)

    async def test_rotation(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("refresh-abc", "device-1", agent_id, 60)
        assert await db.validate_and_consume_refresh("refresh-abc") is not None
        assert await db.validate_and_consume_refresh("refresh-abc") is None

    async def test_revoke_all(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r1", "device-1", agent_id, 60)
        await db.store_refresh_token("r2", "device-1", agent_id, 60)
        await db.revoke_mobile_tokens("device-1", agent_id)
        assert await db.validate_and_consume_refresh("r1") is None
        assert await db.validate_and_consume_refresh("r2") is None

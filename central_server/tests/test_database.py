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

    async def test_delete_agent_with_qr_claimed_token(self, db: Database):
        """Regression: pairing_tokens.claimed_agent_id is a second FK to
        agents that ``delete_agent`` must also clear, otherwise SQLite
        raises a FOREIGN KEY constraint failure on the agents DELETE.

        Reproduces the QR-pairing post-claim shape where ``agent_id`` is
        NULL (created pre-claim) and ``claimed_agent_id`` carries the FK
        to the live agent — surviving cleanup for forensics.
        """
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)

        raw_nonce, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=300
        )
        claimed = await db.claim_pairing_nonce(
            raw_token=raw_nonce,
            mobile_device_id="device-1",
            claimed_agent_id=agent_id,
            claimed_token_raw="agent-token-stub",
        )
        assert claimed is True

        assert await db.delete_agent(agent_id) is True
        assert await db.get_agent(agent_id) is None
        cur = await db.conn.execute(
            "SELECT 1 FROM pairing_tokens WHERE claimed_agent_id = ?",
            (agent_id,),
        )
        assert await cur.fetchone() is None


class TestAgentHandshakeInfo:
    """Cover ``Database.update_agent_handshake_info`` + the new
    ``agent_version`` / ``agent_protocol_version`` columns surfaced
    through ``get_agent`` / ``list_agents`` / ``list_agents_with_owner``
    / ``list_agents_for_user``.

    The COALESCE-based update is the load-bearing invariant: an
    auth message that omits ``agent_version`` must not clobber a
    previously-recorded value.
    """

    async def test_update_persists_both_fields(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)

        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        agent = await db.get_agent(agent_id)
        assert agent is not None
        assert agent["agent_version"] == "0.3.0"
        assert agent["agent_protocol_version"] == 2

    async def test_freshly_registered_agent_has_null_handshake_fields(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)

        agent = await db.get_agent(agent_id)
        assert agent is not None
        assert agent["agent_version"] is None
        assert agent["agent_protocol_version"] is None

    async def test_update_with_none_version_preserves_existing(
        self, db: Database
    ):
        """A reconnect that omitted ``agent_version`` from the handshake
        must not wipe the previously-stored value — that's the COALESCE
        contract.
        """
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        await db.update_agent_handshake_info(agent_id, None, 2)

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.3.0"
        assert agent["agent_protocol_version"] == 2

    async def test_update_with_none_protocol_version_preserves_existing(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        await db.update_agent_handshake_info(agent_id, "0.4.0", None)

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.4.0"
        assert agent["agent_protocol_version"] == 2

    async def test_update_with_both_none_is_noop_on_existing_values(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        await db.update_agent_handshake_info(agent_id, None, None)

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.3.0"
        assert agent["agent_protocol_version"] == 2

    async def test_update_overwrites_with_newer_version(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        await db.update_agent_handshake_info(agent_id, "0.4.0", 3)

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.4.0"
        assert agent["agent_protocol_version"] == 3

    async def test_update_nonexistent_agent_is_noop(self, db: Database):
        # No row matches; UPDATE affects 0 rows but must not raise.
        await db.update_agent_handshake_info("nonexistent", "0.3.0", 2)
        assert await db.get_agent("nonexistent") is None

    async def test_list_agents_includes_handshake_fields(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a1", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        agents = await db.list_agents()
        assert len(agents) == 1
        assert agents[0]["agent_version"] == "0.3.0"
        assert agents[0]["agent_protocol_version"] == 2

    async def test_list_agents_with_owner_includes_handshake_fields(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a1", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        agents = await db.list_agents_with_owner()
        assert len(agents) == 1
        assert agents[0]["agent_version"] == "0.3.0"
        assert agents[0]["agent_protocol_version"] == 2
        # Existing owner_email projection must still work alongside the
        # new columns — guards against an accidental column drop in the
        # JOIN SELECT list.
        assert agents[0]["owner_email"] == "owner@test.local"

    async def test_list_agents_for_user_includes_handshake_fields(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a1", owner)
        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        agents = await db.list_agents_for_user(owner)
        assert len(agents) == 1
        assert agents[0]["agent_version"] == "0.3.0"
        assert agents[0]["agent_protocol_version"] == 2

    async def test_update_refreshes_last_seen_on_fresh_row(self, db: Database):
        """The connect path no longer calls ``touch_agent_last_seen``
        separately — ``update_agent_handshake_info`` is now responsible
        for bumping ``last_seen_at`` in the same UPDATE+commit. A
        freshly-registered agent has ``last_seen_at IS NULL``; one
        handshake must populate it.
        """
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        before = await db.get_agent(agent_id)
        assert before is not None
        assert before["last_seen_at"] is None

        await db.update_agent_handshake_info(agent_id, "0.3.0", 2)

        after = await db.get_agent(agent_id)
        assert after["last_seen_at"] is not None
        # _now_iso() emits an ISO-8601 string with a UTC offset.
        assert after["last_seen_at"].endswith("+00:00")

    async def test_update_refreshes_last_seen_when_both_versions_none(
        self, db: Database
    ):
        """``last_seen_at`` is always refreshed, regardless of the
        COALESCE-preserve-on-None rule that governs ``agent_version`` /
        ``agent_protocol_version``. A reconnect where the agent omitted
        both version fields still counts as "seen now".
        """
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        assert (await db.get_agent(agent_id))["last_seen_at"] is None

        await db.update_agent_handshake_info(agent_id, None, None)

        agent = await db.get_agent(agent_id)
        # last_seen_at must be set even though the COALESCE branches for
        # the two version columns are no-ops.
        assert agent["last_seen_at"] is not None
        assert agent["agent_version"] is None
        assert agent["agent_protocol_version"] is None


class TestListUsersWithAgents:
    """``Database.list_users_with_agents`` is the single query driving the
    users-centric dashboard. Verifies the LEFT JOIN grouping shape, the
    empty-agents path for an agentless user, ordering, and that every
    documented field flows through the SELECT projection.
    """

    async def test_empty_db_returns_empty_list(self, db: Database):
        assert await db.list_users_with_agents() == []

    async def test_user_with_no_agents_has_empty_agents_list(self, db: Database):
        """A user with zero agents must surface as a row with ``agents=[]``,
        not be filtered out by the LEFT JOIN (the agentless-user branch in
        the Python grouping is the load-bearing reason for the LEFT JOIN).
        """
        await db.create_user(email="solo@test.local", password_hash="h")

        users = await db.list_users_with_agents()
        assert len(users) == 1
        assert users[0]["email"] == "solo@test.local"
        assert users[0]["agents"] == []

    async def test_user_with_multiple_agents_groups_correctly(
        self, db: Database
    ):
        """Two LEFT JOIN rows for one user must collapse to one user dict
        with both agents nested in the ``agents`` list — the grouping
        invariant.
        """
        owner = await db.create_user(email="multi@test.local", password_hash="h")
        await db.register_agent("a1", owner)
        await db.register_agent("a2", owner)

        users = await db.list_users_with_agents()
        assert len(users) == 1
        assert users[0]["id"] == owner
        agent_names = {a["name"] for a in users[0]["agents"]}
        assert agent_names == {"a1", "a2"}

    async def test_users_ordered_newest_first(self, db: Database):
        """``ORDER BY u.created_at DESC`` — the most recently created user
        appears first. Sleeps a microsecond between creates so the ISO
        timestamps sort strictly.
        """
        import asyncio

        first = await db.create_user(email="first@test.local", password_hash="h")
        await asyncio.sleep(0.001)
        middle = await db.create_user(email="middle@test.local", password_hash="h")
        await asyncio.sleep(0.001)
        last = await db.create_user(email="last@test.local", password_hash="h")

        users = await db.list_users_with_agents()
        ids = [u["id"] for u in users]
        assert ids == [last, middle, first]

    async def test_agents_within_user_ordered_newest_first(self, db: Database):
        """Per the SQL ``ORDER BY u.created_at DESC, a.created_at DESC`` the
        nested ``agents`` list for each user must be newest-agent-first.
        """
        import asyncio

        owner = await db.create_user(email="o@test.local", password_hash="h")
        old_id, _ = await db.register_agent("old", owner)
        await asyncio.sleep(0.001)
        mid_id, _ = await db.register_agent("mid", owner)
        await asyncio.sleep(0.001)
        new_id, _ = await db.register_agent("new", owner)

        users = await db.list_users_with_agents()
        assert len(users) == 1
        agent_ids = [a["agent_id"] for a in users[0]["agents"]]
        assert agent_ids == [new_id, mid_id, old_id]

    async def test_projects_all_documented_user_fields(self, db: Database):
        """The dashboard template reads ``id``, ``email``, ``role``,
        ``created_at`` off each user dict. The function's docstring locks
        these keys in — guard against an accidental SELECT-list trim.
        """
        owner = await db.create_user(
            email="who@test.local", password_hash="h", role="viewer"
        )

        users = await db.list_users_with_agents()
        assert len(users) == 1
        u = users[0]
        assert u["id"] == owner
        assert u["email"] == "who@test.local"
        assert u["role"] == "viewer"
        assert isinstance(u["created_at"], str)
        # Trailing UTC offset confirms _now_iso() shape, not a stray
        # naive datetime from a future regression.
        assert u["created_at"].endswith("+00:00")

    async def test_projects_all_documented_agent_fields(self, db: Database):
        """The expanded per-user detail row reads ``agent_id``, ``name``,
        ``created_at``, ``last_seen_at``, ``agent_version``,
        ``agent_protocol_version`` off each nested agent dict. The
        function's docstring locks these keys in.
        """
        owner = await db.create_user(email="o@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("the-agent", owner)
        await db.update_agent_handshake_info(agent_id, "0.5.0", 2)

        users = await db.list_users_with_agents()
        assert len(users) == 1
        assert len(users[0]["agents"]) == 1
        a = users[0]["agents"][0]
        assert a["agent_id"] == agent_id
        assert a["name"] == "the-agent"
        assert a["agent_version"] == "0.5.0"
        assert a["agent_protocol_version"] == 2
        assert a["last_seen_at"] is not None
        assert isinstance(a["created_at"], str)

    async def test_fresh_agent_has_null_handshake_fields(self, db: Database):
        """A just-registered agent has NULL ``last_seen_at``,
        ``agent_version``, and ``agent_protocol_version``. These must
        flow through as Python ``None``, not be dropped from the dict.
        """
        owner = await db.create_user(email="o@test.local", password_hash="h")
        await db.register_agent("fresh", owner)

        users = await db.list_users_with_agents()
        a = users[0]["agents"][0]
        assert a["last_seen_at"] is None
        assert a["agent_version"] is None
        assert a["agent_protocol_version"] is None

    async def test_multiple_users_with_mixed_agent_counts(self, db: Database):
        """A populated DB: one agentless user, one user with two agents,
        one user with one agent. Every user appears exactly once with the
        correct ``agents`` count.
        """
        import asyncio

        # Creation order shapes the ORDER BY: u3 is newest.
        u1 = await db.create_user(email="u1@test.local", password_hash="h")
        await asyncio.sleep(0.001)
        u2 = await db.create_user(email="u2@test.local", password_hash="h")
        await asyncio.sleep(0.001)
        u3 = await db.create_user(email="u3@test.local", password_hash="h")

        # u1 has 2 agents, u2 has 0, u3 has 1.
        await db.register_agent("u1-a1", u1)
        await db.register_agent("u1-a2", u1)
        await db.register_agent("u3-a1", u3)

        users = await db.list_users_with_agents()
        assert len(users) == 3
        # Newest-first ordering.
        assert [u["id"] for u in users] == [u3, u2, u1]
        counts = {u["id"]: len(u["agents"]) for u in users}
        assert counts == {u1: 2, u2: 0, u3: 1}


class TestDeleteUser:
    """``Database.delete_user`` cascade — explicit per-table sequence that
    returns the deleted agent IDs so the router can tear down live WSs.
    """

    async def test_delete_unknown_user_returns_empty_list(self, db: Database):
        """Per docstring: ``Returns an empty list if user_id does not exist.``"""
        assert await db.delete_user("ghost") == []

    async def test_delete_user_with_no_agents_returns_empty_list(
        self, db: Database
    ):
        """An agentless user yields an empty agent-id list — the dashboard
        router uses that list to decide whether to fan out
        ``disconnect_agent`` calls. Wrong shape (None / scalar) would
        crash the router's ``for agent_id in agent_ids`` loop.
        """
        user_id = await db.create_user(email="solo@test.local", password_hash="h")

        agent_ids = await db.delete_user(user_id)
        assert agent_ids == []
        assert await db.get_user_by_id(user_id) is None

    async def test_delete_user_returns_owned_agent_ids(self, db: Database):
        """Single-agent owner: returned list has exactly the one agent_id."""
        owner = await db.create_user(email="o@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("the-agent", owner)

        agent_ids = await db.delete_user(owner)
        assert agent_ids == [agent_id]
        assert await db.get_agent(agent_id) is None

    async def test_delete_user_returns_all_owned_agent_ids(
        self, db: Database
    ):
        """Multi-agent cascade: the router needs *every* agent_id back so
        it can disconnect each agent's WS. The returned list must contain
        all of them, even though a single SQL ``DELETE FROM agents WHERE
        owner_user_id = ?`` drops them in one statement.
        """
        owner = await db.create_user(email="o@test.local", password_hash="h")
        a1, _ = await db.register_agent("a1", owner)
        a2, _ = await db.register_agent("a2", owner)
        a3, _ = await db.register_agent("a3", owner)

        agent_ids = await db.delete_user(owner)
        assert isinstance(agent_ids, list)
        assert set(agent_ids) == {a1, a2, a3}
        for aid in (a1, a2, a3):
            assert await db.get_agent(aid) is None

    async def test_delete_user_cascades_all_dependent_rows(
        self, db: Database
    ):
        """End-to-end cascade across every table the docstring enumerates:
        ``mobile_refresh_tokens``, ``pairing_tokens``,
        ``pairings``, ``agents``, ``email_verification_tokens``,
        ``prompt_templates``, ``users``. Establish at least one row in
        each (across two agents to exercise the IN-subquery shape) and
        assert each table is empty for the deleted user afterward.
        """
        owner = await db.create_user(email="o@test.local", password_hash="h")
        a1, _ = await db.register_agent("a1", owner)
        a2, _ = await db.register_agent("a2", owner)

        await db.create_pairing(a1, "dev-1")
        await db.create_pairing(a2, "dev-2")
        await db.create_pairing_token(a1, 300)
        await db.create_pairing_token(a2, 300)
        await db.store_refresh_token("r-1", "dev-1", a1, 60)
        await db.store_refresh_token("r-2", "dev-2", a2, 60)
        await db.create_template(owner, "tpl-1", "body-1")
        await db.create_template(owner, "tpl-2", "body-2")
        await db.create_verification_token(owner, "code-hash", ttl_seconds=300)

        agent_ids = await db.delete_user(owner)
        assert set(agent_ids) == {a1, a2}

        # Every table is empty for the deleted user.
        assert await db.get_user_by_id(owner) is None
        assert await db.get_agent(a1) is None
        assert await db.get_agent(a2) is None
        assert await db.list_pairings_for_agent(a1) == []
        assert await db.list_pairings_for_agent(a2) == []
        assert await db.get_pending_pairing_tokens(a1) == []
        assert await db.get_pending_pairing_tokens(a2) == []
        assert await db.validate_and_consume_refresh("r-1") is None
        assert await db.validate_and_consume_refresh("r-2") is None
        assert await db.list_templates_for_user(owner) == []
        # email_verification_tokens — direct count, no public reader.
        cur = await db.conn.execute(
            "SELECT COUNT(*) AS n FROM email_verification_tokens "
            "WHERE user_id = ?",
            (owner,),
        )
        row = await cur.fetchone()
        assert row["n"] == 0

    async def test_delete_user_preserves_other_users_data(self, db: Database):
        """The IN-subquery cascade must scope strictly to the deleted user.
        A second user's agents, pairings, refresh tokens, and templates
        must be untouched.
        """
        victim = await db.create_user(email="victim@test.local", password_hash="h")
        survivor = await db.create_user(email="survivor@test.local", password_hash="h")
        v_agent, _ = await db.register_agent("v-agent", victim)
        s_agent, _ = await db.register_agent("s-agent", survivor)

        await db.create_pairing(v_agent, "v-dev")
        await db.create_pairing(s_agent, "s-dev")
        await db.store_refresh_token("v-r", "v-dev", v_agent, 60)
        await db.store_refresh_token("s-r", "s-dev", s_agent, 60)
        await db.create_template(victim, "v-tpl", "v")
        await db.create_template(survivor, "s-tpl", "s")

        agent_ids = await db.delete_user(victim)
        assert agent_ids == [v_agent]

        # Survivor's rows are intact.
        assert await db.get_user_by_id(survivor) is not None
        assert await db.get_agent(s_agent) is not None
        assert await db.is_mobile_paired_to_agent("s-dev", s_agent) is True
        assert await db.validate_and_consume_refresh("s-r") == ("s-dev", s_agent)
        templates = await db.list_templates_for_user(survivor)
        assert len(templates) == 1
        assert templates[0]["name"] == "s-tpl"

    async def test_delete_user_clears_qr_claimed_agent_id_rows(
        self, db: Database
    ):
        """Regression: ``pairing_tokens.claimed_agent_id`` is the second FK
        from pairing_tokens to agents (the QR-pairing post-claim shape).
        The cascade's ``OR claimed_agent_id IN (...)`` branch must clear
        these rows, otherwise the agents DELETE raises an FK violation
        under ``PRAGMA foreign_keys=ON`` (mirrors the established
        ``delete_agent`` regression).
        """
        owner = await db.create_user(email="o@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("qr-agent", owner)

        raw_nonce, _ = await db.create_pairing_token(
            agent_id=None, ttl_seconds=300
        )
        claimed = await db.claim_pairing_nonce(
            raw_token=raw_nonce,
            mobile_device_id="dev-1",
            claimed_agent_id=agent_id,
            claimed_token_raw="agent-token-stub",
        )
        assert claimed is True

        agent_ids = await db.delete_user(owner)
        assert agent_ids == [agent_id]
        # Both the agent row AND the claimed_agent_id pairing-token row
        # must be gone for the cascade to be FK-clean.
        assert await db.get_agent(agent_id) is None
        cur = await db.conn.execute(
            "SELECT 1 FROM pairing_tokens WHERE claimed_agent_id = ?",
            (agent_id,),
        )
        assert await cur.fetchone() is None


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
        assert row[0] == 9


class TestAdminOwnedAgentPurgeMigration:
    """Verify that migration v4 purges admin-owned agents + dependents.

    Simulates a pre-migration DB (user_version=3) that contains an
    admin-owned agent with pairings, pairing tokens and mobile refresh
    tokens. Opening the DB must cascade-delete all of them.
    """

    async def test_purges_admin_agent_and_all_dependents(self, tmp_path):
        db_path = str(tmp_path / "pre_migration.db")

        # Seed at version 3.
        db = Database(db_path)
        await db.connect()
        admin_id = await db.create_user(
            email="admin@legacy.local", password_hash="h", role="admin"
        )
        viewer_id = await db.create_user(
            email="viewer@legacy.local", password_hash="h", role="viewer"
        )
        admin_agent_id, _ = await db.register_agent("admin-owned", admin_id)
        viewer_agent_id, _ = await db.register_agent("viewer-owned", viewer_id)
        admin_pairing_id = await db.create_pairing(admin_agent_id, "dev-a")
        viewer_pairing_id = await db.create_pairing(viewer_agent_id, "dev-v")
        await db.create_pairing_token(admin_agent_id, 300)
        await db.create_pairing_token(viewer_agent_id, 300)
        await db.store_refresh_token("r-admin", "dev-a", admin_agent_id, 60)
        await db.store_refresh_token("r-viewer", "dev-v", viewer_agent_id, 60)
        # Roll the schema version back to before the purge migration.
        await db.conn.execute("PRAGMA user_version = 3")
        await db.conn.commit()
        await db.close()

        # Reopen — should run the v4 migration.
        db = Database(db_path)
        await db.connect()

        cursor = await db.conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        assert row[0] == 9

        assert await db.get_agent(admin_agent_id) is None
        assert await db.list_pairings_for_agent(admin_agent_id) == []
        assert await db.get_pending_pairing_tokens(admin_agent_id) == []
        assert await db.validate_and_consume_refresh("r-admin") is None

        # Viewer-owned data must be untouched.
        viewer_agent = await db.get_agent(viewer_agent_id)
        assert viewer_agent is not None
        assert viewer_agent["name"] == "viewer-owned"
        assert await db.validate_and_consume_refresh("r-viewer") == (
            "dev-v",
            viewer_agent_id,
        )
        # Pairing IDs are still meaningful references to the pre-purge data.
        del admin_pairing_id, viewer_pairing_id

        await db.close()

    async def test_idempotent_on_already_migrated_db(self, tmp_path):
        db_path = str(tmp_path / "post_migration.db")
        db = Database(db_path)
        await db.connect()
        await db.close()

        # Reopen — version is already 9, migration is a no-op.
        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        assert row[0] == 9
        await db.close()


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


class TestRefreshTokenNeverExpires:
    """store_refresh_token(expire_minutes=0) writes the year-9999 sentinel
    and validate_and_consume_refresh still accepts the token.
    """

    async def test_zero_ttl_writes_sentinel_expires_at(self, db: Database):
        from central_server.db.database import _NEVER_EXPIRES_AT, _hash_token

        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)

        cursor = await db.conn.execute(
            "SELECT expires_at FROM mobile_refresh_tokens WHERE token_hash = ?",
            (_hash_token("r-forever"),),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["expires_at"] == _NEVER_EXPIRES_AT
        assert row["expires_at"].startswith("9999-12-31")

    async def test_zero_ttl_token_validates_and_consumes(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)

        result = await db.validate_and_consume_refresh("r-forever")
        assert result == ("device-1", agent_id)
        # Rotation semantics still apply — single-use.
        assert await db.validate_and_consume_refresh("r-forever") is None

    async def test_zero_ttl_token_comparable_to_positive_ttl(self, db: Database):
        """A 0-TTL token (sentinel) and a positive-TTL token live side-by-side
        and both validate correctly.
        """
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)
        await db.store_refresh_token("r-short", "device-1", agent_id, 60)

        assert await db.validate_and_consume_refresh("r-forever") == (
            "device-1",
            agent_id,
        )
        assert await db.validate_and_consume_refresh("r-short") == (
            "device-1",
            agent_id,
        )

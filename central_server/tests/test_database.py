"""Tests for the Database layer."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

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


async def _schema_head(tmp_path) -> int:
    """The ``user_version`` a brand-new database migrates to."""
    fresh = Database(str(tmp_path / "schema_head.db"))
    await fresh.connect()
    try:
        cursor = await fresh.conn.execute("PRAGMA user_version")
        return (await cursor.fetchone())[0]
    finally:
        await fresh.close()


class TestAgentCRUD:
    async def test_register_and_verify(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("my-agent", owner)
        assert len(agent_id) == 12
        assert len(raw_token) > 20
        assert await db.verify_agent_token(agent_id, raw_token) is True
        assert await db.verify_agent_token(agent_id, "wrong") is False

    async def test_bulk_touch_agents_last_seen(self, db: Database):
        owner = await _create_test_user(db)
        a1, _ = await db.register_agent("a1", owner)
        a2, _ = await db.register_agent("a2", owner)
        t1 = datetime(2030, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2030, 2, 2, tzinfo=timezone.utc)
        t3 = datetime(2030, 3, 3, tzinfo=timezone.utc)
        # An id with no DB row (deleted mid-sweep) must be a harmless no-op.
        await db.bulk_touch_agents_last_seen(
            [(a1, t1), (a2, t2), ("ghost", t3)]
        )
        assert (await db.get_agent(a1))["last_seen_at"] == t1.isoformat()
        assert (await db.get_agent(a2))["last_seen_at"] == t2.isoformat()

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
        await db.create_pairing_token(agent_id, 300)
        await db.store_refresh_token("r1", "device-1", agent_id, 60)

        assert await db.delete_agent(agent_id) is True
        assert await db.get_agent(agent_id) is None
        # Cascade: pairing tokens and refresh tokens are gone. Mobile
        # devices are user-scoped and outlive any one agent.
        assert await db.get_pending_pairing_tokens(agent_id) == []
        assert await db.validate_and_consume_refresh("r1") is None

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


class TestListUsersFilterSortPage:
    """The keyword filters, sort keys and paging of
    ``list_users_with_agents`` plus the ``count_users`` that shares its
    filter. Creates are spaced a millisecond apart so ``created_at``
    orders strictly and the ``u.id`` tiebreak never decides a test.
    """

    async def _seed(self, db: Database, *emails: str, **kwargs) -> list[str]:
        import asyncio

        ids = []
        for email in emails:
            ids.append(
                await db.create_user(email=email, password_hash="h", **kwargs)
            )
            await asyncio.sleep(0.001)
        return ids

    async def test_q_matches_email_substring_case_insensitively(
        self, db: Database
    ):
        alice, _bob = await self._seed(db, "Alice@test.local", "bob@test.local")

        for needle in ("ALI", "  ali "):
            users = await db.list_users_with_agents(q=needle)
            assert [u["id"] for u in users] == [alice]
            assert await db.count_users(q=needle) == 1

    async def test_q_treats_like_wildcards_as_literal_characters(
        self, db: Database
    ):
        """``%`` and ``_`` must match themselves, not "anything" / "any
        one character" — the needle goes through ``instr``, not ``LIKE``.
        """
        pct, _pct_x, under, _under_x = await self._seed(
            db,
            "pct%x@test.local", "pctax@test.local",
            "u_1@test.local", "ux1@test.local",
        )

        assert [u["id"] for u in await db.list_users_with_agents(q="pct%")] == [
            pct
        ]
        assert [u["id"] for u in await db.list_users_with_agents(q="u_1")] == [
            under
        ]
        assert await db.count_users(q="pct%") == 1
        assert await db.count_users(q="u_1") == 1

    async def test_q_finds_a_non_ascii_address_by_its_exact_spelling(
        self, db: Database
    ):
        """Both operands are folded by SQLite's ASCII-only ``lower()``.
        Folding the needle in Python instead would turn ``Ü`` into ``ü``
        on one side only and miss the stored address.
        """
        unal, _ascii = await self._seed(
            db, "Ünal@test.local", "unal@test.local"
        )

        for needle in ("Ünal", "ÜNAL"):
            users = await db.list_users_with_agents(q=needle)
            assert [u["id"] for u in users] == [unal], needle
            assert await db.count_users(q=needle) == 1, needle

    async def test_verified_filter_tristate_and_count(self, db: Database):
        (vouched,) = await self._seed(
            db, "vouched@test.local", email_verified=True
        )
        (pending,) = await self._seed(db, "pending@test.local")

        assert [u["id"] for u in await db.list_users_with_agents(verified=True)] == [
            vouched
        ]
        assert [u["id"] for u in await db.list_users_with_agents(verified=False)] == [
            pending
        ]
        assert len(await db.list_users_with_agents(verified=None)) == 2
        assert await db.count_users(verified=True) == 1
        assert await db.count_users(verified=False) == 1
        assert await db.count_users() == 2

    async def test_sort_activity_keys_on_latest_agent_and_places_null_at_the_low_end(
        self, db: Database
    ):
        """``activity`` orders by ``MAX(agents.last_seen_at)`` per user;
        users whose agents were never seen and agentless users both have
        a NULL key, which sorts last descending and first ascending.
        NULL ties fall back to the ``created_at`` tiebreak in the same
        direction.
        """
        busy, quiet, never, agentless = await self._seed(
            db,
            "busy@test.local", "quiet@test.local",
            "never@test.local", "agentless@test.local",
        )
        busy_old, _ = await db.register_agent("busy-old", busy)
        busy_new, _ = await db.register_agent("busy-new", busy)
        quiet_agent, _ = await db.register_agent("quiet-a", quiet)
        await db.register_agent("never-seen", never)
        await db.touch_agent_last_seen(
            busy_old, datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        await db.touch_agent_last_seen(
            busy_new, datetime(2024, 3, 1, tzinfo=timezone.utc)
        )
        await db.touch_agent_last_seen(
            quiet_agent, datetime(2024, 2, 1, tzinfo=timezone.utc)
        )

        newest_first = await db.list_users_with_agents(sort="activity")
        assert [u["id"] for u in newest_first] == [busy, quiet, agentless, never]

        stalest_first = await db.list_users_with_agents(
            sort="activity", descending=False
        )
        assert [u["id"] for u in stalest_first] == [never, agentless, quiet, busy]

    async def test_limit_offset_page_users_not_join_rows(self, db: Database):
        """A page of two users must hold two *users* with every one of
        their agents nested, not two LEFT JOIN rows."""
        oldest, middle, newest = await self._seed(
            db, "oldest@test.local", "middle@test.local", "newest@test.local"
        )
        for name in ("n1", "n2", "n3"):
            await db.register_agent(name, newest)
        await db.register_agent("m1", middle)

        page_one = await db.list_users_with_agents(limit=2, offset=0)
        assert [u["id"] for u in page_one] == [newest, middle]
        assert {a["name"] for a in page_one[0]["agents"]} == {"n1", "n2", "n3"}
        assert [a["name"] for a in page_one[1]["agents"]] == ["m1"]

        page_two = await db.list_users_with_agents(limit=2, offset=2)
        assert [u["id"] for u in page_two] == [oldest]
        assert page_two[0]["agents"] == []

        assert await db.list_users_with_agents(limit=2, offset=4) == []
        assert len(await db.list_users_with_agents(limit=None)) == 3

    async def test_paging_applies_after_filter_and_sort(self, db: Database):
        a1, a2, a3, _b1 = await self._seed(
            db, "alpha1@x.io", "alpha2@x.io", "alpha3@x.io", "beta1@x.io"
        )

        page = await db.list_users_with_agents(
            q="alpha", descending=False, limit=2, offset=2
        )
        assert [u["id"] for u in page] == [a3]
        assert await db.count_users(q="alpha") == 3
        assert [
            u["id"]
            for u in await db.list_users_with_agents(
                q="alpha", descending=False, limit=2
            )
        ] == [a1, a2]


class TestDeleteUser:
    """``Database.delete_user`` cascade — explicit per-table sequence that
    returns the deleted agent IDs so the router can tear down live WSs.
    """

    async def test_delete_user_cascades_all_dependent_rows(
        self, db: Database
    ):
        """End-to-end cascade across every table the docstring enumerates:
        ``mobile_refresh_tokens``, ``pairing_tokens``, ``mobile_devices``,
        ``agents``, ``email_verification_tokens``, ``prompt_templates``,
        ``user_shortcuts``, ``users``. Establish at least one row in each
        (across two agents to exercise the IN-subquery shape) and assert
        each table is empty for the deleted user afterward.
        """
        owner = await db.create_user(email="o@test.local", password_hash="h")
        a1, _ = await db.register_agent("a1", owner)
        a2, _ = await db.register_agent("a2", owner)

        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        await db.upsert_mobile_device(device_id="dev-2", user_id=owner, name="B")
        await db.create_pairing_token(a1, 300)
        await db.create_pairing_token(a2, 300)
        await db.store_refresh_token("r-1", "dev-1", a1, 60)
        await db.store_refresh_token("r-2", "dev-2", a2, 60)
        await db.create_template(owner, "tpl-1", "body-1")
        await db.create_template(owner, "tpl-2", "body-2")
        s1 = await db.create_user_shortcut(owner, "claude", "ctrl+x", "Cut")
        s2 = await db.create_user_shortcut(owner, "codex", "ctrl+k", None)
        await db.create_verification_token(owner, "code-hash", ttl_seconds=300)

        agent_ids = await db.delete_user(owner)
        assert set(agent_ids) == {a1, a2}

        assert await db.get_user_by_id(owner) is None
        assert await db.get_agent(a1) is None
        assert await db.get_agent(a2) is None
        assert await db.list_user_devices(owner) == []
        assert await db.get_pending_pairing_tokens(a1) == []
        assert await db.get_pending_pairing_tokens(a2) == []
        assert await db.validate_and_consume_refresh("r-1") is None
        assert await db.validate_and_consume_refresh("r-2") is None
        assert await db.list_templates_for_user(owner) == []
        # Shortcuts are hard-deleted by the cascade, not soft-deleted.
        assert await db.get_user_shortcut(s1["shortcut_id"]) is None
        assert await db.get_user_shortcut(s2["shortcut_id"]) is None
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
        A second user's agents, devices, refresh tokens, and templates
        must be untouched.
        """
        victim = await db.create_user(email="victim@test.local", password_hash="h")
        survivor = await db.create_user(email="survivor@test.local", password_hash="h")
        v_agent, _ = await db.register_agent("v-agent", victim)
        s_agent, _ = await db.register_agent("s-agent", survivor)

        await db.upsert_mobile_device(
            device_id="v-dev", user_id=victim, name="Victim phone"
        )
        await db.upsert_mobile_device(
            device_id="s-dev", user_id=survivor, name="Survivor phone"
        )
        await db.store_refresh_token("v-r", "v-dev", v_agent, 60)
        await db.store_refresh_token("s-r", "s-dev", s_agent, 60)
        await db.create_template(victim, "v-tpl", "v")
        await db.create_template(survivor, "s-tpl", "s")
        await db.create_user_shortcut(victim, "claude", "ctrl+x", None)
        await db.create_user_shortcut(survivor, "claude", "ctrl+x", None)

        agent_ids = await db.delete_user(victim)
        assert agent_ids == [v_agent]

        assert await db.get_user_by_id(survivor) is not None
        assert await db.get_agent(s_agent) is not None
        survivor_devices = await db.list_user_devices(survivor)
        assert [d["device_id"] for d in survivor_devices] == ["s-dev"]
        assert await db.validate_and_consume_refresh("s-r") == ("s-dev", s_agent)
        templates = await db.list_templates_for_user(survivor)
        assert len(templates) == 1
        assert templates[0]["name"] == "s-tpl"
        shortcuts = await db.list_user_shortcuts_for_user(survivor)
        assert len(shortcuts) == 1
        assert shortcuts[0]["chord_token"] == "ctrl+x"

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

    async def test_cleanup_expired(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.create_pairing_token(agent_id, 0)
        await db.create_pairing_token(agent_id, 300)
        deleted = await db.cleanup_expired_tokens()
        assert deleted == 1


class TestMobileDevices:
    """Coverage for the user-scoped ``mobile_devices`` registry.

    Devices are keyed by a client-generated UUID and are independent of any
    one agent. Upsert is idempotent, re-activates a previously revoked
    device on fresh pairing (Bitwarden semantics), and is last-login-wins
    across users: a cross-user upsert hands the device over, returning the
    previous owner's id and revoking their live refresh tokens.
    """

    async def test_upsert_hands_over_in_use_device_and_revokes_tokens(
        self, db: Database
    ):
        """Last-login-wins: even a device with a live refresh token is
        handed over to the new authenticated claimant. The upsert returns
        the previous owner's id, moves the row, and kills the prior
        owner's live session."""
        u_a = await db.create_user(email="a@test.local", password_hash="h")
        u_b = await db.create_user(email="b@test.local", password_hash="h")
        agent_a, _ = await db.register_agent("a-agent", u_a)
        assert await db.upsert_mobile_device(
            device_id="dev-x", user_id=u_a, name="A's device"
        ) is None
        # A live session no longer pins the device to A.
        await db.store_refresh_token("a-refresh", "dev-x", agent_a, 60)

        previous_owner = await db.upsert_mobile_device(
            device_id="dev-x", user_id=u_b, name="B's phone"
        )

        assert previous_owner == u_a
        # The device moved to B and left A's list.
        assert await db.list_user_devices(u_a) == []
        b_devices = await db.list_user_devices(u_b)
        assert [d["name"] for d in b_devices] == ["B's phone"]
        # A's refresh token died with the handover.
        assert await db.validate_and_consume_refresh("a-refresh") is None

    async def test_handover_revokes_tokens_across_all_agents(
        self, db: Database
    ):
        """The handover revokes the prior owner's live refresh tokens for
        *every* agent paired on the device, not just one."""
        u_a = await db.create_user(email="a@test.local", password_hash="h")
        u_b = await db.create_user(email="b@test.local", password_hash="h")
        agent_1, _ = await db.register_agent("a-agent-1", u_a)
        agent_2, _ = await db.register_agent("a-agent-2", u_a)
        await db.upsert_mobile_device(device_id="dev-x", user_id=u_a, name="A")
        await db.store_refresh_token("a-refresh-1", "dev-x", agent_1, 60)
        await db.store_refresh_token("a-refresh-2", "dev-x", agent_2, 60)

        assert await db.upsert_mobile_device(
            device_id="dev-x", user_id=u_b, name="B"
        ) == u_a

        assert await db.validate_and_consume_refresh("a-refresh-1") is None
        assert await db.validate_and_consume_refresh("a-refresh-2") is None

    async def test_same_user_upsert_keeps_live_refresh_tokens(
        self, db: Database
    ):
        """A same-owner re-upsert (routine token re-mint) is not a
        handover and must not revoke the device's live refresh tokens."""
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a", owner)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        await db.store_refresh_token("r-live", "dev-1", agent_id, 60)

        assert await db.upsert_mobile_device(
            device_id="dev-1", user_id=owner, name="A"
        ) is None

        assert await db.validate_and_consume_refresh("r-live") is not None

    async def test_upsert_reassigns_idle_device_cross_tenant(
        self, db: Database
    ):
        """An idle device (no live refresh token) re-pairs under a new
        account — the same physical phone signing into a different user.
        The device moves to B and leaves A's list."""
        u_a = await db.create_user(email="a@test.local", password_hash="h")
        u_b = await db.create_user(email="b@test.local", password_hash="h")
        await db.upsert_mobile_device(device_id="dev-x", user_id=u_a, name="A")
        # Revoking leaves the device idle (no live token).
        await db.revoke_mobile_device("dev-x", u_a)
        assert await db.upsert_mobile_device(
            device_id="dev-x", user_id=u_b, name="B's phone"
        ) == u_a
        # The device is now B's, active, and gone from A's list.
        assert await db.list_user_devices(u_a) == []
        b_devices = await db.list_user_devices(u_b)
        assert [d["name"] for d in b_devices] == ["B's phone"]

    async def test_revoke_removes_from_list(self, db: Database):
        owner = await _create_test_user(db)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        assert await db.revoke_mobile_device("dev-1", owner) is True
        assert await db.list_user_devices(owner) == []
        assert await db.revoke_mobile_device("dev-1", owner) is False

    async def test_revoke_scoped_to_owner(self, db: Database):
        u1 = await db.create_user(email="u1@test.local", password_hash="h")
        u2 = await db.create_user(email="u2@test.local", password_hash="h")
        await db.upsert_mobile_device(device_id="dev-x", user_id=u1, name="X")
        assert await db.revoke_mobile_device("dev-x", u2) is False
        assert len(await db.list_user_devices(u1)) == 1

    async def test_upsert_reactivates_revoked_device(self, db: Database):
        owner = await _create_test_user(db)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        await db.revoke_mobile_device("dev-1", owner)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name=None)
        devices = await db.list_user_devices(owner)
        assert [d["device_id"] for d in devices] == ["dev-1"]

    async def test_revoke_sets_a_watermark_that_re_registration_keeps(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        assert await db.device_tokens_valid_after("dev-1") is None
        before = int(datetime.now(timezone.utc).timestamp())

        await db.revoke_mobile_device("dev-1", owner)
        watermark = await db.device_tokens_valid_after("dev-1")
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")

        assert watermark is not None and watermark >= before
        # Re-registration revives the device, never the tokens it held before.
        assert await db.device_tokens_valid_after("dev-1") == watermark
        assert await db.device_tokens_valid_after("dev-unknown") is None

    async def test_revoke_keeps_the_later_watermark_and_a_refused_revoke_writes_none(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        stranger = await db.create_user(email="stranger@test.local", password_hash="h")
        for device_id in ("dev-later", "dev-earlier", "dev-foreign"):
            await db.upsert_mobile_device(device_id=device_id, user_id=owner, name="A")
        now = int(datetime.now(timezone.utc).timestamp())
        await _set_watermark(db, "dev-later", now + 3600)
        await _set_watermark(db, "dev-earlier", now - 3600)

        await db.revoke_mobile_device("dev-later", owner)
        await db.revoke_mobile_device("dev-earlier", owner)
        assert await db.revoke_mobile_device("dev-foreign", stranger) is False

        assert await db.device_tokens_valid_after("dev-later") == now + 3600
        assert await db.device_tokens_valid_after("dev-earlier") >= now
        assert await db.device_tokens_valid_after("dev-foreign") is None

    async def test_watermark_outlives_a_handover_and_the_new_owners_deletion(
        self, db: Database
    ):
        first = await _create_test_user(db)
        second = await db.create_user(email="second@test.local", password_hash="h")
        await db.upsert_mobile_device(device_id="dev-1", user_id=first, name="A")
        await db.revoke_mobile_device("dev-1", first)
        watermark = await db.device_tokens_valid_after("dev-1")

        previous = await db.upsert_mobile_device(
            device_id="dev-1", user_id=second, name="B"
        )
        await db.delete_user(second)

        assert previous == first
        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM mobile_devices WHERE device_id = 'dev-1'"
        )
        assert (await cursor.fetchone())[0] == 0
        assert watermark is not None
        assert await db.device_tokens_valid_after("dev-1") == watermark

    async def test_revoke_user_refresh_tokens_for_device_spares_other_sessions(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        other = await db.create_user(email="other@test.local", password_hash="h")
        await db.store_user_refresh_token("bound", owner, 60, device_id="dev-1")
        await db.store_user_refresh_token("other-dev", owner, 60, device_id="dev-2")
        await db.store_user_refresh_token("unbound", owner, 60)
        await db.store_user_refresh_token("other-user", other, 60, device_id="dev-1")

        count = await db.revoke_user_refresh_tokens_for_device(owner, "dev-1")

        assert count == 1
        assert await db.validate_and_consume_user_refresh("bound") is None
        assert (await db.validate_and_consume_user_refresh("other-dev"))[:2] == (
            owner,
            "dev-2",
        )
        assert (await db.validate_and_consume_user_refresh("unbound"))[:2] == (
            owner,
            None,
        )
        assert (await db.validate_and_consume_user_refresh("other-user"))[:2] == (
            other,
            "dev-1",
        )

    async def test_user_refresh_auth_time_defaults_to_the_rows_second(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        await db.store_user_refresh_token("signed-in", owner, 60)
        created_second, _ = await _refresh_row_seconds(db)
        await db.store_user_refresh_token("rotated", owner, 60, auth_time=1_700_000_000)

        _, _, signed_in_at = await db.validate_and_consume_user_refresh("signed-in")
        _, _, rotated_at = await db.validate_and_consume_user_refresh("rotated")

        assert signed_in_at == created_second
        # A rotation passes the consumed row's value, which is kept as is.
        assert rotated_at == 1_700_000_000

    async def test_revoke_refresh_tokens_for_device(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a", owner)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        await db.store_refresh_token("r-1", "dev-1", agent_id, 60)
        count = await db.revoke_all_refresh_tokens_for_device("dev-1")
        assert count == 1
        assert await db.validate_and_consume_refresh("r-1") is None

    async def test_consume_refuses_a_live_refresh_row_from_the_watermark_second(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a", owner)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        await db.store_refresh_token("r-mobile", "dev-1", agent_id, 60)
        await db.store_user_refresh_token("r-user", owner, 60, device_id="dev-1")
        first, last = await _refresh_row_seconds(db)

        await _set_watermark(db, "dev-1", last)
        refused_mobile = await db.validate_and_consume_refresh("r-mobile")
        refused_user = await db.validate_and_consume_user_refresh("r-user")
        # The refusal did not consume the rows: once the watermark is an
        # earlier second, the same tokens rotate.
        await _set_watermark(db, "dev-1", first - 1)

        assert refused_mobile is None
        assert refused_user is None
        assert await db.validate_and_consume_refresh("r-mobile") == ("dev-1", agent_id)
        assert (await db.validate_and_consume_user_refresh("r-user"))[:2] == (
            owner,
            "dev-1",
        )

    async def test_consume_ignores_watermarks_of_other_or_unregistered_devices(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("a", owner)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        await db.store_user_refresh_token("unbound", owner, 60)
        await db.store_refresh_token("ghost-mobile", "dev-ghost", agent_id, 60)
        await db.store_user_refresh_token("ghost-user", owner, 60, device_id="dev-ghost")
        _, last = await _refresh_row_seconds(db)
        await _set_watermark(db, "dev-1", last + 60)

        assert (await db.validate_and_consume_user_refresh("unbound"))[:2] == (
            owner,
            None,
        )
        assert await db.validate_and_consume_refresh("ghost-mobile") == (
            "dev-ghost",
            agent_id,
        )
        assert (await db.validate_and_consume_user_refresh("ghost-user"))[:2] == (
            owner,
            "dev-ghost",
        )


async def _set_watermark(db: Database, device_id: str, valid_after: int) -> None:
    await db.conn.execute(
        "INSERT INTO device_revocations (device_id, tokens_valid_after) "
        "VALUES (?, ?) ON CONFLICT(device_id) DO UPDATE SET "
        "    tokens_valid_after = excluded.tokens_valid_after",
        (device_id, valid_after),
    )
    await db.conn.commit()


async def _refresh_row_seconds(db: Database) -> tuple[int, int]:
    """Oldest and newest ``created_at`` across both refresh tables, in epoch
    seconds, so a watermark can sit on either side of every row."""
    cursor = await db.conn.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM ("
        "    SELECT created_at FROM mobile_refresh_tokens"
        "    UNION ALL SELECT created_at FROM user_refresh_tokens)"
    )
    oldest, newest = await cursor.fetchone()
    return (
        int(datetime.fromisoformat(oldest).timestamp()),
        int(datetime.fromisoformat(newest).timestamp()),
    )


class TestPushTokens:
    """Push-token lifecycle on ``mobile_devices``: registration,
    rotation, owner-scoped, token-matched and account-wide clears,
    cross-tenant reassign wipe, and the dispatcher's target listing."""

    async def _register(self, db, owner, device_id="dev-1"):
        await db.upsert_mobile_device(
            device_id=device_id, user_id=owner, name="iPhone", platform="ios"
        )

    async def test_set_and_list_push_targets(self, db: Database):
        owner = await _create_test_user(db)
        await self._register(db, owner)
        assert await db.set_push_token(
            "dev-1", owner, "ios", "tok-1", "sandbox"
        ) is True

        targets = await db.list_push_targets(owner)
        assert targets == [
            {
                "device_id": "dev-1",
                "platform": "ios",
                "push_token": "tok-1",
                "push_environment": "sandbox",
            }
        ]

    async def test_set_scoped_to_owner_and_active(self, db: Database):
        u1 = await db.create_user(email="u1@test.local", password_hash="h")
        u2 = await db.create_user(email="u2@test.local", password_hash="h")
        await self._register(db, u1)
        assert await db.set_push_token(
            "dev-1", u2, "ios", "tok-x", "sandbox"
        ) is False
        assert await db.set_push_token(
            "dev-missing", u1, "ios", "tok-x", "sandbox"
        ) is False
        await db.revoke_mobile_device("dev-1", u1)
        assert await db.set_push_token(
            "dev-1", u1, "ios", "tok-x", "sandbox"
        ) is False

    async def test_clear_push_token_is_token_matched(self, db: Database):
        owner = await _create_test_user(db)
        await self._register(db, owner)
        await db.set_push_token("dev-1", owner, "ios", "tok-old", "sandbox")
        # A rotation lands while a send with the old token is in flight …
        await db.set_push_token("dev-1", owner, "ios", "tok-new", "sandbox")
        # … so the stale 410 cleanup must not wipe the fresh token.
        await db.clear_push_token("dev-1", "tok-old")
        assert [
            t["push_token"] for t in await db.list_push_targets(owner)
        ] == ["tok-new"]

        await db.clear_push_token("dev-1", "tok-new")
        assert await db.list_push_targets(owner) == []

    async def test_clear_for_owner_scoped(self, db: Database):
        u1 = await db.create_user(email="u1@test.local", password_hash="h")
        u2 = await db.create_user(email="u2@test.local", password_hash="h")
        await self._register(db, u1)
        await db.set_push_token("dev-1", u1, "ios", "tok-1", "sandbox")

        assert await db.clear_push_token_for_owner("dev-1", u2) is False
        assert len(await db.list_push_targets(u1)) == 1

        assert await db.clear_push_token_for_owner("dev-1", u1) is True
        assert await db.list_push_targets(u1) == []
        # Idempotent for an owned active device without a token.
        assert await db.clear_push_token_for_owner("dev-1", u1) is True

    async def test_revoke_clears_push_token(self, db: Database):
        owner = await _create_test_user(db)
        await self._register(db, owner)
        await db.set_push_token("dev-1", owner, "ios", "tok-1", "sandbox")
        await db.revoke_mobile_device("dev-1", owner)

        # Re-activating under the same owner must not resurrect the token.
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner)
        assert await db.list_push_targets(owner) == []

    async def test_cross_tenant_reassign_clears_push_token(self, db: Database):
        u_a = await db.create_user(email="a@test.local", password_hash="h")
        u_b = await db.create_user(email="b@test.local", password_hash="h")
        await self._register(db, u_a, "dev-x")
        await db.set_push_token("dev-x", u_a, "ios", "tok-a", "sandbox")
        # The device re-pairs under B (last-login-wins handover).
        assert await db.upsert_mobile_device(
            device_id="dev-x", user_id=u_b, name="B's phone"
        ) == u_a

        assert await db.list_push_targets(u_a) == []
        assert await db.list_push_targets(u_b) == []

    async def test_list_excludes_revoked_and_tokenless(self, db: Database):
        owner = await _create_test_user(db)
        await self._register(db, owner, "dev-token")
        await self._register(db, owner, "dev-bare")
        await self._register(db, owner, "dev-revoked")
        await db.set_push_token("dev-token", owner, "ios", "tok-1", "sandbox")
        await db.set_push_token("dev-revoked", owner, "ios", "tok-2", "sandbox")
        await db.revoke_mobile_device("dev-revoked", owner)

        targets = await db.list_push_targets(owner)
        assert [t["device_id"] for t in targets] == ["dev-token"]

    async def test_clear_for_user_drops_every_token_and_counts_them(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        await self._register(db, owner, "dev-a")
        await self._register(db, owner, "dev-b")
        await self._register(db, owner, "dev-bare")
        await db.set_push_token("dev-a", owner, "ios", "tok-a", "sandbox")
        await db.set_push_token("dev-b", owner, "ios", "tok-b", "production")

        # Only devices that actually held a token count — the log line the
        # deletion path emits must not overstate the blast radius.
        assert await db.clear_push_tokens_for_user(owner) == 2
        assert await db.list_push_targets(owner) == []
        # Idempotent: nothing left to clear on a second pass.
        assert await db.clear_push_tokens_for_user(owner) == 0

    async def test_clear_for_user_leaves_other_users_tokens(self, db: Database):
        u1 = await db.create_user(email="u1@test.local", password_hash="h")
        u2 = await db.create_user(email="u2@test.local", password_hash="h")
        await self._register(db, u1, "dev-1")
        await self._register(db, u2, "dev-2")
        await db.set_push_token("dev-1", u1, "ios", "tok-1", "sandbox")
        await db.set_push_token("dev-2", u2, "ios", "tok-2", "sandbox")

        assert await db.clear_push_tokens_for_user(u1) == 1
        assert await db.list_push_targets(u1) == []
        assert [
            t["push_token"] for t in await db.list_push_targets(u2)
        ] == ["tok-2"]


class TestPushColumnsMigration:
    async def test_v15_adds_push_columns_to_existing_db(self, tmp_path):
        db_path = str(tmp_path / "pre_push.db")
        db = Database(db_path)
        await db.connect()
        owner = await _create_test_user(db)
        await db.upsert_mobile_device(device_id="dev-1", user_id=owner, name="A")
        # Simulate a pre-v15 on-disk schema: drop the push columns and
        # roll user_version back so reopening replays the v15 block.
        for column in ("platform", "push_token", "push_environment"):
            await db.conn.execute(
                f"ALTER TABLE mobile_devices DROP COLUMN {column}"
            )
        await db.conn.execute("PRAGMA user_version = 14")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == await _schema_head(tmp_path)
        cursor = await db.conn.execute("PRAGMA table_info(mobile_devices)")
        columns = {r[1] for r in await cursor.fetchall()}
        assert {"platform", "push_token", "push_environment"} <= columns
        # Pre-existing rows survive with NULL push columns.
        assert await db.set_push_token(
            "dev-1", owner, "ios", "tok-1", "sandbox"
        ) is True
        await db.close()


class TestUserCRUD:
    async def test_duplicate_email_raises(self, db: Database):
        await db.create_user(email="dup@example.com")
        with pytest.raises(Exception):  # aiosqlite raises IntegrityError
            await db.create_user(email="dup@example.com")


class TestUnverifiedSignupGuards:
    async def test_consumed_code_within_ttl_blocks_takeover(self, db: Database):
        user_id = await db.create_user(
            email="u@example.com", password_hash="owner-hash"
        )
        await db.create_verification_token(user_id, "code-hash", ttl_seconds=300)
        assert await db.consume_verification_token(user_id, "code-hash", 5) == "ok"

        assert await db.has_live_verification_token(user_id) is True
        assert await db.take_over_unverified_signup(user_id, "new-hash") is False
        user = await db.get_user_by_id(user_id)
        assert user["password_hash"] == "owner-hash"

    async def test_expired_code_allows_takeover(self, db: Database):
        user_id = await db.create_user(
            email="u@example.com", password_hash="owner-hash"
        )
        await db.create_verification_token(user_id, "code-hash", ttl_seconds=300)
        await db.conn.execute(
            "UPDATE email_verification_tokens SET expires_at = ? WHERE user_id = ?",
            ("2000-01-01T00:00:00+00:00", user_id),
        )
        await db.conn.commit()

        assert await db.has_live_verification_token(user_id) is False
        assert await db.take_over_unverified_signup(user_id, "new-hash") is True
        user = await db.get_user_by_id(user_id)
        assert user["password_hash"] == "new-hash"

    async def test_mark_user_verified_installs_a_given_hash(self, db: Database):
        user_id = await db.create_user(email="u@example.com", password_hash="old")

        await db.mark_user_verified(user_id, "typed-hash")

        user = await db.get_user_by_id(user_id)
        assert (user["email_verified"], user["password_hash"]) == (1, "typed-hash")

    async def test_clear_unverified_password_clears_an_unverified_row(
        self, db: Database
    ):
        user_id = await db.create_user(email="u@example.com", password_hash="h")

        await db.clear_unverified_password(user_id)

        assert (await db.get_user_by_id(user_id))["password_hash"] is None

    async def test_clear_unverified_password_leaves_a_verified_row_alone(
        self, db: Database
    ):
        user_id = await db.create_user(
            email="u@example.com", password_hash="h", email_verified=True
        )

        await db.clear_unverified_password(user_id)

        assert (await db.get_user_by_id(user_id))["password_hash"] == "h"


_CODE_TOKEN_KINDS = [
    pytest.param(
        "create_verification_token",
        "consume_verification_token",
        id="verification",
    ),
    pytest.param(
        "create_password_reset_token",
        "consume_password_reset_token",
        id="password_reset",
    ),
]


@pytest.mark.parametrize(("create_name", "consume_name"), _CODE_TOKEN_KINDS)
class TestCodeTokenAttemptCap:
    async def _issue(self, db: Database, create_name: str) -> str:
        user_id = await _create_test_user(db)
        await getattr(db, create_name)(user_id, "right-hash", ttl_seconds=300)
        return user_id

    async def test_concurrent_wrong_guesses_cannot_exceed_the_cap(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await self._issue(db, create_name)
        consume = getattr(db, consume_name)

        outcomes = await asyncio.gather(
            *(consume(user_id, f"wrong-{i}", 5) for i in range(50)),
            consume(user_id, "right-hash", 5),
        )

        assert outcomes[:-1].count("invalid") == 5
        assert outcomes[:-1].count("exhausted") == 45
        assert outcomes[-1] == "exhausted"

    async def test_rollbacks_from_other_requests_cannot_erase_counted_guesses(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await self._issue(db, create_name)
        consume = getattr(db, consume_name)
        await db.create_custom_agent_type(user_id, "dup", "echo")

        results = await asyncio.gather(
            *(
                call
                for i in range(50)
                for call in (
                    consume(user_id, f"wrong-{i}", 5),
                    db.create_custom_agent_type(user_id, "dup", "echo"),
                )
            ),
            return_exceptions=True,
        )
        outcomes = results[0::2]

        assert all(isinstance(r, sqlite3.IntegrityError) for r in results[1::2])
        assert outcomes.count("invalid") == 5
        assert outcomes.count("exhausted") == 45
        assert await consume(user_id, "right-hash", 5) == "exhausted"

    async def test_attempts_survive_close_and_reconnect(
        self, tmp_path, create_name: str, consume_name: str
    ):
        db_path = str(tmp_path / "reconnect.db")
        first = Database(db_path)
        await first.connect()
        user_id = await self._issue(first, create_name)
        for _ in range(5):
            assert await getattr(first, consume_name)(user_id, "wrong", 5) == "invalid"
        await first.close()

        second = Database(db_path)
        await second.connect()
        try:
            outcome = await getattr(second, consume_name)(user_id, "right-hash", 5)
        finally:
            await second.close()

        assert outcome == "exhausted"

    async def test_guess_during_open_multi_chunk_read_leaves_connection_usable(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await self._issue(db, create_name)
        consume = getattr(db, consume_name)
        for i in range(100):
            await db.create_user(email=f"reader-{i}@test.local", password_hash="h")

        cursor = await db.conn.execute("SELECT id FROM users")
        rows = cursor.__aiter__()
        await rows.__anext__()

        assert await consume(user_id, "wrong", 5) == "invalid"
        await db.update_user_password(user_id, "mid-read-hash")
        rest = [row async for row in rows]

        assert 1 + len(rest) == 101
        assert db.conn.in_transaction is False
        await db.update_user_password(user_id, "after-read-hash")
        user = await db.get_user_by_id(user_id)
        assert user["password_hash"] == "after-read-hash"
        assert await consume(user_id, "right-hash", 5) == "ok"

    async def test_concurrent_correct_guesses_consume_the_code_once(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await self._issue(db, create_name)
        consume = getattr(db, consume_name)

        outcomes = await asyncio.gather(
            *(consume(user_id, "right-hash", 5) for _ in range(3))
        )

        assert sorted(outcomes) == ["invalid", "invalid", "ok"]

    async def test_correct_guess_after_four_misses_succeeds(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await self._issue(db, create_name)
        consume = getattr(db, consume_name)
        for _ in range(4):
            assert await consume(user_id, "wrong", 5) == "invalid"

        assert await consume(user_id, "right-hash", 5) == "ok"

    async def test_correct_guess_after_five_misses_is_exhausted(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await self._issue(db, create_name)
        consume = getattr(db, consume_name)
        for _ in range(5):
            assert await consume(user_id, "wrong", 5) == "invalid"

        assert await consume(user_id, "right-hash", 5) == "exhausted"

    async def test_expired_code_reports_expired(
        self, db: Database, create_name: str, consume_name: str
    ):
        user_id = await _create_test_user(db)
        await getattr(db, create_name)(user_id, "right-hash", ttl_seconds=0)

        assert await getattr(db, consume_name)(user_id, "right-hash", 5) == "expired"


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
        # Seed legacy `pairings` rows via raw SQL — both the helper and the
        # table itself are gone from _SCHEMA in current code. Re-create the
        # table here to faithfully simulate the v3 on-disk state that
        # migration v13 is expected to DROP.
        await db.conn.execute(
            "CREATE TABLE IF NOT EXISTS pairings ("
            "    pairing_id       TEXT PRIMARY KEY,"
            "    agent_id         TEXT NOT NULL REFERENCES agents(agent_id),"
            "    mobile_device_id TEXT NOT NULL,"
            "    device_name      TEXT,"
            "    created_at       TEXT NOT NULL,"
            "    revoked_at       TEXT"
            ")"
        )
        for agent_id, dev in (
            (admin_agent_id, "dev-a"),
            (viewer_agent_id, "dev-v"),
        ):
            await db.conn.execute(
                "INSERT INTO pairings "
                "(pairing_id, agent_id, mobile_device_id, created_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (f"p-{dev}", agent_id, dev),
            )
        await db.create_pairing_token(admin_agent_id, 300)
        await db.create_pairing_token(viewer_agent_id, 300)
        await db.store_refresh_token("r-admin", "dev-a", admin_agent_id, 60)
        await db.store_refresh_token("r-viewer", "dev-v", viewer_agent_id, 60)
        # Roll the schema version back to before the purge migration.
        await db.conn.execute("PRAGMA user_version = 3")
        await db.conn.commit()
        await db.close()

        # Reopen — should run the v4 migration through to head.
        db = Database(db_path)
        await db.connect()

        cursor = await db.conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        assert row[0] == await _schema_head(tmp_path)

        # v4 purges admin-owned agents + their cascade rows.
        assert await db.get_agent(admin_agent_id) is None
        assert await db.get_pending_pairing_tokens(admin_agent_id) == []
        assert await db.validate_and_consume_refresh("r-admin") is None

        # Viewer agent survives the v4 cascade.
        viewer_agent = await db.get_agent(viewer_agent_id)
        assert viewer_agent is not None
        assert viewer_agent["name"] == "viewer-owned"

        # v13 unconditionally clears mobile_refresh_tokens so every paired
        # device re-pairs after the rename. The viewer's token therefore
        # also fails to validate after the upgrade — documented behaviour.
        assert await db.validate_and_consume_refresh("r-viewer") is None

        await db.close()


class TestRefreshTokens:
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

    async def test_zero_ttl_token_validates_and_consumes(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)

        result = await db.validate_and_consume_refresh("r-forever")
        assert result == ("device-1", agent_id)
        # Rotation semantics still apply — single-use.
        assert await db.validate_and_consume_refresh("r-forever") is None


async def _refresh_row(db: Database, raw_token: str) -> dict | None:
    from central_server.db.database import _hash_token

    cursor = await db.conn.execute(
        "SELECT created_at, expires_at FROM mobile_refresh_tokens "
        "WHERE token_hash = ?",
        (_hash_token(raw_token),),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


class TestCapNeverExpiringRefreshTokens:
    """Refresh tokens stored while the TTL was 0 get ``created_at + ttl``
    once the TTL is positive."""

    async def test_live_sentinel_row_expires_ttl_after_creation(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)

        capped = await db.cap_never_expiring_refresh_tokens(60)

        assert capped == 1
        row = await _refresh_row(db, "r-forever")
        assert datetime.fromisoformat(row["expires_at"]) == (
            datetime.fromisoformat(row["created_at"]) + timedelta(minutes=60)
        )
        assert await db.validate_and_consume_refresh("r-forever") == (
            "device-1",
            agent_id,
        )

    async def test_counts_only_live_sentinel_rows_and_runs_once(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)
        await db.store_refresh_token("r-short", "device-1", agent_id, 60)
        await db.store_refresh_token("r-used", "device-2", agent_id, 0)
        assert await db.validate_and_consume_refresh("r-used") is not None
        short_before = await _refresh_row(db, "r-short")

        assert await db.cap_never_expiring_refresh_tokens(43200) == 1
        assert await db.cap_never_expiring_refresh_tokens(43200) == 0
        assert await _refresh_row(db, "r-short") == short_before

    async def test_row_older_than_the_ttl_stops_validating_and_is_swept(
        self, db: Database
    ):
        from central_server.db.database import _hash_token

        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-stale", "device-1", agent_id, 0)
        created = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        await db.conn.execute(
            "UPDATE mobile_refresh_tokens SET created_at = ? WHERE token_hash = ?",
            (created, _hash_token("r-stale")),
        )
        await db.conn.commit()

        assert await db.cap_never_expiring_refresh_tokens(43200) == 1
        assert await db.validate_and_consume_refresh("r-stale") is None
        assert await db.cleanup_expired_tokens() == 1
        assert await _refresh_row(db, "r-stale") is None


class TestMobileRefreshTokenSweep:
    async def test_expired_rows_go_live_and_never_expiring_rows_stay(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-expired", "device-1", agent_id, -1)
        await db.store_refresh_token("r-live", "device-1", agent_id, 60)
        await db.store_refresh_token("r-forever", "device-1", agent_id, 0)

        assert await db.cleanup_expired_tokens() == 1

        assert await _refresh_row(db, "r-expired") is None
        assert await _refresh_row(db, "r-live") is not None
        assert await _refresh_row(db, "r-forever") is not None

    async def test_a_revoked_row_lingers_only_for_the_retention_window(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.store_refresh_token("r-rotated", "device-1", agent_id, 60)
        assert await db.validate_and_consume_refresh("r-rotated") is not None

        assert await db.cleanup_expired_tokens() == 0
        assert await _refresh_row(db, "r-rotated") is not None

        stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        await db.conn.execute(
            "UPDATE mobile_refresh_tokens SET revoked_at = ?", (stale,)
        )
        await db.conn.commit()

        assert await db.cleanup_expired_tokens() == 1
        assert await _refresh_row(db, "r-rotated") is None


class TestScheduledDeletionFields:
    """``set_scheduled_deletion`` / ``clear_scheduled_deletion`` /
    ``list_users_due_for_purge`` — the user-row column pair the sweeper
    reads on every interval."""

    async def test_list_users_due_for_purge_returns_only_past_due(
        self, db: Database
    ):
        """Sweeper filter: rows where ``scheduled_deletion_at <= now`` are
        returned; future-scheduled and never-scheduled rows are not."""
        u_past = await db.create_user(email="past@test.local", password_hash="h")
        u_future = await db.create_user(
            email="future@test.local", password_hash="h",
        )
        u_unscheduled = await db.create_user(
            email="never@test.local", password_hash="h",
        )

        past_iso = "2020-01-01T00:00:00+00:00"
        future_iso = "2099-01-01T00:00:00+00:00"
        await db.set_scheduled_deletion(
            u_past, scheduled_for=past_iso, requested_at=past_iso,
        )
        await db.set_scheduled_deletion(
            u_future, scheduled_for=future_iso, requested_at=past_iso,
        )

        # "Now" is between past_iso and future_iso.
        due = await db.list_users_due_for_purge("2024-06-01T00:00:00+00:00")
        ids = {row["id"] for row in due}
        assert u_past in ids
        assert u_future not in ids
        assert u_unscheduled not in ids

        # Each returned row carries the columns the sweeper re-checks.
        for row in due:
            assert "email" in row and row["email"]
            assert "scheduled_deletion_at" in row

    async def test_list_users_due_for_purge_excludes_boundary_when_strictly_after(
        self, db: Database
    ):
        """If now < scheduled_deletion_at, the user is NOT due. Boundary
        check: ``scheduled_deletion_at <= now`` includes exact equality."""
        user_id = await db.create_user(email="edge@test.local", password_hash="h")
        scheduled = "2030-06-15T12:00:00+00:00"
        await db.set_scheduled_deletion(
            user_id, scheduled_for=scheduled, requested_at=scheduled,
        )
        # Now is strictly before the schedule → not due.
        before = await db.list_users_due_for_purge("2030-06-15T11:59:59+00:00")
        assert all(r["id"] != user_id for r in before)
        # Now is exactly equal → due (the SQL uses <=).
        equal = await db.list_users_due_for_purge(scheduled)
        assert any(r["id"] == user_id for r in equal)


class TestDeletionAuditCrud:
    """``record_deletion_audit`` / ``update_deletion_audit_status`` /
    ``find_pending_audit_for_user`` — drives audit row lifecycle from
    request → terminal state."""

    async def test_update_status_to_completed_with_timestamp(self, db: Database):
        user_id = await db.create_user(email="done@test.local", password_hash="h")
        audit_id = await db.record_deletion_audit(
            user_id=user_id,
            email_at_request="done@test.local",
            requested_at="2025-01-01T00:00:00+00:00",
            scheduled_for="2025-01-15T00:00:00+00:00",
            source="android",
        )
        await db.update_deletion_audit_status(
            audit_id, "completed", completed_at="2025-01-15T01:00:00+00:00",
        )
        # No more pending row for this user.
        assert await db.find_pending_audit_for_user(user_id) is None

        cur = await db.conn.execute(
            "SELECT status, completed_at FROM deletion_audit WHERE id = ?",
            (audit_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "completed"
        assert row["completed_at"] == "2025-01-15T01:00:00+00:00"

    async def test_find_pending_returns_most_recent_when_multiple(
        self, db: Database
    ):
        """A user could in theory have multiple historical audit rows
        (one cancelled, one re-requested). ``find_pending_audit_for_user``
        narrows to ``status = 'pending'`` and orders by requested_at DESC."""
        user_id = await db.create_user(
            email="resched@test.local", password_hash="h",
        )
        old_id = await db.record_deletion_audit(
            user_id=user_id,
            email_at_request="resched@test.local",
            requested_at="2025-01-01T00:00:00+00:00",
            scheduled_for="2025-01-15T00:00:00+00:00",
            source="android",
        )
        await db.update_deletion_audit_status(old_id, "cancelled")
        new_id = await db.record_deletion_audit(
            user_id=user_id,
            email_at_request="resched@test.local",
            requested_at="2025-02-01T00:00:00+00:00",
            scheduled_for="2025-02-15T00:00:00+00:00",
            source="portal",
        )

        pending = await db.find_pending_audit_for_user(user_id)
        assert pending is not None
        assert pending["id"] == new_id
        assert pending["source"] == "portal"


class TestDeletionCancelTokens:
    """``create_deletion_cancel_token`` / ``consume_deletion_cancel_token``
    — single-use short-TTL tokens for the cancel link emailed to users."""

    async def test_consume_is_single_use(self, db: Database):
        user_id = await db.create_user(email="su@test.local", password_hash="h")
        raw = await db.create_deletion_cancel_token(user_id, ttl_seconds=3600)
        first = await db.consume_deletion_cancel_token(raw)
        assert first == user_id

        # Second consume returns None — token is consumed.
        again = await db.consume_deletion_cancel_token(raw)
        assert again is None

    async def test_consume_expired_token_returns_none(self, db: Database):
        """TTL=0 makes the token expire the instant it's stored — the
        ``expires_at > now`` predicate in consume keeps it from matching."""
        user_id = await db.create_user(email="exp@test.local", password_hash="h")
        raw = await db.create_deletion_cancel_token(user_id, ttl_seconds=0)
        assert await db.consume_deletion_cancel_token(raw) is None

    async def test_creating_new_token_invalidates_outstanding_one(
        self, db: Database
    ):
        """Mirrors ``create_password_reset_token``: only one live cancel
        token per user. A second create marks the first consumed (it
        can no longer be redeemed)."""
        user_id = await db.create_user(email="inv@test.local", password_hash="h")
        first = await db.create_deletion_cancel_token(user_id, ttl_seconds=3600)
        second = await db.create_deletion_cancel_token(user_id, ttl_seconds=3600)
        assert first != second

        # First token is now invalidated; second still works.
        assert await db.consume_deletion_cancel_token(first) is None
        assert await db.consume_deletion_cancel_token(second) == user_id


class TestDeleteUserCascadeExtensions:
    """``delete_user``'s cascade was extended to drop rows in
    ``password_reset_tokens`` and ``account_deletion_cancel_tokens``.
    Both tables FK to ``users(id)`` so leaving stragglers would block the
    final user DELETE."""

    async def test_delete_user_drops_outstanding_password_reset_tokens(
        self, db: Database
    ):
        user_id = await db.create_user(email="pr@test.local", password_hash="h")
        # An unconsumed reset token must be cleared, or the FK on
        # password_reset_tokens(user_id) would block the users DELETE.
        await db.create_password_reset_token(user_id, "hash-1", ttl_seconds=300)

        agent_ids = await db.delete_user(user_id)
        assert agent_ids == []

        cur = await db.conn.execute(
            "SELECT COUNT(*) AS n FROM password_reset_tokens WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        assert row["n"] == 0
        assert await db.get_user_by_id(user_id) is None

    async def test_delete_user_drops_outstanding_cancel_tokens(
        self, db: Database
    ):
        user_id = await db.create_user(email="ct2@test.local", password_hash="h")
        await db.create_deletion_cancel_token(user_id, ttl_seconds=3600)
        # Sanity: row exists before the cascade runs.
        cur = await db.conn.execute(
            "SELECT COUNT(*) AS n FROM account_deletion_cancel_tokens "
            "WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        assert row["n"] == 1

        await db.delete_user(user_id)

        cur = await db.conn.execute(
            "SELECT COUNT(*) AS n FROM account_deletion_cancel_tokens "
            "WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        assert row["n"] == 0

    async def test_delete_user_preserves_audit_rows(self, db: Database):
        """Compliance trail: ``deletion_audit`` has no FK on user_id and
        must outlive the user row (GDPR Art. 17)."""
        user_id = await db.create_user(email="aud@test.local", password_hash="h")
        audit_id = await db.record_deletion_audit(
            user_id=user_id,
            email_at_request="aud@test.local",
            requested_at="2025-01-01T00:00:00+00:00",
            scheduled_for="2025-01-15T00:00:00+00:00",
            source="android",
        )

        await db.delete_user(user_id)

        # User is gone; audit row survives.
        assert await db.get_user_by_id(user_id) is None
        cur = await db.conn.execute(
            "SELECT id, user_id, email_at_request FROM deletion_audit WHERE id = ?",
            (audit_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["user_id"] == user_id
        assert row["email_at_request"] == "aud@test.local"


class TestUserShortcuts:
    """DB-layer invariants of the ``user_shortcuts`` helpers that the REST
    surface never reaches directly: the partial dedupe unique index and the
    per-(owner, kind) position counter."""

    async def test_duplicate_chord_rolls_back_open_transaction(
        self, db: Database
    ):
        """The aborted INSERT must not leave the shared connection's
        implicit write transaction open; the next shortcut write still
        lands normally."""
        owner = await _create_test_user(db)
        await db.create_user_shortcut(owner, "claude", "ctrl+x", None)

        with pytest.raises(sqlite3.IntegrityError):
            await db.create_user_shortcut(owner, "claude", "ctrl+x", None)

        assert db.conn.in_transaction is False
        fresh = await db.create_user_shortcut(owner, "claude", "ctrl+y", None)
        assert fresh["position"] == 1

    async def test_insert_allowed_after_soft_delete(self, db: Database):
        owner = await _create_test_user(db)
        first = await db.create_user_shortcut(owner, "claude", "ctrl+x", None)
        assert (
            await db.soft_delete_user_shortcut(first["shortcut_id"])
            is not None
        )

        fresh = await db.create_user_shortcut(owner, "claude", "ctrl+x", None)
        assert fresh["shortcut_id"] != first["shortcut_id"]
        assert fresh["version"] == 1

    async def test_dedupe_scoped_to_owner_and_kind(self, db: Database):
        owner = await _create_test_user(db)
        other = await db.create_user(
            email="other@test.local", password_hash="h"
        )
        await db.create_user_shortcut(owner, "claude", "ctrl+x", None)
        await db.create_user_shortcut(owner, "codex", "ctrl+x", None)
        await db.create_user_shortcut(other, "claude", "ctrl+x", None)

        assert len(await db.list_user_shortcuts_for_user(owner)) == 2
        assert len(await db.list_user_shortcuts_for_user(other)) == 1

    async def test_position_appends_per_owner_and_kind(self, db: Database):
        owner = await _create_test_user(db)
        a = await db.create_user_shortcut(owner, "claude", "ctrl+a", None)
        b = await db.create_user_shortcut(owner, "claude", "ctrl+b", None)
        c = await db.create_user_shortcut(owner, "codex", "ctrl+a", None)

        assert (a["position"], b["position"], c["position"]) == (0, 1, 0)

    async def test_position_counts_live_rows_only(self, db: Database):
        owner = await _create_test_user(db)
        await db.create_user_shortcut(owner, "claude", "ctrl+a", None)
        b = await db.create_user_shortcut(owner, "claude", "ctrl+b", None)
        assert b["position"] == 1
        await db.soft_delete_user_shortcut(b["shortcut_id"])

        c = await db.create_user_shortcut(owner, "claude", "ctrl+c", None)
        # MAX(position)+1 over live rows: the freed slot is reused.
        assert c["position"] == 1

    async def test_update_name_racing_delete_at_readback_returns_none(
        self, db: Database, monkeypatch
    ):
        """A soft-delete committing between the name UPDATE and its
        readback must not surface the tombstone as the updated row —
        the helper reports None so callers treat the shortcut as gone."""
        owner = await _create_test_user(db)
        row = await db.create_user_shortcut(owner, "claude", "ctrl+x", None)

        real_get = db.get_user_shortcut

        async def racing_get(shortcut_id):
            monkeypatch.setattr(db, "get_user_shortcut", real_get)
            await db.soft_delete_user_shortcut(shortcut_id)
            return await real_get(shortcut_id)

        monkeypatch.setattr(db, "get_user_shortcut", racing_get)

        result = await db.update_user_shortcut_name(row["shortcut_id"], "New")

        assert result is None
        persisted = await db.get_user_shortcut(row["shortcut_id"])
        assert persisted is not None
        assert persisted["deleted_at"] is not None


class TestPromptTemplates:
    """DB-layer readback guard for prompt templates: a soft-delete landing
    between a mutation's commit and its unfiltered readback must surface as
    ``None`` (gone), never the tombstone row — otherwise the service
    rebroadcasts the deleted template as ``template.updated`` and resurrects
    it on every device."""

    async def test_update_template_racing_delete_at_readback_returns_none(
        self, db: Database, monkeypatch
    ):
        owner = await _create_test_user(db)
        row = await db.create_template(owner, "orig", "body")

        real_get = db.get_template

        async def racing_get(template_id):
            monkeypatch.setattr(db, "get_template", real_get)
            await db.soft_delete_template(template_id)
            return await real_get(template_id)

        monkeypatch.setattr(db, "get_template", racing_get)

        result = await db.update_template(row["template_id"], name="New")

        assert result is None
        persisted = await db.get_template(row["template_id"])
        assert persisted is not None
        assert persisted["deleted_at"] is not None

    async def test_update_template_no_fields_on_deleted_row_returns_none(
        self, db: Database
    ):
        """The all-None PATCH path reads the row back without mutating it; on
        an already-tombstoned row it must report ``None``, not the tombstone
        dict (pre-fix the unfiltered readback returned the deleted row)."""
        owner = await _create_test_user(db)
        row = await db.create_template(owner, "orig", "body")
        await db.soft_delete_template(row["template_id"])

        assert await db.update_template(row["template_id"]) is None


class TestCustomAgentTypes:
    """DB helpers for the account-scoped custom agent type registry:
    single-statement cap enforcement, NOCASE name uniqueness, guarded
    update, and the kind-scoped shortcut cascade on soft-delete."""

    async def test_create_cap_kwarg_returns_none_and_inserts_nothing(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        assert await db.create_custom_agent_type(owner, "One", "c1", cap=1)

        assert await db.create_custom_agent_type(
            owner, "Two", "c2", cap=1
        ) is None
        rows = await db.list_custom_agent_types_for_user(owner)
        assert [r["name"] for r in rows] == ["One"]

    async def test_cap_counts_only_live_rows(self, db: Database):
        owner = await _create_test_user(db)
        first = await db.create_custom_agent_type(owner, "One", "c1", cap=1)
        await db.soft_delete_custom_agent_type(first["id"], owner)

        fresh = await db.create_custom_agent_type(owner, "Two", "c2", cap=1)
        assert fresh is not None

    async def test_cap_is_per_owner(self, db: Database):
        owner = await _create_test_user(db)
        other = await db.create_user(
            email="other@test.local", password_hash="hash"
        )
        assert await db.create_custom_agent_type(owner, "One", "c1", cap=1)

        assert await db.create_custom_agent_type(
            other, "One", "c1", cap=1
        ) is not None

    async def test_create_name_conflict_rolls_back_open_transaction(
        self, db: Database
    ):
        """The aborted INSERT must not leave the shared connection's
        implicit write transaction open; the next unrelated write still
        lands normally."""
        owner = await _create_test_user(db)
        await db.create_custom_agent_type(owner, "Aider", "c1")

        with pytest.raises(sqlite3.IntegrityError):
            await db.create_custom_agent_type(owner, "aider", "c2")

        assert db.conn.in_transaction is False
        fresh = await db.create_custom_agent_type(owner, "Goose", "goose run")
        assert await db.get_custom_agent_type(fresh["id"]) == fresh

    async def test_update_name_conflict_rolls_back_open_transaction(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        await db.create_custom_agent_type(owner, "Aider", "c1")
        target = await db.create_custom_agent_type(owner, "Goose", "c2")

        with pytest.raises(sqlite3.IntegrityError):
            await db.update_custom_agent_type(
                target["id"], owner, name="AIDER"
            )

        assert db.conn.in_transaction is False
        renamed = await db.update_custom_agent_type(
            target["id"], owner, name="Gander"
        )
        assert renamed["name"] == "Gander"

    async def test_get_live_for_owner_scoping(self, db: Database):
        owner = await _create_test_user(db)
        other = await db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await db.create_custom_agent_type(owner, "Aider", "c1")

        assert await db.get_live_custom_agent_type_for_owner(
            row["id"], owner
        ) is not None
        assert await db.get_live_custom_agent_type_for_owner(
            row["id"], other
        ) is None

        await db.soft_delete_custom_agent_type(row["id"], owner)
        assert await db.get_live_custom_agent_type_for_owner(
            row["id"], owner
        ) is None

    async def test_update_coalesce_keeps_omitted_fields(self, db: Database):
        owner = await _create_test_user(db)
        row = await db.create_custom_agent_type(owner, "Aider", "c1")

        renamed = await db.update_custom_agent_type(
            row["id"], owner, name="Renamed"
        )
        assert renamed["name"] == "Renamed"
        assert renamed["command"] == "c1"

        recommanded = await db.update_custom_agent_type(
            row["id"], owner, command="c2"
        )
        assert recommanded["name"] == "Renamed"
        assert recommanded["command"] == "c2"

    async def test_update_guard_returns_none(self, db: Database):
        owner = await _create_test_user(db)
        other = await db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await db.create_custom_agent_type(owner, "Aider", "c1")

        assert await db.update_custom_agent_type(
            "nonexistent12", owner, name="X"
        ) is None
        assert await db.update_custom_agent_type(
            row["id"], other, name="X"
        ) is None

        await db.soft_delete_custom_agent_type(row["id"], owner)
        assert await db.update_custom_agent_type(
            row["id"], owner, name="X"
        ) is None

    async def test_soft_delete_cascades_only_matching_kind_shortcuts(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        other = await db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await db.create_custom_agent_type(owner, "Aider", "c1")
        kind = f"custom_{row['id']}"
        scoped = await db.create_user_shortcut(owner, kind, "ctrl+x", None)
        other_kind = await db.create_user_shortcut(
            owner, "claude", "ctrl+x", None
        )
        foreign = await db.create_user_shortcut(other, kind, "ctrl+x", None)

        tombstone = await db.soft_delete_custom_agent_type(row["id"], owner)
        assert tombstone is not None
        assert tombstone["deleted_at"] is not None

        scoped_after = await db.get_user_shortcut(scoped["shortcut_id"])
        assert scoped_after["deleted_at"] is not None
        assert scoped_after["version"] == scoped["version"] + 1

        other_after = await db.get_user_shortcut(other_kind["shortcut_id"])
        assert other_after["deleted_at"] is None
        assert other_after["version"] == other_kind["version"]

        foreign_after = await db.get_user_shortcut(foreign["shortcut_id"])
        assert foreign_after["deleted_at"] is None

    async def test_soft_delete_guard_returns_none(self, db: Database):
        owner = await _create_test_user(db)
        other = await db.create_user(
            email="other@test.local", password_hash="hash"
        )
        row = await db.create_custom_agent_type(owner, "Aider", "c1")

        assert await db.soft_delete_custom_agent_type(row["id"], other) is None
        assert await db.soft_delete_custom_agent_type(row["id"], owner)
        assert await db.soft_delete_custom_agent_type(row["id"], owner) is None

    async def test_delete_user_purges_custom_agent_types(self, db: Database):
        owner = await _create_test_user(db)
        survivor = await db.create_user(
            email="other@test.local", password_hash="hash"
        )
        await db.create_custom_agent_type(owner, "Mine", "c1")
        kept = await db.create_custom_agent_type(survivor, "Theirs", "c2")

        await db.delete_user(owner)

        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM custom_agent_types WHERE owner_user_id = ?",
            (owner,),
        )
        assert (await cursor.fetchone())[0] == 0
        assert await db.get_custom_agent_type(kept["id"]) is not None


class TestCustomAgentTypesMigration:
    async def test_v17_creates_table_on_existing_db(self, tmp_path):
        db_path = str(tmp_path / "pre_v17.db")
        db = Database(db_path)
        await db.connect()
        owner = await _create_test_user(db)
        # Simulate a pre-v17 on-disk schema: drop the new table + indexes
        # and roll user_version back so reopening replays the v17 block.
        await db.conn.execute("DROP INDEX idx_custom_agent_types_owner")
        await db.conn.execute("DROP INDEX idx_custom_agent_types_owner_name")
        await db.conn.execute("DROP TABLE custom_agent_types")
        await db.conn.execute("PRAGMA user_version = 16")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == await _schema_head(tmp_path)

        # Table is usable and the NOCASE unique index came back with it.
        row = await db.create_custom_agent_type(owner, "Aider", "c1")
        assert row is not None
        with pytest.raises(sqlite3.IntegrityError):
            await db.create_custom_agent_type(owner, "AIDER", "c2")
        await db.close()

        # Idempotent: a second reopen at head is a no-op and the row
        # survives.
        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == await _schema_head(tmp_path)
        assert await db.get_custom_agent_type(row["id"]) is not None
        await db.close()


class TestAgentSupportsUninstall:
    """``supports_uninstall`` describes the build that last connected, so
    unlike the COALESCE'd version fields it is overwritten on every
    handshake."""

    async def test_handshake_persists_the_capability(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)

        await db.update_agent_handshake_info(
            agent_id, "0.9.0", 8, supports_uninstall=True
        )

        assert (await db.get_agent(agent_id))["supports_uninstall"] == 1
        listed = await db.list_agents_for_user(owner)
        assert listed[0]["supports_uninstall"] == 1

    async def test_downgraded_handshake_clears_the_capability(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.update_agent_handshake_info(
            agent_id, "0.9.0", 8, supports_uninstall=True
        )

        await db.update_agent_handshake_info(agent_id, None, None)

        assert (await db.get_agent(agent_id))["supports_uninstall"] == 0


class TestAgentUninstallRequests:
    async def test_recorded_request_matches_the_removed_agents_token(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("test", owner)

        assert await db.record_uninstall_request(agent_id) is True
        await db.delete_agent(agent_id)

        assert await db.match_uninstall_request(agent_id, raw_token) is True

    async def test_wrong_token_does_not_match(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, _ = await db.register_agent("test", owner)
        await db.record_uninstall_request(agent_id)
        await db.delete_agent(agent_id)

        assert await db.match_uninstall_request(agent_id, "not-the-token") is False

    async def test_token_does_not_match_another_agent_id(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("test", owner)
        other_id, _ = await db.register_agent("other", owner)
        await db.record_uninstall_request(agent_id)

        assert await db.match_uninstall_request(other_id, raw_token) is False

    async def test_no_request_without_a_record(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("test", owner)
        await db.delete_agent(agent_id)

        assert await db.match_uninstall_request(agent_id, raw_token) is False

    async def test_request_keys_on_the_token_current_at_removal(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        agent_id, old_token = await db.register_agent("test", owner)
        new_token = await db.reregister_agent(agent_id)
        await db.record_uninstall_request(agent_id)
        await db.delete_agent(agent_id)

        assert await db.match_uninstall_request(agent_id, new_token) is True
        assert await db.match_uninstall_request(agent_id, old_token) is False

    async def test_expired_request_does_not_match(self, db: Database):
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("test", owner)
        await db.record_uninstall_request(agent_id, ttl=timedelta(seconds=-1))
        await db.delete_agent(agent_id)

        assert await db.match_uninstall_request(agent_id, raw_token) is False

    async def test_cleanup_expired_removes_only_expired_requests(
        self, db: Database
    ):
        owner = await _create_test_user(db)
        stale_id, _ = await db.register_agent("stale", owner)
        live_id, live_token = await db.register_agent("live", owner)
        await db.record_uninstall_request(stale_id, ttl=timedelta(seconds=-1))
        await db.record_uninstall_request(live_id)

        deleted = await db.cleanup_expired_tokens()

        assert deleted == 1
        cursor = await db.conn.execute(
            "SELECT agent_id FROM agent_uninstall_requests"
        )
        assert [r[0] for r in await cursor.fetchall()] == [live_id]
        assert await db.match_uninstall_request(live_id, live_token) is True


class TestAgentUninstallMigration:
    async def test_v21_adds_column_and_table_to_existing_db(self, tmp_path):
        db_path = str(tmp_path / "pre_v21.db")
        db = Database(db_path)
        await db.connect()
        owner = await _create_test_user(db)
        agent_id, raw_token = await db.register_agent("legacy", owner)
        # Simulate a v20 on-disk schema: drop the new column + table and
        # roll user_version back so reopening replays the v21 block.
        await db.conn.execute("DROP TABLE agent_uninstall_requests")
        await db.conn.execute("ALTER TABLE agents DROP COLUMN supports_uninstall")
        await db.conn.execute("PRAGMA user_version = 20")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == await _schema_head(tmp_path)
        # Pre-existing agents read as not supporting uninstall until their
        # next handshake says otherwise.
        assert (await db.get_agent(agent_id))["supports_uninstall"] == 0
        assert await db.record_uninstall_request(agent_id) is True
        await db.delete_agent(agent_id)
        assert await db.match_uninstall_request(agent_id, raw_token) is True
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == await _schema_head(tmp_path)
        assert await db.match_uninstall_request(agent_id, raw_token) is True
        await db.close()


class TestDeviceTokenRevocationMigration:
    async def test_v22_creates_and_backfills_the_revocation_table(self, tmp_path):
        db_path = str(tmp_path / "pre_v22.db")
        db = Database(db_path)
        await db.connect()
        owner = await _create_test_user(db)
        await db.upsert_mobile_device(device_id="dev-gone", user_id=owner, name="A")
        await db.upsert_mobile_device(device_id="dev-live", user_id=owner, name="B")
        await db.revoke_mobile_device("dev-gone", owner)
        await db.store_user_refresh_token("r-legacy", owner, 60)
        cursor = await db.conn.execute(
            "SELECT revoked_at FROM mobile_devices WHERE device_id = 'dev-gone'"
        )
        revoked_at = (await cursor.fetchone())[0]
        cursor = await db.conn.execute("SELECT created_at FROM user_refresh_tokens")
        legacy_created_at = (await cursor.fetchone())[0]
        # Simulate a v21 on-disk schema: drop the new table and columns and
        # roll user_version back so reopening replays the v22 block. The
        # device row keeps its ``revoked_at``.
        await db.conn.execute("DROP TABLE device_revocations")
        await db.conn.execute("ALTER TABLE user_refresh_tokens DROP COLUMN device_id")
        await db.conn.execute("ALTER TABLE user_refresh_tokens DROP COLUMN auth_time")
        await db.conn.execute("PRAGMA user_version = 21")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == await _schema_head(tmp_path)
        # A device revoked before the upgrade loses its old tokens with it.
        assert await db.device_tokens_valid_after("dev-gone") == int(
            datetime.fromisoformat(revoked_at).timestamp()
        )
        assert await db.device_tokens_valid_after("dev-live") is None
        cursor = await db.conn.execute("PRAGMA table_info(mobile_devices)")
        assert "tokens_valid_after" not in {row[1] for row in await cursor.fetchall()}
        # Pre-existing user sessions survive, unbound, signed in no later than
        # their last rotation — so before any revoke made after the upgrade.
        assert await db.validate_and_consume_user_refresh("r-legacy") == (
            owner,
            None,
            int(datetime.fromisoformat(legacy_created_at).timestamp()),
        )
        await db.close()

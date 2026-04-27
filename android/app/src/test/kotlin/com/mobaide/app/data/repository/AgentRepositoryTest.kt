package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.CreateAgentResponse
import com.mobaide.app.data.network.dto.MobileTokenResponse
import com.mobaide.app.data.network.dto.ReRegisterAgentResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [AgentRepository] -- exercises the [CentralApi] delegation,
 * user-token requirement via [UserIdentityStore], in-memory caching of the
 * agent list exposed through [observeAgents], and the auto-refresh side
 * effects on create and delete.
 *
 * Collaborators are mocked with MockK. The repository does not use coroutine
 * dispatchers internally beyond what the caller provides, so plain
 * `runTest { }` is sufficient.
 */
class AgentRepositoryTest {

    // ---- Helpers ---------------------------------------------------------------

    private fun agent(
        id: String,
        name: String = "agent-$id",
        online: Boolean = true,
    ): AgentSummaryResponse = AgentSummaryResponse(
        agent_id = id,
        name = name,
        online = online,
        created_at = "2026-04-16T12:00:00Z",
        last_seen_at = null,
    )

    private data class Fixture(
        val repo: AgentRepository,
        val centralApi: CentralApi,
        val userIdentityStore: UserIdentityStore,
    )

    private fun newFixture(userToken: String? = "tok-user-123"): Fixture {
        val centralApi = mockk<CentralApi>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.getUserAccessToken() } returns userToken
        return Fixture(
            repo = AgentRepository(centralApi, userIdentityStore),
            centralApi = centralApi,
            userIdentityStore = userIdentityStore,
        )
    }

    // ---- observeAgents initial state -------------------------------------------

    @Test
    fun observeAgents_initialState_isEmptyList() = runTest {
        val (repo) = newFixture()
        assertTrue(repo.observeAgents().value.isEmpty())
    }

    @Test
    fun observeAgents_initialFlowEmission_isEmptyList() = runTest {
        val (repo) = newFixture()
        repo.observeAgents().test {
            assertEquals(emptyList<AgentSummaryResponse>(), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- requireUserToken guard ------------------------------------------------

    @Test
    fun refreshAgents_throwsIllegalStateException_whenUserNotLoggedIn() = runTest {
        val (repo) = newFixture(userToken = null)
        try {
            repo.refreshAgents()
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            assertEquals("User not logged in", e.message)
        }
    }

    @Test
    fun createAgent_throwsIllegalStateException_whenUserNotLoggedIn() = runTest {
        val (repo) = newFixture(userToken = null)
        try {
            repo.createAgent("test-agent")
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            assertEquals("User not logged in", e.message)
        }
    }

    // ---- refreshAgents ---------------------------------------------------------

    @Test
    fun refreshAgents_delegatesToCentralApi_andUpdatesFlow() = runTest {
        val (repo, api) = newFixture()
        val agents = listOf(agent("a-1"), agent("a-2"))
        coEvery { api.listAgents("tok-user-123") } returns agents

        repo.observeAgents().test {
            assertEquals(emptyList<AgentSummaryResponse>(), awaitItem())

            repo.refreshAgents()

            assertEquals(agents, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { api.listAgents("tok-user-123") }
    }

    @Test
    fun refreshAgents_secondCall_replacesEntireList() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listAgents(any()) } returns listOf(agent("a-1"), agent("a-2"))

        repo.refreshAgents()
        assertEquals(2, repo.observeAgents().value.size)

        coEvery { api.listAgents(any()) } returns listOf(agent("a-3"))
        repo.refreshAgents()
        assertEquals(listOf("a-3"), repo.observeAgents().value.map { it.agent_id })
    }

    // ---- createAgent -----------------------------------------------------------

    @Test
    fun createAgent_delegatesToCentralApi_andReturnsSamePayload() = runTest {
        val (repo, api) = newFixture()
        val response = CreateAgentResponse(agent_id = "a-1", name = "my-agent", token = "tok-abc")
        coEvery { api.createAgent("tok-user-123", "my-agent") } returns response
        coEvery { api.listAgents("tok-user-123") } returns listOf(agent("a-1", name = "my-agent"))

        val returned = repo.createAgent("my-agent")

        assertSame(response, returned)
        coVerify(exactly = 1) { api.createAgent("tok-user-123", "my-agent") }
    }

    @Test
    fun createAgent_triggersRefreshAfterCreation() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.createAgent(any(), any()) } returns CreateAgentResponse(
            agent_id = "a-1", name = "test", token = "tok",
        )
        coEvery { api.listAgents(any()) } returns listOf(agent("a-1"))

        repo.createAgent("test")

        // refreshAgents is called after createAgent, so listAgents must have been called
        coVerify(atLeast = 1) { api.listAgents("tok-user-123") }
        assertEquals(1, repo.observeAgents().value.size)
    }

    // ---- reregisterAgent -------------------------------------------------------

    @Test
    fun reregisterAgent_delegatesToCentralApi_andReturnsSamePayload() = runTest {
        val (repo, api) = newFixture()
        val response = ReRegisterAgentResponse(agent_id = "a-1", token = "new-tok")
        coEvery { api.reregisterAgent("tok-user-123", "a-1") } returns response

        val returned = repo.reregisterAgent("a-1")

        assertSame(response, returned)
        coVerify(exactly = 1) { api.reregisterAgent("tok-user-123", "a-1") }
    }

    // ---- deleteAgent -----------------------------------------------------------

    @Test
    fun deleteAgent_delegatesToCentralApi_andRefreshesAfterwards() = runTest {
        val (repo, api) = newFixture()
        // Seed agents
        coEvery { api.listAgents(any()) } returns listOf(agent("a-1"), agent("a-2"))
        repo.refreshAgents()
        assertEquals(2, repo.observeAgents().value.size)

        // After delete, refresh returns only a-2
        coEvery { api.deleteAgent(any(), any()) } returns Unit
        coEvery { api.listAgents(any()) } returns listOf(agent("a-2"))

        repo.deleteAgent("a-1")

        coVerify(exactly = 1) { api.deleteAgent("tok-user-123", "a-1") }
        assertEquals(listOf("a-2"), repo.observeAgents().value.map { it.agent_id })
    }

    // ---- updateAgentOnline (user-scoped push from UserEventBus) ----------------

    @Test
    fun updateAgentOnline_togglesOnlineFlag_onMatchingAgent() = runTest {
        // Happy path: a server-pushed status_changed event flips an agent
        // from online=true to online=false. Only the matching entry is
        // mutated; others are unchanged.
        val (repo, api) = newFixture()
        coEvery { api.listAgents(any()) } returns listOf(
            agent("a-1", online = true),
            agent("a-2", online = true),
        )
        repo.refreshAgents()

        repo.updateAgentOnline("a-1", online = false)

        val list = repo.observeAgents().value
        assertEquals(2, list.size)
        assertFalse(list.first { it.agent_id == "a-1" }.online)
        assertTrue(list.first { it.agent_id == "a-2" }.online)
    }

    @Test
    fun updateAgentOnline_flipsFromOfflineToOnline() = runTest {
        // Symmetric of the previous test: offline → online transition
        // (the reconnect path that reveals an agent came back).
        val (repo, api) = newFixture()
        coEvery { api.listAgents(any()) } returns listOf(agent("a-1", online = false))
        repo.refreshAgents()

        repo.updateAgentOnline("a-1", online = true)

        assertTrue(repo.observeAgents().value.single().online)
    }

    @Test
    fun updateAgentOnline_unknownAgentId_isNoOp() = runTest {
        // An event for an agent we haven't fetched yet must not alter the
        // list (and must not throw). The next `refreshAgents()` call will
        // discover that agent naturally.
        val (repo, api) = newFixture()
        val original = listOf(agent("a-1", online = true))
        coEvery { api.listAgents(any()) } returns original
        repo.refreshAgents()
        val before = repo.observeAgents().value

        repo.updateAgentOnline("ghost-never-seen", online = true)

        // List reference and contents are both unchanged.
        assertSame(before, repo.observeAgents().value)
    }

    @Test
    fun updateAgentOnline_idempotentOnMatchingState_doesNotEmitNewValue() = runTest {
        // Critical: when the incoming event matches the cached state, the
        // StateFlow must NOT emit a new value — otherwise every collector
        // (AgentListViewModel, lazy list, etc.) would recompose/re-render
        // on every heartbeat-like duplicate.
        val (repo, api) = newFixture()
        coEvery { api.listAgents(any()) } returns listOf(agent("a-1", online = true))
        repo.refreshAgents()

        repo.observeAgents().test {
            val first = awaitItem()                // replayed current list
            assertEquals(1, first.size)
            assertTrue(first[0].online)

            // Re-apply the same state. No new emission expected.
            repo.updateAgentOnline("a-1", online = true)
            expectNoEvents()

            // Apply a different state — now a new emission must arrive.
            repo.updateAgentOnline("a-1", online = false)
            val second = awaitItem()
            assertFalse(second[0].online)

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun updateAgentOnline_onEmptyCache_isNoOp() = runTest {
        // Regression guard: before any refreshAgents() call the cache is
        // empty. A status event arriving at that moment must not throw
        // and must not leave the StateFlow in a non-empty state.
        val (repo) = newFixture()

        repo.updateAgentOnline("any-id", online = true)

        assertEquals(emptyList<AgentSummaryResponse>(), repo.observeAgents().value)
    }

    // ---- getMobileTokensForAgent -----------------------------------------------

    @Test
    fun getMobileTokensForAgent_delegatesToCentralApi_andReturnsSamePayload() = runTest {
        val (repo, api) = newFixture()
        val response = MobileTokenResponse(
            access_token = "at-1", refresh_token = "rt-1", token_type = "bearer",
        )
        coEvery { api.getMobileTokens("tok-user-123", "a-1") } returns response

        val returned = repo.getMobileTokensForAgent("a-1")

        assertSame(response, returned)
        coVerify(exactly = 1) { api.getMobileTokens("tok-user-123", "a-1") }
    }
}

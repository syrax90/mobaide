package com.mobaide.app.ui.sessions.panel

import app.cash.turbine.test
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentSwitchCoordinator
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Dependencies exposing flows are stubbed with real [MutableStateFlow]
 * instances; [MainDispatcherRule] makes the `init` refresh deterministic under
 * `runTest { advanceUntilIdle() }`.
 * `uiState` is a `stateIn(WhileSubscribed)` of the combine, so each test
 * subscribes first and reads the latest value after the scheduler drains.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SessionsPanelViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun session(id: String, state: String = "running"): SessionResponse =
        SessionResponse(session_id = id, state = state, created_at = "2026-04-11T00:00:00Z")

    private fun agent(id: String, online: Boolean = true): AgentSummaryResponse =
        AgentSummaryResponse(agent_id = id, name = "agent-$id", online = online, created_at = "t")

    private class Fixture(
        val sessionRepo: SessionRepository,
        val agentRepository: AgentRepository,
        val sessionsFlow: MutableStateFlow<List<SessionResponse>>,
        val syncedFlow: MutableStateFlow<Boolean>,
        val connectionFlow: MutableStateFlow<AgentEventBus.ConnState>,
        val typesFlow: MutableStateFlow<List<CustomAgentTypeDto>>,
        val agentsFlow: MutableStateFlow<List<AgentSummaryResponse>>,
        val switchingFlow: MutableStateFlow<String?>,
        val vm: SessionsPanelViewModel,
    )

    private fun build(
        connectedAgentId: String? = "a1",
        initialSynced: Boolean = true,
        initialConnection: AgentEventBus.ConnState = AgentEventBus.ConnState.Connected,
        refreshFails: Boolean = false,
        initialSessions: List<SessionResponse> = emptyList(),
        initialAgents: List<AgentSummaryResponse> = emptyList(),
    ): Fixture {
        val sessionRepo = mockk<SessionRepository>()
        val typesRepo = mockk<CustomAgentTypesRepository>()
        val agentRepository = mockk<AgentRepository>()
        val tokenStore = mockk<TokenStore>()
        val bus = mockk<AgentEventBus>()
        val coordinator = mockk<AgentSwitchCoordinator>()

        val sessionsFlow = MutableStateFlow(initialSessions)
        val syncedFlow = MutableStateFlow(initialSynced)
        val connectionFlow = MutableStateFlow(initialConnection)
        val typesFlow = MutableStateFlow<List<CustomAgentTypeDto>>(emptyList())
        val agentsFlow = MutableStateFlow(initialAgents)
        val switchingFlow = MutableStateFlow<String?>(null)

        every { sessionRepo.observeSessionList() } returns sessionsFlow
        every { sessionRepo.observeSessionsSynced() } returns syncedFlow
        every { bus.connection } returns connectionFlow
        every { typesRepo.observeTypes() } returns typesFlow
        every { agentRepository.observeAgents() } returns agentsFlow
        every { tokenStore.getAgentId() } returns connectedAgentId
        every { coordinator.switchingAgentId } returns switchingFlow
        if (refreshFails) {
            coEvery { agentRepository.refreshAgents() } throws RuntimeException("offline")
        } else {
            coEvery { agentRepository.refreshAgents() } just Runs
        }

        val vm = SessionsPanelViewModel(sessionRepo, typesRepo, agentRepository, tokenStore, bus, coordinator)
        return Fixture(
            sessionRepo, agentRepository, sessionsFlow, syncedFlow, connectionFlow, typesFlow,
            agentsFlow, switchingFlow, vm,
        )
    }

    @Test
    fun init_refreshesAgents_andAFailureLeavesTheCachedListRendering() = runTest {
        val fx = build(refreshFails = true)
        fx.agentsFlow.value = listOf(agent("a1"), agent("a2"))
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.agentRepository.refreshAgents() }
        fx.vm.uiState.test {
            advanceUntilIdle()
            assertEquals(listOf("a1", "a2"), expectMostRecentItem().agents.map { it.agent_id })
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun uiState_isSeededFromTheCachesBeforeAnyCollection() {
        val fx = build(initialSessions = listOf(session("s1")), initialAgents = listOf(agent("a1")))

        // A terminal that opens with the drawer up (a stop hand-off) renders
        // this value on its first frame: no "Reconnecting…", no rows fading in.
        val state = fx.vm.uiState.value
        assertTrue(state.synced)
        assertEquals(listOf("s1"), state.sessions.map { it.session_id })
        assertEquals("a1", state.connectedAgent?.agent_id)
    }

    @Test
    fun synced_requiresBothTheSnapshotAndALiveBus() = runTest {
        val fx = build(initialSynced = true, initialConnection = AgentEventBus.ConnState.Connected)
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            assertTrue(expectMostRecentItem().synced)

            // A dropped tunnel keeps the last snapshot in the cache; that list
            // is not agent-authoritative while the bus is down.
            fx.connectionFlow.value = AgentEventBus.ConnState.Connecting
            advanceUntilIdle()
            assertFalse(expectMostRecentItem().synced)

            fx.connectionFlow.value = AgentEventBus.ConnState.Connected
            advanceUntilIdle()
            assertTrue(expectMostRecentItem().synced)

            fx.syncedFlow.value = false
            advanceUntilIdle()
            assertFalse("a live bus with no snapshot yet is still reconnecting", expectMostRecentItem().synced)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionsAndCustomTypeNames_flowIntoTheState() = runTest {
        val fx = build()
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            assertEquals(emptyList<SessionResponse>(), expectMostRecentItem().sessions)

            fx.sessionsFlow.value = listOf(session("s1"), session("s2"))
            advanceUntilIdle()
            assertEquals(listOf("s1", "s2"), expectMostRecentItem().sessions.map { it.session_id })

            fx.typesFlow.value = listOf(
                CustomAgentTypeDto(id = "ab12cd34ef56", name = "Aider", command = "aider", created_at = "c", updated_at = "u"),
            )
            advanceUntilIdle()
            assertEquals(
                "the map keys on the full wire kind, never the bare id",
                mapOf("custom_ab12cd34ef56" to "Aider"),
                expectMostRecentItem().customTypeNames,
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun connectedAgent_isSplitFromTheOthers_bySavedAgentId() = runTest {
        val fx = build(connectedAgentId = "a2")
        fx.agentsFlow.value = listOf(agent("a1"), agent("a2"), agent("a3", online = false))
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            val state = expectMostRecentItem()
            assertEquals("a2", state.connectedAgent?.agent_id)
            assertEquals(listOf("a1", "a3"), state.otherAgents.map { it.agent_id })
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun noSavedAgent_leavesTheConnectedLineEmpty_andListsEveryAgentAsOther() = runTest {
        val fx = build(connectedAgentId = null)
        fx.agentsFlow.value = listOf(agent("a1"))
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            val state = expectMostRecentItem()
            assertNull(state.connectedAgent)
            assertEquals(listOf("a1"), state.otherAgents.map { it.agent_id })
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun switchingAgentId_mirrorsTheCoordinator() = runTest {
        val fx = build()
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            assertNull(expectMostRecentItem().switchingAgentId)

            fx.switchingFlow.value = "a2"
            advanceUntilIdle()
            assertEquals("a2", expectMostRecentItem().switchingAgentId)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun stopSession_success_emitsStopped_andForwardsTheForceFlag() = runTest {
        val fx = build()
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } just Runs
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s2", force = true)
            advanceUntilIdle()
            assertEquals(SessionsPanelEvent.Stopped("s2", nextSessionId = null), awaitItem())
            expectNoEvents()
        }
        coVerify(exactly = 1) { fx.sessionRepo.deleteSession("s2", true) }
    }

    @Test
    fun stopSession_failure_emitsStopFailedWithTheListsText_andClearsTheStoppingFlag() = runTest {
        val fx = build()
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } throws RuntimeException("agent offline")
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            fx.vm.events.test {
                fx.vm.stopSession("s1", force = false)
                advanceUntilIdle()
                assertEquals(
                    SessionsPanelEvent.StopFailed("Failed to stop session: agent offline"),
                    awaitItem(),
                )
            }
            assertNull("a failed stop must re-enable the row", expectMostRecentItem().stoppingSessionId)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun stopSession_alreadyGone404_emitsStopped() = runTest {
        val fx = build()
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } throws ApiException(404, "not found")
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s1", force = false)
            advanceUntilIdle()
            assertEquals(SessionsPanelEvent.Stopped("s1", nextSessionId = null), awaitItem())
            expectNoEvents()
        }
    }

    @Test
    fun stopSession_otherHttpError_emitsStopFailed() = runTest {
        val fx = build()
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } throws ApiException(503, "agent offline")
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s1", force = false)
            advanceUntilIdle()
            assertEquals(
                SessionsPanelEvent.StopFailed("Failed to stop session: HTTP 503: agent offline"),
                awaitItem(),
            )
            expectNoEvents()
        }
    }

    @Test
    fun stopSession_marksTheRowWhileInFlight_andIgnoresASecondStopUntilItSettles() = runTest {
        val fx = build()
        val gate = CompletableDeferred<Unit>()
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } coAnswers { gate.await() }
        advanceUntilIdle()

        fx.vm.uiState.test {
            advanceUntilIdle()
            fx.vm.stopSession("s1", force = false)
            advanceUntilIdle()
            assertEquals("s1", expectMostRecentItem().stoppingSessionId)

            fx.vm.stopSession("s2", force = true)
            advanceUntilIdle()
            expectNoEvents()
            coVerify(exactly = 0) { fx.sessionRepo.deleteSession("s2", any()) }

            gate.complete(Unit)
            advanceUntilIdle()
            assertNull(expectMostRecentItem().stoppingSessionId)
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { fx.sessionRepo.deleteSession(any(), any()) }
        fx.vm.events.test {
            assertEquals(SessionsPanelEvent.Stopped("s1", nextSessionId = null), awaitItem())
            expectNoEvents()
        }
    }

    @Test
    fun stopSession_success_namesTheNearestOpenSessionBelow() = runTest {
        val fx = build()
        fx.sessionsFlow.value = listOf(session("s1"), session("s2"), session("s3"))
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } just Runs
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s2", force = false)
            advanceUntilIdle()
            assertEquals(SessionsPanelEvent.Stopped("s2", nextSessionId = "s3"), awaitItem())
            expectNoEvents()
        }
    }

    @Test
    fun stopSession_alreadyGone404_stillNamesTheNearestOpenSession() = runTest {
        val fx = build()
        fx.sessionsFlow.value = listOf(session("s1"), session("s2"))
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } throws ApiException(404, "not found")
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s2", force = false)
            advanceUntilIdle()
            assertEquals(SessionsPanelEvent.Stopped("s2", nextSessionId = "s1"), awaitItem())
            expectNoEvents()
        }
    }

    @Test
    fun stopSession_usesPositionsFromBeforeTheRequest_andTheListAfterIt() = runTest {
        val fx = build()
        fx.sessionsFlow.value = listOf(session("s1"), session("s2"), session("s3"), session("s4"))
        val gate = CompletableDeferred<Unit>()
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } coAnswers { gate.await() }
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s2", force = false)
            advanceUntilIdle()
            // The agent's push lands before the stop resolves: the stopped row
            // is gone and its old neighbour below has ended.
            fx.sessionsFlow.value = listOf(session("s1"), session("s3", state = "stopped"), session("s4"))
            gate.complete(Unit)
            advanceUntilIdle()
            assertEquals(SessionsPanelEvent.Stopped("s2", nextSessionId = "s1"), awaitItem())
            expectNoEvents()
        }
    }

    @Test
    fun stopSession_withNoOtherOpenSession_namesNone() = runTest {
        val fx = build()
        fx.sessionsFlow.value = listOf(session("s1", state = "stopped"), session("s2"))
        coEvery { fx.sessionRepo.deleteSession(any(), any()) } just Runs
        advanceUntilIdle()

        fx.vm.events.test {
            fx.vm.stopSession("s2", force = false)
            advanceUntilIdle()
            assertEquals(SessionsPanelEvent.Stopped("s2", nextSessionId = null), awaitItem())
            expectNoEvents()
        }
    }
}

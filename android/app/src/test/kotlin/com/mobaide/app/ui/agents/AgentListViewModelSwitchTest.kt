package com.mobaide.app.ui.agents

import app.cash.turbine.test
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.MobileTokenResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentSwitchCoordinator
import com.mobaide.app.data.security.TrustKeyPinning
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * The coordinator is real over the same relaxed mocks as the ViewModel;
 * [MainDispatcherRule] points its `Dispatchers.Main.immediate` scope at the
 * test scheduler, so both halves are deterministic under `runTest`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AgentListViewModelSwitchTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun agent(id: String, name: String = "agent-$id"): AgentSummaryResponse =
        AgentSummaryResponse(
            agent_id = id,
            name = name,
            online = true,
            created_at = "2026-04-16T12:00:00Z",
            last_seen_at = null,
        )

    private class Fixture(
        val agentRepository: AgentRepository,
        val tokenStore: TokenStore,
        val coordinator: AgentSwitchCoordinator,
        private val sessionManager: SessionManager,
        private val agentEventBus: AgentEventBus,
        private val deviceIdentityStore: DeviceIdentityStore,
    ) {
        fun buildVm(): AgentListViewModel = AgentListViewModel(
            agentRepository, tokenStore,
            sessionManager, agentEventBus, deviceIdentityStore,
            mockk<TrustKeyPinning>(relaxed = true),
            coordinator,
        )
    }

    private fun newFixture(connectedAgentId: String? = null): Fixture {
        val agentRepository = mockk<AgentRepository>(relaxed = true)
        val connectionStore = mockk<ConnectionStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val agentEventBus = mockk<AgentEventBus>(relaxed = true)
        val deviceIdentityStore = mockk<DeviceIdentityStore>(relaxed = true)

        every { agentRepository.observeAgents() } returns MutableStateFlow(emptyList())
        coEvery { agentRepository.refreshAgents() } just Runs
        every { tokenStore.getAgentId() } returns connectedAgentId
        every { sessionManager.isLoggedIn() } returns (connectedAgentId != null)
        every { sessionManager.tryMarkAgentConnected(any()) } answers { firstArg<() -> Unit>().invoke(); true }
        every { sessionManager.isUserAuthenticated() } returns true
        coEvery { deviceIdentityStore.getOrCreateDeviceId() } returns TEST_DEVICE_ID
        every { deviceIdentityStore.getDeviceName() } returns TEST_DEVICE_NAME
        every { connectionStore.listProfiles() } returns emptyList()

        return Fixture(
            agentRepository, tokenStore,
            AgentSwitchCoordinator(
                agentRepository, connectionStore, tokenStore,
                sessionManager, agentEventBus, deviceIdentityStore,
            ),
            sessionManager, agentEventBus, deviceIdentityStore,
        )
    }

    companion object {
        private const val TEST_DEVICE_ID = "dev-uuid-switch"
        private const val TEST_DEVICE_NAME = "Test Device"
        private val TOKENS = MobileTokenResponse("access-tok", "refresh-tok", "bearer")
    }

    @Test
    fun init_surfacesASwitchThatFailedBeforeTheScreenWasBuilt() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", TEST_DEVICE_ID, TEST_DEVICE_NAME) } throws
            RuntimeException("token minting failed")
        fx.coordinator.switchAndOpenSessions(agent("a-1"))
        advanceUntilIdle()

        val vm = fx.buildVm()
        advanceUntilIdle()

        assertEquals(
            "refresh() clears error inline; a message already waiting in the channel must outlive it",
            "token minting failed",
            vm.uiState.value.error,
        )
        assertFalse(vm.uiState.value.isConnecting)
        assertFalse(fx.coordinator.pendingSessionsList.value)
    }

    @Test
    fun connectToAgent_differentAgent_handsTheSwitchToTheCoordinator() = runTest {
        val fx = newFixture(connectedAgentId = "a-1")
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-2", TEST_DEVICE_ID, TEST_DEVICE_NAME) } coAnswers {
            delay(10_000)
            TOKENS
        }
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.navigateToSessions.test {
            vm.connectToAgent(agent("a-2"))
            runCurrent()

            assertTrue(
                "the success navigation belongs to AppNavigation, raised through the coordinator",
                fx.coordinator.pendingSessionsList.value,
            )
            assertEquals("a-2", fx.coordinator.switchingAgentId.value)
            assertTrue(vm.uiState.value.isConnecting)

            every { fx.tokenStore.getAgentId() } returns "a-2"
            advanceUntilIdle()

            assertFalse(vm.uiState.value.isConnecting)
            assertEquals(
                "the connected mark is re-read from the credentials the switch wrote",
                "a-2",
                vm.uiState.value.connectedAgentId,
            )
            assertNull(vm.uiState.value.error)
            assertTrue(fx.coordinator.pendingSessionsList.value)
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun connectToAgent_differentAgent_clearsAnEarlierFailure_thenShowsTheNewOne() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", TEST_DEVICE_ID, TEST_DEVICE_NAME) } throws
            RuntimeException("first attempt failed")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.connectToAgent(agent("a-1"))
        advanceUntilIdle()
        assertEquals("first attempt failed", vm.uiState.value.error)

        coEvery { fx.agentRepository.getMobileTokensForAgent("a-2", TEST_DEVICE_ID, TEST_DEVICE_NAME) } coAnswers {
            delay(10_000)
            throw RuntimeException("second attempt failed")
        }
        vm.connectToAgent(agent("a-2"))
        runCurrent()

        assertNull("a stale banner must not sit under the new spinner", vm.uiState.value.error)
        assertTrue(vm.uiState.value.isConnecting)

        advanceUntilIdle()

        assertEquals("second attempt failed", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isConnecting)
        assertFalse(fx.coordinator.pendingSessionsList.value)
    }

    @Test
    fun connectToAgent_alreadyConnectedAgent_navigatesWithoutTouchingTheCoordinator() = runTest {
        val fx = newFixture(connectedAgentId = "a-1")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.navigateToSessions.test {
            vm.connectToAgent(agent("a-1"))
            advanceUntilIdle()

            assertEquals(Unit, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        assertFalse(fx.coordinator.pendingSessionsList.value)
        assertNull(fx.coordinator.switchingAgentId.value)
        assertFalse(vm.uiState.value.isConnecting)
        coVerify(exactly = 0) { fx.agentRepository.getMobileTokensForAgent(any(), any(), any()) }
    }
}

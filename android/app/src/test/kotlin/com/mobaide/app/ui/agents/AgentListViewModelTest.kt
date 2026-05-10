package com.mobaide.app.ui.agents

import app.cash.turbine.test
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.MobileTokenResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.security.TrustKeyPinning
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
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
 * Unit tests for [AgentListViewModel] -- exercises the init side-effects,
 * refresh, add/re-register/delete dialog state transitions, the auto-connect
 * flow ([connectToAgent]), error propagation, and the guard against
 * concurrent connects.
 *
 * Dependencies exposing flows are stubbed with real [MutableStateFlow]
 * instances. [MainDispatcherRule] installs a `StandardTestDispatcher` as the
 * Main replacement so that `viewModelScope.launch { }` blocks are
 * deterministic under `runTest { advanceUntilIdle() }`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AgentListViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

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

    private class Fixture(
        val agentRepository: AgentRepository,
        val connectionStore: ConnectionStore,
        val tokenStore: TokenStore,
        val sessionManager: SessionManager,
        val agentEventBus: AgentEventBus,
        val userIdentityStore: UserIdentityStore,
        val trustKeyPinning: TrustKeyPinning,
        val agentsFlow: MutableStateFlow<List<AgentSummaryResponse>>,
    )

    private fun newFixture(
        initialAgents: List<AgentSummaryResponse> = emptyList(),
        connectedAgentId: String? = null,
    ): Fixture {
        val agentRepository = mockk<AgentRepository>(relaxed = true)
        val connectionStore = mockk<ConnectionStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val agentEventBus = mockk<AgentEventBus>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>(relaxed = true)
        val trustKeyPinning = mockk<TrustKeyPinning>(relaxed = true)

        val agentsFlow = MutableStateFlow(initialAgents)
        every { agentRepository.observeAgents() } returns agentsFlow
        every { tokenStore.getAgentId() } returns connectedAgentId
        every { agentEventBus.stop() } just Runs
        every { agentEventBus.start() } just Runs
        every { sessionManager.agentDisconnect() } just Runs
        every { sessionManager.markAgentConnected() } just Runs
        every { sessionManager.isLoggedIn() } returns (connectedAgentId != null)
        coEvery { agentRepository.refreshAgents() } just Runs

        return Fixture(
            agentRepository, connectionStore, tokenStore,
            sessionManager, agentEventBus, userIdentityStore,
            trustKeyPinning, agentsFlow,
        )
    }

    private fun Fixture.buildVm(): AgentListViewModel =
        AgentListViewModel(
            agentRepository, connectionStore, tokenStore,
            sessionManager, agentEventBus, userIdentityStore,
            trustKeyPinning,
        )

    // ---- init() side-effects ---------------------------------------------------

    @Test
    fun init_callsRefreshAgents_andSeedsEmptyUiState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coVerify(atLeast = 1) { fx.agentRepository.refreshAgents() }
        assertTrue(vm.uiState.value.agents.isEmpty())
        assertFalse(vm.uiState.value.isLoading)
        assertNull(vm.uiState.value.error)
    }

    @Test
    fun init_readsConnectedAgentId_fromTokenStore() = runTest {
        val fx = newFixture(connectedAgentId = "a-42")
        val vm = fx.buildVm()
        advanceUntilIdle()

        assertEquals("a-42", vm.uiState.value.connectedAgentId)
    }

    // ---- Agent list pass-through -----------------------------------------------

    @Test
    fun observeAgents_emissions_flowIntoUiState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.uiState.test {
            assertEquals(emptyList<AgentSummaryResponse>(), awaitItem().agents)

            fx.agentsFlow.value = listOf(agent("a-1"), agent("a-2"))
            advanceUntilIdle()

            val updated = awaitItem()
            assertEquals(2, updated.agents.size)
            assertEquals("a-1", updated.agents[0].agent_id)
            assertEquals("a-2", updated.agents[1].agent_id)
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- refresh() -------------------------------------------------------------

    @Test
    fun refresh_setsLoadingThenClearsIt() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.refresh()
        advanceUntilIdle()

        assertFalse(vm.uiState.value.isLoading)
        coVerify(atLeast = 2) { fx.agentRepository.refreshAgents() }
    }

    @Test
    fun refresh_onException_setsErrorMessage() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.refreshAgents() } throws RuntimeException("network down")

        vm.refresh()
        advanceUntilIdle()

        assertEquals("network down", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
    }

    // ---- clearError ------------------------------------------------------------

    @Test
    fun clearError_resetsErrorToNull() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // Seed an error
        coEvery { fx.agentRepository.refreshAgents() } throws RuntimeException("err")
        vm.refresh()
        advanceUntilIdle()
        assertEquals("err", vm.uiState.value.error)

        vm.clearError()

        assertNull(vm.uiState.value.error)
    }

    // ---- Add agent dialog state ------------------------------------------------

    @Test
    fun showAddDialog_setsStateToInput() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.showAddDialog()

        assertEquals(AddAgentDialogState.Input, vm.uiState.value.addDialogState)
    }

    @Test
    fun dismissAddDialog_setsStateToHidden() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.showAddDialog()
        vm.dismissAddDialog()

        assertEquals(AddAgentDialogState.Hidden, vm.uiState.value.addDialogState)
    }

    @Test
    fun createAgent_happyPath_transitionsToShowingCredentials() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.createAgent("my-agent") } returns
            com.mobaide.app.data.network.dto.CreateAgentResponse(
                agent_id = "a-new", name = "my-agent", token = "secret-tok",
            )

        vm.createAgent("my-agent")
        advanceUntilIdle()

        val state = vm.uiState.value.addDialogState
        assertTrue(state is AddAgentDialogState.ShowingCredentials)
        val creds = state as AddAgentDialogState.ShowingCredentials
        assertEquals("a-new", creds.agentId)
        assertEquals("secret-tok", creds.token)
    }

    @Test
    fun createAgent_onException_dismissesDialog_andSetsError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.createAgent("bad") } throws RuntimeException("409 conflict")

        vm.createAgent("bad")
        advanceUntilIdle()

        assertEquals(AddAgentDialogState.Hidden, vm.uiState.value.addDialogState)
        assertEquals("409 conflict", vm.uiState.value.error)
    }

    @Test
    fun onScanQrClicked_setsIsScannerLaunchingTrue() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.onScanQrClicked()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.isScannerLaunching)
    }

    @Test
    fun onScanResult_clearsIsScannerLaunching_onSuccess() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.onScanQrClicked()
        advanceUntilIdle()
        assertTrue(vm.uiState.value.isScannerLaunching)

        // Trigger a benign expired-payload result; the VM clears the flag on entry
        // before the expiry check, so the path used here doesn't matter.
        val expired = ParsedQrPairingPayload(
            version = "MOBA1",
            relayUrl = "https://relay.test",
            nonce = "nonce-x",
            trustKeyFp = "f".repeat(64),
            expUnix = 0L,
        )
        vm.onScanResult(Result.success(expired))
        advanceUntilIdle()

        assertFalse(vm.uiState.value.isScannerLaunching)
    }

    @Test
    fun onScanResult_clearsIsScannerLaunching_onFailure() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.onScanQrClicked()
        advanceUntilIdle()
        assertTrue(vm.uiState.value.isScannerLaunching)

        vm.onScanResult(Result.failure(RuntimeException("scanner crashed")))
        advanceUntilIdle()

        assertFalse(vm.uiState.value.isScannerLaunching)
    }

    @Test
    fun onScanQrClicked_whileAlreadyLaunching_isDebounced() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // Drain initial scan request emissions
        vm.scanRequests.test {
            vm.onScanQrClicked()
            assertEquals(Unit, awaitItem())

            // Second click while still launching -- must not emit again
            vm.onScanQrClicked()
            expectNoEvents()

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onScanQrClicked_afterPreviousScanCompletes_emitsAgain() = runTest {
        // Re-arm semantics: once a scan resolves (success or failure), the
        // debounce guard releases and a subsequent click must produce a
        // fresh _scanRequests emission. Protects against a regression
        // where the flag is left set or the predicate references a stale
        // field.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.scanRequests.test {
            vm.onScanQrClicked()
            assertEquals(Unit, awaitItem())

            // First scan resolves (failure path keeps the qrPairing flow
            // out of trustKeyPinning, so no extra mocking is needed).
            vm.onScanResult(Result.failure(RuntimeException("user cancelled")))
            advanceUntilIdle()
            assertFalse(vm.uiState.value.isScannerLaunching)

            // Second click after resolution must re-emit
            vm.onScanQrClicked()
            assertEquals(Unit, awaitItem())
            assertTrue(vm.uiState.value.isScannerLaunching)

            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Re-register dialog state ----------------------------------------------

    @Test
    fun showReRegisterDialog_setsStateToConfirming() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val a = agent("a-1", name = "My Agent")
        vm.showReRegisterDialog(a)

        val state = vm.uiState.value.reRegisterDialogState
        assertTrue(state is ReRegisterDialogState.Confirming)
        val confirming = state as ReRegisterDialogState.Confirming
        assertEquals("a-1", confirming.agentId)
        assertEquals("My Agent", confirming.agentName)
    }

    @Test
    fun dismissReRegisterDialog_setsStateToHidden() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.showReRegisterDialog(agent("a-1"))
        vm.dismissReRegisterDialog()

        assertEquals(ReRegisterDialogState.Hidden, vm.uiState.value.reRegisterDialogState)
    }

    @Test
    fun confirmReRegister_happyPath_transitionsToShowingCredentials() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.reregisterAgent("a-1") } returns
            com.mobaide.app.data.network.dto.ReRegisterAgentResponse(
                agent_id = "a-1", token = "rotated-tok",
            )

        vm.confirmReRegister("a-1")
        advanceUntilIdle()

        val state = vm.uiState.value.reRegisterDialogState
        assertTrue(state is ReRegisterDialogState.ShowingCredentials)
        val creds = state as ReRegisterDialogState.ShowingCredentials
        assertEquals("a-1", creds.agentId)
        assertEquals("rotated-tok", creds.token)
    }

    @Test
    fun confirmReRegister_onException_dismissesDialog_andSetsError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.reregisterAgent("a-1") } throws RuntimeException("500")

        vm.confirmReRegister("a-1")
        advanceUntilIdle()

        assertEquals(ReRegisterDialogState.Hidden, vm.uiState.value.reRegisterDialogState)
        assertEquals("500", vm.uiState.value.error)
    }

    // ---- Delete dialog state ---------------------------------------------------

    @Test
    fun showDeleteConfirm_setsDeleteConfirmAgent() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val a = agent("a-1")
        vm.showDeleteConfirm(a)

        assertEquals(a, vm.uiState.value.deleteConfirmAgent)
    }

    @Test
    fun dismissDeleteConfirm_clearsDeleteConfirmAgent() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.showDeleteConfirm(agent("a-1"))
        vm.dismissDeleteConfirm()

        assertNull(vm.uiState.value.deleteConfirmAgent)
    }

    @Test
    fun confirmDelete_happyPath_clearsConfirmAndDelegates() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.deleteAgent("a-1") } just Runs

        vm.confirmDelete("a-1")
        advanceUntilIdle()

        assertNull(vm.uiState.value.deleteConfirmAgent)
        coVerify(exactly = 1) { fx.agentRepository.deleteAgent("a-1") }
    }

    @Test
    fun confirmDelete_ofConnectedAgent_disconnectsFirst() = runTest {
        val fx = newFixture(connectedAgentId = "a-1")
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.deleteAgent("a-1") } just Runs

        vm.confirmDelete("a-1")
        advanceUntilIdle()

        verify(exactly = 1) { fx.agentEventBus.stop() }
        verify(exactly = 1) { fx.sessionManager.agentDisconnect() }
        assertNull(vm.uiState.value.connectedAgentId)
        coVerify(exactly = 1) { fx.agentRepository.deleteAgent("a-1") }
    }

    @Test
    fun confirmDelete_ofDifferentAgent_doesNotDisconnect() = runTest {
        val fx = newFixture(connectedAgentId = "a-1")
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.deleteAgent("a-2") } just Runs

        vm.confirmDelete("a-2")
        advanceUntilIdle()

        // stop() should NOT be called for deleting a different agent
        verify(exactly = 0) { fx.agentEventBus.stop() }
        verify(exactly = 0) { fx.sessionManager.agentDisconnect() }
        assertEquals("a-1", vm.uiState.value.connectedAgentId)
    }

    @Test
    fun confirmDelete_onException_setsError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.deleteAgent("a-1") } throws RuntimeException("forbidden")

        vm.confirmDelete("a-1")
        advanceUntilIdle()

        assertEquals("forbidden", vm.uiState.value.error)
    }

    // ---- connectToAgent --------------------------------------------------------

    @Test
    fun connectToAgent_happyPath_mintsTokensSavesCredentialsAndNavigates() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val tokens = MobileTokenResponse("access-tok", "refresh-tok", "bearer")
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1") } returns tokens
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        every { fx.connectionStore.listProfiles() } returns emptyList()

        vm.navigateToSessions.test {
            vm.connectToAgent(agent("a-1", name = "TestAgent"))
            advanceUntilIdle()

            // Verify full orchestration chain
            verify(exactly = 1) {
                fx.tokenStore.saveRelayCredentials(
                    serverUrl = any(),
                    agentId = "a-1",
                    pairingId = "user-42",
                    deviceId = "user-42",
                    accessToken = "access-tok",
                    refreshToken = "refresh-tok",
                )
            }
            verify(exactly = 1) { fx.sessionManager.markAgentConnected() }
            verify(exactly = 1) { fx.connectionStore.captureActiveProfile(fx.tokenStore, "Agent: TestAgent") }
            verify(exactly = 1) { fx.agentEventBus.start() }

            // Verify state updates
            assertEquals("a-1", vm.uiState.value.connectedAgentId)
            assertFalse(vm.uiState.value.isConnecting)

            // Verify navigation event
            assertEquals(Unit, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun connectToAgent_alreadyConnectedToSameAgent_navigatesWithoutReconnect() = runTest {
        val fx = newFixture(connectedAgentId = "a-1")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.navigateToSessions.test {
            vm.connectToAgent(agent("a-1"))
            advanceUntilIdle()

            assertEquals(Unit, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        // Should NOT call getMobileTokensForAgent when already connected
        coVerify(exactly = 0) { fx.agentRepository.getMobileTokensForAgent(any()) }
    }

    @Test
    fun connectToAgent_onException_setsError_andClearsConnecting() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1") } throws
            RuntimeException("token minting failed")

        vm.connectToAgent(agent("a-1"))
        advanceUntilIdle()

        assertEquals("token minting failed", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isConnecting)
    }

    @Test
    fun connectToAgent_whileAlreadyConnecting_isIgnored() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // Simulate a slow connect by not resolving the coEvery
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1") } coAnswers {
            delay(10_000)
            MobileTokenResponse("at", "rt", "bearer")
        }

        // First connect
        vm.connectToAgent(agent("a-1"))
        // Do not advanceUntilIdle -- let the first connect be "in progress"
        // The isConnecting flag should be set after the coroutine starts
        runCurrent()
        assertTrue(vm.uiState.value.isConnecting)

        // Second connect should be ignored
        vm.connectToAgent(agent("a-2"))
        runCurrent()

        // Only one call to getMobileTokensForAgent should be made
        coVerify(exactly = 1) { fx.agentRepository.getMobileTokensForAgent(any()) }
    }
}

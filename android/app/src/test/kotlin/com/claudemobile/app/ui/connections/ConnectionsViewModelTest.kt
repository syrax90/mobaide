package com.claudemobile.app.ui.connections

import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.ConnectionStore.ConnectionProfile
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import io.mockk.verifyOrder
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [ConnectionsViewModel] — covers initial state loading,
 * profile switching (with logout/login lifecycle), and profile removal.
 * All collaborators are mocked via MockK.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ConnectionsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private val directProfile = ConnectionProfile(
        id = "prof-1",
        label = "Direct: https://agent.local",
        mode = "direct",
        agentUrl = "https://agent.local",
        accessToken = "at-1",
        refreshToken = "rt-1",
    )

    private val relayProfile = ConnectionProfile(
        id = "prof-2",
        label = "Relay: agent-abc",
        mode = "relay",
        relayServerUrl = "https://relay.example.com",
        agentId = "agent-abc",
        pairingId = "pair-1",
        deviceId = "dev-1",
        accessToken = "at-2",
        refreshToken = "rt-2",
    )

    private fun buildDeps(
        profiles: List<ConnectionProfile> = listOf(directProfile, relayProfile),
        activeId: String? = "prof-1",
    ): Deps {
        val connectionStore = mockk<ConnectionStore>(relaxUnitFun = true)
        every { connectionStore.listProfiles() } returns profiles
        every { connectionStore.getActiveProfileId() } returns activeId
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)
        val eventBus = mockk<AgentEventBus>(relaxUnitFun = true)
        return Deps(connectionStore, tokenStore, sessionManager, eventBus)
    }

    private data class Deps(
        val connectionStore: ConnectionStore,
        val tokenStore: TokenStore,
        val sessionManager: SessionManager,
        val eventBus: AgentEventBus,
    )

    private fun createVm(deps: Deps) = ConnectionsViewModel(
        deps.connectionStore, deps.tokenStore, deps.sessionManager, deps.eventBus,
    )

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_loadsProfilesAndActiveId() = runTest {
        val deps = buildDeps()
        val vm = createVm(deps)

        val state = vm.uiState.value
        assertEquals(2, state.profiles.size)
        assertEquals("prof-1", state.activeProfileId)
        assertEquals("Direct: https://agent.local", state.profiles[0].label)
        assertEquals("Relay: agent-abc", state.profiles[1].label)
    }

    @Test
    fun initialState_emptyProfiles_showsEmptyList() = runTest {
        val deps = buildDeps(profiles = emptyList(), activeId = null)
        val vm = createVm(deps)

        val state = vm.uiState.value
        assertEquals(0, state.profiles.size)
        assertNull(state.activeProfileId)
    }

    // ---- switchProfile ------------------------------------------------------

    @Test
    fun switchProfile_tearsDownCurrentConnection_andActivatesNewProfile() = runTest {
        val deps = buildDeps()
        val vm = createVm(deps)

        vm.switchProfile("prof-2")

        // Verify teardown → activate → re-login ordering
        verifyOrder {
            deps.sessionManager.forceLogout()
            deps.eventBus.stop()
            deps.connectionStore.activateProfile("prof-2", deps.tokenStore)
            deps.sessionManager.markLoggedIn()
            deps.eventBus.start()
        }
    }

    @Test
    fun switchProfile_refreshesUiState() = runTest {
        val deps = buildDeps()
        // After switch, the store returns updated active id
        var currentActiveId = "prof-1"
        every { deps.connectionStore.getActiveProfileId() } answers { currentActiveId }
        every { deps.connectionStore.activateProfile(any(), any()) } answers {
            currentActiveId = firstArg()
        }

        val vm = createVm(deps)
        assertEquals("prof-1", vm.uiState.value.activeProfileId)

        vm.switchProfile("prof-2")

        assertEquals("prof-2", vm.uiState.value.activeProfileId)
    }

    // ---- removeProfile ------------------------------------------------------

    @Test
    fun removeProfile_delegatesToConnectionStore() = runTest {
        val deps = buildDeps()
        val vm = createVm(deps)

        vm.removeProfile("prof-1")

        verify(exactly = 1) { deps.connectionStore.removeProfile("prof-1") }
    }

    @Test
    fun removeProfile_refreshesUiState() = runTest {
        val deps = buildDeps()
        // After removal, only the relay profile remains
        var currentProfiles = listOf(directProfile, relayProfile)
        every { deps.connectionStore.listProfiles() } answers { currentProfiles }
        every { deps.connectionStore.removeProfile(any()) } answers {
            currentProfiles = currentProfiles.filter { it.id != firstArg<String>() }
        }

        val vm = createVm(deps)
        assertEquals(2, vm.uiState.value.profiles.size)

        vm.removeProfile("prof-1")

        assertEquals(1, vm.uiState.value.profiles.size)
        assertEquals("prof-2", vm.uiState.value.profiles[0].id)
    }
}

package com.claudemobile.app.ui.settings

import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [SettingsViewModel] — the ViewModel reads connection info
 * and username synchronously from [AuthRepository] / [TokenStore] inside its
 * constructor, then exposes an immutable [SettingsUiState] and delegates
 * `logout()` to the repository.
 *
 * CLA-32 changed the constructor to accept a [TokenStore] and replaced
 * `agentUrl` with `connectionInfo` that adapts to the active connection mode.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private fun stubTokenStore(
        mode: TokenStore.ConnectionMode = TokenStore.ConnectionMode.DIRECT,
        relayServerUrl: String? = null,
        agentId: String? = null,
        deviceId: String? = null,
    ): TokenStore {
        val store = mockk<TokenStore>()
        every { store.getConnectionMode() } returns mode
        every { store.getRelayServerUrl() } returns relayServerUrl
        every { store.getAgentId() } returns agentId
        every { store.getDeviceId() } returns deviceId
        return store
    }

    // ---- Direct mode state --------------------------------------------------

    @Test
    fun initialState_directMode_showsDirectConnectionInfo() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns "alice"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore)

        val state = vm.uiState.value
        assertEquals("Direct: https://example.com", state.connectionInfo)
        assertEquals("alice", state.username)
    }

    @Test
    fun initialState_directMode_nullAgentUrl_showsUnknown() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns null
        every { authRepository.getUsername() } returns "alice"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore)

        assertEquals("Direct: unknown", vm.uiState.value.connectionInfo)
    }

    // ---- Relay mode state ---------------------------------------------------

    @Test
    fun initialState_relayMode_showsRelayConnectionInfo() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUsername() } returns null
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.RELAY,
            relayServerUrl = "https://relay.example.com",
            agentId = "agent-abc",
            deviceId = "dev-123",
        )

        val vm = SettingsViewModel(authRepository, tokenStore)

        val state = vm.uiState.value
        assertEquals("Relay: https://relay.example.com (agent agent-abc)", state.connectionInfo)
        // Relay mode falls back to deviceId when username is null
        assertEquals("dev-123", state.username)
    }

    @Test
    fun initialState_relayMode_nullServerUrlAndAgentId_showsUnknown() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUsername() } returns null
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.RELAY,
            relayServerUrl = null,
            agentId = null,
            deviceId = null,
        )

        val vm = SettingsViewModel(authRepository, tokenStore)

        assertEquals("Relay: unknown (agent unknown)", vm.uiState.value.connectionInfo)
        assertEquals("", vm.uiState.value.username)
    }

    // ---- Username fallback --------------------------------------------------

    @Test
    fun initialState_usernameFromAuthRepository_takePriority() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns "alice"
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.DIRECT,
            deviceId = "dev-123",
        )

        val vm = SettingsViewModel(authRepository, tokenStore)

        // AuthRepository username takes priority over deviceId
        assertEquals("alice", vm.uiState.value.username)
    }

    @Test
    fun initialState_whenUsernameNull_fallsBackToDeviceId() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns null
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.DIRECT,
            deviceId = "dev-xyz",
        )

        val vm = SettingsViewModel(authRepository, tokenStore)

        assertEquals("dev-xyz", vm.uiState.value.username)
    }

    @Test
    fun initialState_whenBothUsernameAndDeviceIdNull_usernameIsEmpty() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns null
        every { authRepository.getUsername() } returns null
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.DIRECT,
            deviceId = null,
        )

        val vm = SettingsViewModel(authRepository, tokenStore)

        assertEquals("", vm.uiState.value.username)
    }

    // ---- logout() delegation -----------------------------------------------

    @Test
    fun logout_delegatesToAuthRepository_withoutMutatingUiState() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns "alice"
        every { authRepository.userLogout() } just Runs
        every { authRepository.getUserEmail() } returns null
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore)
        val before = vm.uiState.value

        vm.logout()

        verify(exactly = 1) { authRepository.userLogout() }
        assertEquals(before, vm.uiState.value)
    }
}

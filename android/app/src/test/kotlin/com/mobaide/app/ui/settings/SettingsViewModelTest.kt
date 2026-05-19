package com.mobaide.app.ui.settings

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coVerify
import io.mockk.coEvery
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [SettingsViewModel] — the ViewModel reads connection info
 * and the user's email synchronously from [AuthRepository] / [TokenStore]
 * inside its constructor, then exposes an immutable [SettingsUiState] and
 * delegates `logout()` to the repository.
 *
 * "Logged in as" now sources from [AuthRepository.getUserEmail] (i.e. the
 * `UserIdentityStore` populated by the central-server login flows) rather
 * than the agent JWT `sub` claim, so the value is the same regardless of
 * whether the user signed in with email/password or Google.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun stubTokenStore(
        mode: TokenStore.ConnectionMode = TokenStore.ConnectionMode.DIRECT,
        relayServerUrl: String? = null,
        agentId: String? = null,
    ): TokenStore {
        val store = mockk<TokenStore>()
        every { store.getConnectionMode() } returns mode
        every { store.getRelayServerUrl() } returns relayServerUrl
        every { store.getAgentId() } returns agentId
        return store
    }

    private fun stubPreferencesStore(
        mode: TerminalDisplayMode = TerminalDisplayMode.Full,
        staticColumns: Int = TERMINAL_STATIC_COLUMNS_DEFAULT,
    ): PreferencesStore = mockk(relaxed = true) {
        every { terminalDisplayMode } returns flowOf(mode)
        every { terminalStaticColumns } returns flowOf(staticColumns)
    }

    @Test
    fun initialState_directMode_showsDirectConnectionInfo() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())

        val state = vm.uiState.value
        assertEquals("Direct: https://example.com", state.connectionInfo)
        assertEquals("alice@example.com", state.userEmail)
    }

    @Test
    fun initialState_directMode_nullAgentUrl_showsUnknown() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns null
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())

        assertEquals("Direct: unknown", vm.uiState.value.connectionInfo)
    }

    @Test
    fun initialState_relayMode_showsRelayConnectionInfo() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns "bob@example.com"
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.RELAY,
            relayServerUrl = "https://relay.example.com",
            agentId = "agent-abc",
        )

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())

        val state = vm.uiState.value
        assertEquals("Relay: https://relay.example.com (agent agent-abc)", state.connectionInfo)
        assertEquals("bob@example.com", state.userEmail)
    }

    @Test
    fun initialState_relayMode_nullServerUrlAndAgentId_showsUnknown() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns null
        val tokenStore = stubTokenStore(
            mode = TokenStore.ConnectionMode.RELAY,
            relayServerUrl = null,
            agentId = null,
        )

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())

        assertEquals("Relay: unknown (agent unknown)", vm.uiState.value.connectionInfo)
    }

    @Test
    fun initialState_userEmail_sourcedFromAuthRepository() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns "carol@example.com"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())

        assertEquals("carol@example.com", vm.uiState.value.userEmail)
    }

    @Test
    fun initialState_userEmailNull_fallsBackToEmptyString() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns null
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())

        assertEquals("", vm.uiState.value.userEmail)
    }

    @Test
    fun logout_delegatesToAuthRepository_withoutMutatingUiState() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns "alice@example.com"
        every { authRepository.userLogout() } just Runs
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)

        val vm = SettingsViewModel(authRepository, tokenStore, stubPreferencesStore())
        val before = vm.uiState.value

        vm.logout()

        verify(exactly = 1) { authRepository.userLogout() }
        assertEquals(before, vm.uiState.value)
    }

    @Test
    fun initialState_loadsTerminalDisplayPreferences() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)
        val prefs = stubPreferencesStore(
            mode = TerminalDisplayMode.Static,
            staticColumns = 100,
        )

        val vm = SettingsViewModel(authRepository, tokenStore, prefs)
        advanceUntilIdle()

        assertEquals(TerminalDisplayMode.Static, vm.uiState.value.terminalDisplayMode)
        assertEquals(100, vm.uiState.value.terminalStaticColumns)
    }

    @Test
    fun setTerminalDisplayMode_writesThroughToPreferencesStore() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)
        val prefs = stubPreferencesStore()
        coEvery { prefs.setTerminalDisplayMode(any()) } just Runs

        val vm = SettingsViewModel(authRepository, tokenStore, prefs)
        vm.setTerminalDisplayMode(TerminalDisplayMode.Static)
        advanceUntilIdle()

        coVerify(exactly = 1) { prefs.setTerminalDisplayMode(TerminalDisplayMode.Static) }
    }

    @Test
    fun setTerminalStaticColumns_clampsToValidRangeBeforeWrite() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val tokenStore = stubTokenStore(mode = TokenStore.ConnectionMode.DIRECT)
        val prefs = stubPreferencesStore()
        coEvery { prefs.setTerminalStaticColumns(any()) } just Runs

        val vm = SettingsViewModel(authRepository, tokenStore, prefs)
        vm.setTerminalStaticColumns(40)
        vm.setTerminalStaticColumns(300)
        vm.setTerminalStaticColumns(150)
        advanceUntilIdle()

        coVerify(exactly = 1) { prefs.setTerminalStaticColumns(60) }
        coVerify(exactly = 1) { prefs.setTerminalStaticColumns(240) }
        coVerify(exactly = 1) { prefs.setTerminalStaticColumns(150) }
    }
}

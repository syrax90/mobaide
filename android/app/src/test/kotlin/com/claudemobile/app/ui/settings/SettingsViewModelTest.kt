package com.claudemobile.app.ui.settings

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
 * Unit tests for [SettingsViewModel] — the ViewModel reads `agentUrl` and
 * `username` synchronously from [AuthRepository] inside its constructor, then
 * exposes an immutable [SettingsUiState] and delegates `logout()` to the
 * repository. Both collaborators are mocked with MockK; no coroutines run in
 * this ViewModel, but the [MainDispatcherRule] is installed for consistency
 * with the other ViewModel test files in this package.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Initial state from AuthRepository ---------------------------------

    @Test
    fun initialState_populatesServerUrlAndUsername_fromAuthRepository() = runTest {
        val authRepository = mockk<AuthRepository>()
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns "alice"

        val vm = SettingsViewModel(authRepository)

        val state = vm.uiState.value
        assertEquals("https://example.com", state.agentUrl)
        assertEquals("alice", state.username)
        // Read-through: the constructor must consult both accessors exactly once.
        verify(exactly = 1) { authRepository.getAgentUrl() }
        verify(exactly = 1) { authRepository.getUsername() }
    }

    @Test
    fun initialState_coercesNullServerUrl_toEmptyString() = runTest {
        val authRepository = mockk<AuthRepository>()
        every { authRepository.getAgentUrl() } returns null
        every { authRepository.getUsername() } returns "alice"

        val vm = SettingsViewModel(authRepository)

        val state = vm.uiState.value
        assertEquals("", state.agentUrl)
        assertEquals("alice", state.username)
    }

    @Test
    fun initialState_coercesNullUsername_toEmptyString() = runTest {
        val authRepository = mockk<AuthRepository>()
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns null

        val vm = SettingsViewModel(authRepository)

        val state = vm.uiState.value
        assertEquals("https://example.com", state.agentUrl)
        assertEquals("", state.username)
    }

    @Test
    fun initialState_whenBothNull_isFullyEmpty() = runTest {
        val authRepository = mockk<AuthRepository>()
        every { authRepository.getAgentUrl() } returns null
        every { authRepository.getUsername() } returns null

        val vm = SettingsViewModel(authRepository)

        assertEquals(SettingsUiState(), vm.uiState.value)
    }

    // ---- logout() delegation -----------------------------------------------

    @Test
    fun logout_delegatesToAuthRepository_withoutMutatingUiState() = runTest {
        val authRepository = mockk<AuthRepository>()
        every { authRepository.getAgentUrl() } returns "https://example.com"
        every { authRepository.getUsername() } returns "alice"
        every { authRepository.logout() } just Runs

        val vm = SettingsViewModel(authRepository)
        val before = vm.uiState.value

        vm.logout()

        verify(exactly = 1) { authRepository.logout() }
        // UI state is not reset on logout — that's the caller's responsibility
        // (navigation back to LoginScreen, which discards this ViewModel).
        assertEquals(before, vm.uiState.value)
    }
}

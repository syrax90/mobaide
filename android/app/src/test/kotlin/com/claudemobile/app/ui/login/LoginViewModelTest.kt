package com.claudemobile.app.ui.login

import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
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
 * Unit tests for [LoginViewModel] — exercises blank-field validation, the
 * loading→success state transition, and the three failure branches
 * (`ApiException(401)`, other [ApiException], non-API [Exception]) plus
 * the `update*` helpers that clear the pending error.
 *
 * [AuthRepository] is mocked with MockK and all async paths are driven by the
 * [MainDispatcherRule]'s [kotlinx.coroutines.test.StandardTestDispatcher] so
 * that `viewModelScope.launch { ... }` only runs when the test yields via
 * `advanceUntilIdle()`. This lets us observe the intermediate `isLoading=true`
 * state before the repository call is allowed to resolve.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    // ---- Helpers ------------------------------------------------------------

    private fun primed(vm: LoginViewModel) = vm.apply {
        updateAgentUrl("https://example.com")
        updateUsername("alice")
        updatePassword("secret")
    }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_hasDefaultServerUrlPrefix_andNoErrorOrLoading() = runTest {
        val vm = LoginViewModel(authRepository)
        val state = vm.uiState.value
        assertEquals("https://", state.agentUrl)
        assertEquals("", state.username)
        assertEquals("", state.password)
        assertFalse(state.isLoading)
        assertNull(state.error)
        assertFalse(vm.loginSuccess.value)
    }

    // ---- Blank-field validation ---------------------------------------------

    @Test
    fun login_whenServerUrlBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.updateAgentUrl("   ")
        vm.updateUsername("alice")
        vm.updatePassword("secret")

        vm.login()
        advanceUntilIdle()

        assertEquals("All fields are required", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.login(any(), any(), any()) }
    }

    @Test
    fun login_whenUsernameBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.updateAgentUrl("https://example.com")
        vm.updateUsername("")
        vm.updatePassword("secret")

        vm.login()
        advanceUntilIdle()

        assertEquals("All fields are required", vm.uiState.value.error)
        coVerify(exactly = 0) { authRepository.login(any(), any(), any()) }
    }

    @Test
    fun login_whenPasswordBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.updateAgentUrl("https://example.com")
        vm.updateUsername("alice")
        vm.updatePassword("")

        vm.login()
        advanceUntilIdle()

        assertEquals("All fields are required", vm.uiState.value.error)
        coVerify(exactly = 0) { authRepository.login(any(), any(), any()) }
    }

    // ---- Loading → success path ---------------------------------------------

    @Test
    fun login_happyPath_setsLoadingTrue_thenEmitsLoginSuccess() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        // A [CompletableDeferred] lets the test body observe the intermediate
        // `isLoading = true` state: the repository mock suspends on `await()`
        // until the test explicitly resolves it, so `runCurrent()` can drain
        // the launch body up to the suspension point without running the
        // success branch. A plain `just Runs` answer would complete
        // synchronously and the intermediate state would be unobservable
        // (StateFlow keeps only the latest value).
        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        coEvery {
            authRepository.login("https://example.com", "alice", "secret")
        } coAnswers { gate.await() }

        assertFalse(vm.uiState.value.isLoading)
        assertFalse(vm.loginSuccess.value)

        vm.login()
        // Step 1: drain pending work until the coroutine suspends on
        // `gate.await()`. At this point the VM has flipped `isLoading = true`
        // but not yet set `loginSuccess`.
        runCurrent()
        val loading = vm.uiState.value
        assertTrue("must enter loading state before repository resolves", loading.isLoading)
        assertNull(loading.error)
        assertFalse(
            "must not signal success until repository returns",
            vm.loginSuccess.value,
        )

        // Step 2: resolve the gate so the success branch runs.
        gate.complete(Unit)
        advanceUntilIdle()

        assertTrue(
            "loginSuccess must flip to true after successful login",
            vm.loginSuccess.value,
        )
        // On success the ViewModel never flips `isLoading` back — it signals
        // completion via the separate `loginSuccess` StateFlow. Assert that
        // the error field stays null.
        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) {
            authRepository.login("https://example.com", "alice", "secret")
        }
    }

    // ---- ApiException(401) → "Invalid credentials" --------------------------

    @Test
    fun login_on401_setsInvalidCredentialsError_andClearsLoading() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        coEvery {
            authRepository.login("https://example.com", "alice", "secret")
        } throws ApiException(401, """{"detail":"bad creds"}""")

        vm.login()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Invalid credentials", state.error)
        assertFalse(state.isLoading)
        assertFalse(vm.loginSuccess.value)
    }

    // ---- Other ApiException → "Connection failed: ..." ---------------------

    @Test
    fun login_onNon401ApiException_setsConnectionFailedError_withServerMessage() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        val boom = ApiException(500, "server exploded")
        coEvery {
            authRepository.login("https://example.com", "alice", "secret")
        } throws boom

        vm.login()
        advanceUntilIdle()

        val state = vm.uiState.value
        // ApiException's message is "HTTP $code: $errorBody" — the ViewModel
        // concatenates "Connection failed: " in front of it.
        assertEquals("Connection failed: ${boom.message}", state.error)
        assertTrue(
            "error must mention connection failure",
            state.error!!.startsWith("Connection failed:"),
        )
        assertTrue(
            "error must include the underlying ApiException message",
            state.error!!.contains("500") && state.error!!.contains("server exploded"),
        )
        assertFalse(state.isLoading)
        assertFalse(vm.loginSuccess.value)
    }

    // ---- Non-API exception path ---------------------------------------------

    @Test
    fun login_onGenericException_setsConnectionFailedError_withUnderlyingMessage() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        coEvery {
            authRepository.login("https://example.com", "alice", "secret")
        } throws java.net.UnknownHostException("example.com: nodename nor servname provided")

        vm.login()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals(
            "Connection failed: example.com: nodename nor servname provided",
            state.error,
        )
        assertFalse(state.isLoading)
        assertFalse(vm.loginSuccess.value)
    }

    // ---- update* helpers clear the pending error ----------------------------

    @Test
    fun updateAgentUrl_clearsPendingError() = runTest {
        val vm = LoginViewModel(authRepository)
        // Seed an error via the blank-field path.
        vm.login()
        advanceUntilIdle()
        assertEquals("All fields are required", vm.uiState.value.error)

        vm.updateAgentUrl("https://example.com")

        assertNull(vm.uiState.value.error)
        assertEquals("https://example.com", vm.uiState.value.agentUrl)
    }

    @Test
    fun updateUsername_clearsPendingError() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.login()
        advanceUntilIdle()
        assertEquals("All fields are required", vm.uiState.value.error)

        vm.updateUsername("alice")

        assertNull(vm.uiState.value.error)
        assertEquals("alice", vm.uiState.value.username)
    }

    @Test
    fun updatePassword_clearsPendingError() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.login()
        advanceUntilIdle()
        assertEquals("All fields are required", vm.uiState.value.error)

        vm.updatePassword("secret")

        assertNull(vm.uiState.value.error)
        assertEquals("secret", vm.uiState.value.password)
    }
}

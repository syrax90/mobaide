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
 * loading→success state transition, Google Sign-In callback, and the
 * failure branches (`ApiException(401)`, other [ApiException],
 * non-API [Exception]) plus the `update*` helpers that clear the pending
 * error.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    // ---- Helpers ------------------------------------------------------------

    private fun primed(vm: LoginViewModel) = vm.apply {
        updateEmail("alice@example.com")
        updatePassword("secret")
    }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_hasEmptyFields_andNoErrorOrLoading() = runTest {
        val vm = LoginViewModel(authRepository)
        val state = vm.uiState.value
        assertEquals("", state.email)
        assertEquals("", state.password)
        assertFalse(state.isLoading)
        assertNull(state.error)
        assertFalse(vm.loginSuccess.value)
    }

    // ---- Blank-field validation ---------------------------------------------

    @Test
    fun login_whenEmailBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.updateEmail("   ")
        vm.updatePassword("secret")

        vm.loginWithPassword()
        advanceUntilIdle()

        assertEquals("Email and password are required", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.loginWithPassword(any(), any()) }
    }

    @Test
    fun login_whenPasswordBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.updateEmail("alice@example.com")
        vm.updatePassword("")

        vm.loginWithPassword()
        advanceUntilIdle()

        assertEquals("Email and password are required", vm.uiState.value.error)
        coVerify(exactly = 0) { authRepository.loginWithPassword(any(), any()) }
    }

    // ---- Loading → success path ---------------------------------------------

    @Test
    fun login_happyPath_setsLoadingTrue_thenEmitsLoginSuccess() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } coAnswers { gate.await() }

        assertFalse(vm.uiState.value.isLoading)
        assertFalse(vm.loginSuccess.value)

        vm.loginWithPassword()
        runCurrent()
        val loading = vm.uiState.value
        assertTrue("must enter loading state before repository resolves", loading.isLoading)
        assertNull(loading.error)
        assertFalse(vm.loginSuccess.value)

        gate.complete(Unit)
        advanceUntilIdle()

        assertTrue(vm.loginSuccess.value)
        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) {
            authRepository.loginWithPassword("alice@example.com", "secret")
        }
    }

    // ---- ApiException(401) → "Invalid email or password" --------------------

    @Test
    fun login_on401_setsInvalidCredentialsError_andClearsLoading() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } throws ApiException(401, """{"detail":"bad creds"}""")

        vm.loginWithPassword()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Invalid email or password", state.error)
        assertFalse(state.isLoading)
        assertFalse(vm.loginSuccess.value)
    }

    // ---- Other ApiException → "Login failed: ..." --------------------------

    @Test
    fun login_onNon401ApiException_setsLoginFailedError() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        val boom = ApiException(500, "server exploded")
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } throws boom

        vm.loginWithPassword()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Login failed: ${boom.message}", state.error)
        assertFalse(state.isLoading)
    }

    // ---- Non-API exception path ---------------------------------------------

    @Test
    fun login_onGenericException_setsLoginFailedError() = runTest {
        val vm = primed(LoginViewModel(authRepository))
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } throws java.net.UnknownHostException("example.com: nodename nor servname provided")

        vm.loginWithPassword()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals(
            "Login failed: example.com: nodename nor servname provided",
            state.error,
        )
        assertFalse(state.isLoading)
    }

    // ---- Google Sign-In callback -------------------------------------------

    @Test
    fun onGoogleIdToken_happyPath_emitsLoginSuccess() = runTest {
        val vm = LoginViewModel(authRepository)
        coEvery { authRepository.loginWithGoogle("google-token") } returns Unit

        vm.onGoogleIdToken("google-token")
        advanceUntilIdle()

        assertTrue(vm.loginSuccess.value)
        coVerify(exactly = 1) { authRepository.loginWithGoogle("google-token") }
    }

    @Test
    fun onGoogleIdToken_onFailure_setsError() = runTest {
        val vm = LoginViewModel(authRepository)
        coEvery {
            authRepository.loginWithGoogle("bad-token")
        } throws ApiException(401, "invalid token")

        vm.onGoogleIdToken("bad-token")
        advanceUntilIdle()

        assertFalse(vm.loginSuccess.value)
        assertTrue(vm.uiState.value.error!!.contains("Google Sign-In failed"))
    }

    // ---- update* helpers clear the pending error ----------------------------

    @Test
    fun updateEmail_clearsPendingError() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.loginWithPassword()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updateEmail("alice@example.com")

        assertNull(vm.uiState.value.error)
        assertEquals("alice@example.com", vm.uiState.value.email)
    }

    @Test
    fun updatePassword_clearsPendingError() = runTest {
        val vm = LoginViewModel(authRepository)
        vm.loginWithPassword()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updatePassword("secret")

        assertNull(vm.uiState.value.error)
        assertEquals("secret", vm.uiState.value.password)
    }
}

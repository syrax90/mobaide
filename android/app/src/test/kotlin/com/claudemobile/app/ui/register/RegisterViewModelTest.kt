package com.claudemobile.app.ui.register

import app.cash.turbine.test
import com.claudemobile.app.data.auth.GoogleAuthResult
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
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
 * Unit tests for [RegisterViewModel] -- exercises blank-field validation,
 * password-mismatch validation, the loading->success state transition,
 * Google Sign-In callback, and the failure branches (`ApiException(409)`,
 * other [ApiException], non-API [Exception]) plus the `update*` helpers
 * that clear the pending error and the `setError` helper.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class RegisterViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    // ---- Helpers ------------------------------------------------------------

    private fun primed(vm: RegisterViewModel) = vm.apply {
        updateEmail("alice@example.com")
        updatePassword("secret")
        updateConfirmPassword("secret")
    }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_hasEmptyFields_andNoErrorOrLoading() = runTest {
        val vm = RegisterViewModel(authRepository)
        val state = vm.uiState.value
        assertEquals("", state.email)
        assertEquals("", state.password)
        assertEquals("", state.confirmPassword)
        assertFalse(state.isLoading)
        assertNull(state.error)
        assertFalse(vm.registerSuccess.value)
    }

    // ---- Blank-field validation ---------------------------------------------

    @Test
    fun register_whenEmailBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("   ")
        vm.updatePassword("secret")
        vm.updateConfirmPassword("secret")

        vm.register()
        advanceUntilIdle()

        assertEquals("Email and password are required", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.register(any(), any()) }
    }

    @Test
    fun register_whenPasswordBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("alice@example.com")
        vm.updatePassword("")
        vm.updateConfirmPassword("")

        vm.register()
        advanceUntilIdle()

        assertEquals("Email and password are required", vm.uiState.value.error)
        coVerify(exactly = 0) { authRepository.register(any(), any()) }
    }

    // ---- Password mismatch validation ---------------------------------------

    @Test
    fun register_whenPasswordsDoNotMatch_setsError_andDoesNotCallRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("alice@example.com")
        vm.updatePassword("secret")
        vm.updateConfirmPassword("different")

        vm.register()
        advanceUntilIdle()

        assertEquals("Passwords do not match", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.register(any(), any()) }
    }

    // ---- Loading -> success path --------------------------------------------

    @Test
    fun register_happyPath_setsLoadingTrue_thenEmitsRegisterSuccess() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        val gate = CompletableDeferred<Unit>()
        coEvery {
            authRepository.register("alice@example.com", "secret")
        } coAnswers { gate.await() }

        assertFalse(vm.uiState.value.isLoading)
        assertFalse(vm.registerSuccess.value)

        vm.register()
        runCurrent()
        val loading = vm.uiState.value
        assertTrue("must enter loading state before repository resolves", loading.isLoading)
        assertNull(loading.error)
        assertFalse(vm.registerSuccess.value)

        gate.complete(Unit)
        advanceUntilIdle()

        assertTrue(vm.registerSuccess.value)
        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) {
            authRepository.register("alice@example.com", "secret")
        }
    }

    @Test
    fun register_happyPath_trimsEmail() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("  alice@example.com  ")
        vm.updatePassword("secret")
        vm.updateConfirmPassword("secret")
        coEvery { authRepository.register("alice@example.com", "secret") } returns Unit

        vm.register()
        advanceUntilIdle()

        assertTrue(vm.registerSuccess.value)
        coVerify(exactly = 1) {
            authRepository.register("alice@example.com", "secret")
        }
    }

    // ---- ApiException(409) -> "already exists" ------------------------------

    @Test
    fun register_on409_setsAlreadyExistsError_andClearsLoading() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret")
        } throws ApiException(409, """{"detail":"duplicate email"}""")

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("An account with this email already exists", state.error)
        assertFalse(state.isLoading)
        assertFalse(vm.registerSuccess.value)
    }

    // ---- Other ApiException -> "Registration failed: ..." -------------------

    @Test
    fun register_onNon409ApiException_setsRegistrationFailedError() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        val boom = ApiException(500, "server exploded")
        coEvery {
            authRepository.register("alice@example.com", "secret")
        } throws boom

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Registration failed: ${boom.message}", state.error)
        assertFalse(state.isLoading)
    }

    // ---- Non-API exception path ---------------------------------------------

    @Test
    fun register_onGenericException_setsRegistrationFailedError() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret")
        } throws java.net.UnknownHostException("example.com: nodename nor servname provided")

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals(
            "Registration failed: example.com: nodename nor servname provided",
            state.error,
        )
        assertFalse(state.isLoading)
    }

    // ---- Google Sign-In callback --------------------------------------------

    @Test
    fun onGoogleAuthResult_success_emitsRegisterSuccess() = runTest {
        val vm = RegisterViewModel(authRepository)
        coEvery { authRepository.loginWithGoogle("google-token") } returns Unit

        vm.onGoogleAuthResult(GoogleAuthResult.Success("google-token"))
        advanceUntilIdle()

        assertTrue(vm.registerSuccess.value)
        coVerify(exactly = 1) { authRepository.loginWithGoogle("google-token") }
    }

    @Test
    fun onGoogleAuthResult_success_onApiFailure_setsError() = runTest {
        val vm = RegisterViewModel(authRepository)
        coEvery {
            authRepository.loginWithGoogle("bad-token")
        } throws ApiException(401, "invalid token")

        vm.onGoogleAuthResult(GoogleAuthResult.Success("bad-token"))
        advanceUntilIdle()

        assertFalse(vm.registerSuccess.value)
        assertTrue(vm.uiState.value.error!!.contains("Google Sign-In failed"))
        assertFalse(vm.uiState.value.isLoading)
    }

    @Test
    fun onGoogleAuthResult_success_onGenericFailure_setsError() = runTest {
        val vm = RegisterViewModel(authRepository)
        coEvery {
            authRepository.loginWithGoogle("bad-token")
        } throws RuntimeException("network timeout")

        vm.onGoogleAuthResult(GoogleAuthResult.Success("bad-token"))
        advanceUntilIdle()

        assertFalse(vm.registerSuccess.value)
        assertEquals("Google Sign-In failed: network timeout", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
    }

    @Test
    fun onGoogleAuthResult_notConfigured_emitsConfigError() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.NotConfigured)
            assertTrue(awaitItem().contains("not configured"))
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_noAccounts_emitsAccountMessage() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.NoAccounts("Add a Google account."))
            assertEquals("Add a Google account.", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_playServicesUnavailable_emitsPlayServicesMessage() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.PlayServicesUnavailable)
            assertTrue(awaitItem().contains("Google Play services"))
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_failed_emitsFailureMessage() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.Failed("boom"))
            assertEquals("Google Sign-In failed: boom", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_cancelled_clearsLoading_andEmitsNoEvent() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.onGoogleAuthStart()
        assertTrue(vm.uiState.value.isLoading)

        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.Cancelled)
            expectNoEvents()
        }
        assertFalse(vm.uiState.value.isLoading)
    }

    @Test
    fun onGoogleAuthResult_sameErrorTwice_emitsTwoEvents() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.NotConfigured)
            val first = awaitItem()
            vm.onGoogleAuthResult(GoogleAuthResult.NotConfigured)
            val second = awaitItem()
            assertEquals(first, second)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthStart_setsLoading_andClearsError() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.onGoogleAuthResult(GoogleAuthResult.NotConfigured)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.error != null)

        vm.onGoogleAuthStart()

        assertTrue(vm.uiState.value.isLoading)
        assertNull(vm.uiState.value.error)
    }

    // ---- update* helpers clear the pending error ----------------------------

    @Test
    fun updateEmail_clearsPendingError() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.register()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updateEmail("alice@example.com")

        assertNull(vm.uiState.value.error)
        assertEquals("alice@example.com", vm.uiState.value.email)
    }

    @Test
    fun updatePassword_clearsPendingError() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.register()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updatePassword("secret")

        assertNull(vm.uiState.value.error)
        assertEquals("secret", vm.uiState.value.password)
    }

    @Test
    fun updateConfirmPassword_clearsPendingError() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.register()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updateConfirmPassword("secret")

        assertNull(vm.uiState.value.error)
        assertEquals("secret", vm.uiState.value.confirmPassword)
    }

}

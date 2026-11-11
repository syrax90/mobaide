package com.mobaide.app.ui.register

import app.cash.turbine.test
import com.mobaide.app.data.auth.GoogleAuthResult
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.mobaide.app.data.repository.AdminLoginNotSupportedException
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.AuthResult
import com.mobaide.app.data.repository.PASSWORD_TOO_SHORT_MESSAGE
import com.mobaide.app.testing.MainDispatcherRule
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
        updatePassword("secret-pass")
        updateConfirmPassword("secret-pass")
    }

    // ---- Blank-field validation ---------------------------------------------

    @Test
    fun register_whenEmailBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("   ")
        vm.updatePassword("secret-pass")
        vm.updateConfirmPassword("secret-pass")

        vm.register()
        advanceUntilIdle()

        assertEquals("Email and password are required", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.register(any(), any()) }
    }

    // ---- Password mismatch validation ---------------------------------------

    @Test
    fun register_whenPasswordsDoNotMatch_setsError_andDoesNotCallRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("alice@example.com")
        vm.updatePassword("secret-pass")
        vm.updateConfirmPassword("different")

        vm.register()
        advanceUntilIdle()

        assertEquals("Passwords do not match", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.register(any(), any()) }
    }

    // ---- Password length validation -----------------------------------------

    @Test
    fun register_whenPasswordShorterThanEight_setsError_andDoesNotCallRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("alice@example.com")
        vm.updatePassword("1234567")
        vm.updateConfirmPassword("1234567")

        vm.register()
        advanceUntilIdle()

        assertEquals("Password must be at least 8 characters", vm.uiState.value.error)
        assertEquals(PASSWORD_TOO_SHORT_MESSAGE, vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
        coVerify(exactly = 0) { authRepository.register(any(), any()) }
    }

    @Test
    fun register_whenPasswordExactlyEight_callsRepository() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("alice@example.com")
        vm.updatePassword("12345678")
        vm.updateConfirmPassword("12345678")
        coEvery { authRepository.register("alice@example.com", "12345678") } returns
            AuthResult.Success("alice@example.com")

        vm.register()
        advanceUntilIdle()

        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) { authRepository.register("alice@example.com", "12345678") }
    }

    // ---- Loading -> success path --------------------------------------------

    @Test
    fun register_happyPath_setsLoadingTrue_thenEmitsRegisterSuccess() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        val gate = CompletableDeferred<Unit>()
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } coAnswers {
            gate.await()
            AuthResult.Success("alice@example.com")
        }

        assertFalse(vm.uiState.value.isLoading)

        vm.navEvents.test {
            vm.register()
            runCurrent()
            val loading = vm.uiState.value
            assertTrue("must enter loading state before repository resolves", loading.isLoading)
            assertNull(loading.error)

            gate.complete(Unit)
            advanceUntilIdle()

            val event = awaitItem()
            assertTrue(event is RegisterNavEvent.Success)
            assertEquals("alice@example.com", (event as RegisterNavEvent.Success).email)
            cancelAndIgnoreRemainingEvents()
        }
        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) {
            authRepository.register("alice@example.com", "secret-pass")
        }
    }

    @Test
    fun register_happyPath_trimsEmail() = runTest {
        val vm = RegisterViewModel(authRepository)
        vm.updateEmail("  alice@example.com  ")
        vm.updatePassword("secret-pass")
        vm.updateConfirmPassword("secret-pass")
        coEvery { authRepository.register("alice@example.com", "secret-pass") } returns
            AuthResult.Success("alice@example.com")

        vm.navEvents.test {
            vm.register()
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is RegisterNavEvent.Success)
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) {
            authRepository.register("alice@example.com", "secret-pass")
        }
    }

    @Test
    fun register_whenVerificationRequired_emitsVerificationRequired_andNoError() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } returns AuthResult.VerificationRequired("alice@example.com")

        vm.navEvents.test {
            vm.register()
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is RegisterNavEvent.VerificationRequired)
            assertEquals(
                "alice@example.com",
                (event as RegisterNavEvent.VerificationRequired).email,
            )
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(vm.uiState.value.isLoading)
        assertNull(vm.uiState.value.error)
    }

    // ---- ApiException(409) -> "already exists" ------------------------------

    @Test
    fun register_on409_setsAlreadyExistsError_andClearsLoading() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } throws ApiException(409, """{"detail":"duplicate email"}""")

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("An account with this email already exists", state.error)
        assertFalse(state.isLoading)
    }

    @Test
    fun register_on422_setsSingleEmailError_andClearsLoading() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } throws ApiException(422, """{"detail":[{"msg":"Value error"}]}""")

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Enter a single valid email address", state.error)
        assertFalse(state.isLoading)
    }

    @Test
    fun register_on429_setsTooManyAttemptsError_andClearsLoading() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } throws ApiException(
            429,
            """{"detail":{"error":"resend_rate_limited","cooldown_seconds_remaining":1800}}""",
        )

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals(
            "Too many sign-up attempts. Wait a few minutes and try again.",
            state.error,
        )
        assertFalse(state.isLoading)
    }

    // ---- Other ApiException -> "Registration failed: ..." -------------------

    @Test
    fun register_onNon409ApiException_setsRegistrationFailedError() = runTest {
        val vm = primed(RegisterViewModel(authRepository))
        val boom = ApiException(500, "server exploded")
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } throws boom

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Registration failed: ${boom.message}", state.error)
        assertFalse(state.isLoading)
    }

    // ---- Google Sign-In callback --------------------------------------------

    @Test
    fun onGoogleAuthResult_success_emitsRegisterSuccess() = runTest {
        val vm = RegisterViewModel(authRepository)
        coEvery { authRepository.loginWithGoogle("google-token") } returns
            AuthResult.Success("google-user@gmail.com")

        vm.navEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.Success("google-token"))
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is RegisterNavEvent.Success)
            cancelAndIgnoreRemainingEvents()
        }
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

        assertTrue(vm.uiState.value.error!!.contains("Google Sign-In failed"))
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

    // ---- Admin-login mapping ------------------------------------------------

    @Test
    fun register_onAdminLoginNotSupported_setsFriendlySnackbarMessage_andClearsLoading() = runTest {
        // Defensive: the server always returns role=viewer on register today,
        // but if that ever changes the ViewModel must show the friendly
        // message (NOT the generic "Registration failed: ...").
        val vm = primed(RegisterViewModel(authRepository))
        coEvery {
            authRepository.register("alice@example.com", "secret-pass")
        } throws AdminLoginNotSupportedException()

        vm.register()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals(
            ADMIN_LOGIN_USER_MESSAGE,
            state.error,
        )
        assertFalse(state.isLoading)
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
}

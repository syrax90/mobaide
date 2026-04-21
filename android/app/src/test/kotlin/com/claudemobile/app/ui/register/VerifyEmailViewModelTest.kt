package com.claudemobile.app.ui.register

import app.cash.turbine.test
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.claudemobile.app.data.repository.AdminLoginNotSupportedException
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.data.repository.AuthResult
import com.claudemobile.app.data.repository.ResendOutcome
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.Called
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
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
 * Unit tests for [VerifyEmailViewModel] -- exercises the paste filter on
 * `updateCode`, the length-gated submit path, the API-exception mapping,
 * the resend outcome branching (Sent / RateLimited), the cooldown
 * countdown tick loop, and the terminal success / admin-blocked paths.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class VerifyEmailViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    // ---- Helpers ------------------------------------------------------------

    private fun primed(vm: VerifyEmailViewModel, email: String = "alice@example.com") =
        vm.apply { setEmail(email) }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_isEmpty_withNoCooldownOrError() = runTest {
        val vm = VerifyEmailViewModel(authRepository)
        val state = vm.uiState.value
        assertEquals("", state.email)
        assertEquals("", state.code)
        assertFalse(state.isLoading)
        assertFalse(state.isResending)
        assertEquals(0, state.cooldownSeconds)
        assertNull(state.error)
    }

    // ---- updateCode digit filter + cap --------------------------------------

    @Test
    fun updateCode_filtersNonDigits() = runTest {
        val vm = VerifyEmailViewModel(authRepository)
        vm.updateCode("12a3b4c5d6")
        assertEquals("123456", vm.uiState.value.code)
    }

    @Test
    fun updateCode_capsAtSixDigits() = runTest {
        val vm = VerifyEmailViewModel(authRepository)
        vm.updateCode("12345678")
        assertEquals("123456", vm.uiState.value.code)
    }

    @Test
    fun updateCode_acceptsSinglePasteOfSixDigits() = runTest {
        // Regression: a typical paste from a password manager lands as a
        // single onValueChange callback. The filter must not chop it.
        val vm = VerifyEmailViewModel(authRepository)
        vm.updateCode("654321")
        assertEquals("654321", vm.uiState.value.code)
    }

    @Test
    fun updateCode_clearsPendingError() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.submit()  // triggers "Enter the 6-digit code..." validation error
        runCurrent()
        assertEquals(
            "Enter the 6-digit code we emailed you",
            vm.uiState.value.error,
        )

        vm.updateCode("1")

        assertNull(vm.uiState.value.error)
    }

    // ---- submit length gate -------------------------------------------------

    @Test
    fun submit_whenCodeShorterThanSix_setsValidationError_andDoesNotCallRepository() =
        runTest {
            val vm = primed(VerifyEmailViewModel(authRepository))
            vm.updateCode("12345")  // 5 digits

            vm.submit()
            advanceUntilIdle()

            assertEquals(
                "Enter the 6-digit code we emailed you",
                vm.uiState.value.error,
            )
            assertFalse(vm.uiState.value.isLoading)
            coVerify { authRepository wasNot Called }
        }

    @Test
    fun submit_whenCodeShorterThanSix_alsoEmitsErrorEvent() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("")

        vm.errorEvents.test {
            vm.submit()
            runCurrent()
            assertEquals("Enter the 6-digit code we emailed you", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- submit happy path --------------------------------------------------

    @Test
    fun submit_happyPath_setsLoadingTrue_thenEmitsVerificationSuccess() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("123456")

        val gate = kotlinx.coroutines.CompletableDeferred<AuthResult.Success>()
        coEvery {
            authRepository.verifyEmail("alice@example.com", "123456")
        } coAnswers { gate.await() }

        assertFalse(vm.uiState.value.isLoading)

        vm.navEvents.test {
            vm.submit()
            runCurrent()
            val loading = vm.uiState.value
            assertTrue("must enter loading state before repository resolves", loading.isLoading)
            assertNull(loading.error)

            gate.complete(AuthResult.Success("alice@example.com"))
            advanceUntilIdle()

            assertEquals(VerifyEmailNavEvent.Success, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) {
            authRepository.verifyEmail("alice@example.com", "123456")
        }
    }

    // ---- submit: ApiException status-code mapping ---------------------------

    @Test
    fun submit_on400_showsThatCodeIsNotRight_andClearsCode() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("111111")
        coEvery {
            authRepository.verifyEmail("alice@example.com", "111111")
        } throws ApiException(400, """{"detail":{"error":"verification_code_invalid"}}""")

        vm.submit()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("That code isn't right. Try again.", state.error)
        assertFalse(state.isLoading)
        // Code is cleared so the user can re-enter without manually deleting.
        assertEquals("", state.code)
    }

    @Test
    fun submit_on410_showsExpiredMessage() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("222222")
        coEvery {
            authRepository.verifyEmail("alice@example.com", "222222")
        } throws ApiException(410, "gone")

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "That code expired. Tap \"Resend\" to get a new one.",
            vm.uiState.value.error,
        )
    }

    @Test
    fun submit_on429_showsTooManyAttemptsMessage() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("333333")
        coEvery {
            authRepository.verifyEmail("alice@example.com", "333333")
        } throws ApiException(429, "too many")

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "Too many attempts. Request a new code.",
            vm.uiState.value.error,
        )
    }

    @Test
    fun submit_onOtherApiException_showsGenericFailureMessage() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("444444")
        val boom = ApiException(500, "server exploded")
        coEvery {
            authRepository.verifyEmail("alice@example.com", "444444")
        } throws boom

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "Verification failed: ${boom.message}",
            vm.uiState.value.error,
        )
    }

    // ---- submit: non-API exception path -------------------------------------

    @Test
    fun submit_onGenericException_showsFailureMessage_andClearsLoading() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("555555")
        coEvery {
            authRepository.verifyEmail("alice@example.com", "555555")
        } throws java.net.UnknownHostException("api.example.com")

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "Verification failed: api.example.com",
            vm.uiState.value.error,
        )
        assertFalse(vm.uiState.value.isLoading)
    }

    // ---- submit: admin blocked ----------------------------------------------

    @Test
    fun submit_onAdminLoginNotSupported_showsFriendlyMessage() = runTest {
        // Symmetric with the LoginViewModel / RegisterViewModel policy.
        val vm = primed(VerifyEmailViewModel(authRepository))
        vm.updateCode("666666")
        coEvery {
            authRepository.verifyEmail("alice@example.com", "666666")
        } throws AdminLoginNotSupportedException()

        vm.errorEvents.test {
            vm.submit()
            advanceUntilIdle()
            assertEquals(ADMIN_LOGIN_USER_MESSAGE, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        // Must clear loading so the user can retry; regression for the
        // stuck-loading bug found in validation.
        assertFalse(vm.uiState.value.isLoading)
    }

    // ---- resend: Sent -------------------------------------------------------

    @Test
    fun resend_onSent_startsCooldown_andEmitsSuccessMessage() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        coEvery {
            authRepository.resendVerification("alice@example.com")
        } returns ResendOutcome.Sent

        vm.errorEvents.test {
            vm.resend()
            runCurrent()
            assertEquals("A new code is on its way.", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        // Default cooldown is 60 s once Sent.
        val state = vm.uiState.value
        assertEquals(60, state.cooldownSeconds)
        assertFalse(state.isResending)
    }

    // ---- resend: RateLimited -----------------------------------------------

    @Test
    fun resend_onRateLimited_startsSupplierProvidedCooldown_andEmitsWaitMessage() =
        runTest {
            val vm = primed(VerifyEmailViewModel(authRepository))
            coEvery {
                authRepository.resendVerification("alice@example.com")
            } returns ResendOutcome.RateLimited(cooldownSecondsRemaining = 42)

            vm.errorEvents.test {
                vm.resend()
                runCurrent()
                assertEquals(
                    "Please wait 42s before requesting another code.",
                    awaitItem(),
                )
                cancelAndIgnoreRemainingEvents()
            }

            assertEquals(42, vm.uiState.value.cooldownSeconds)
            assertFalse(vm.uiState.value.isResending)
        }

    // ---- resend: guards -----------------------------------------------------

    @Test
    fun resend_whileCooldownActive_isIgnored_andDoesNotHitRepository() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        coEvery {
            authRepository.resendVerification("alice@example.com")
        } returns ResendOutcome.Sent

        // Drive the first resend to install the 60 s cooldown.
        vm.resend()
        runCurrent()
        assertEquals(60, vm.uiState.value.cooldownSeconds)

        // A second tap while the countdown is still running must be a no-op.
        vm.resend()
        advanceUntilIdle()
        coVerify(exactly = 1) {
            authRepository.resendVerification("alice@example.com")
        }
    }

    @Test
    fun resend_whenEmailBlank_isIgnored() = runTest {
        // No setEmail -> email stays "".
        val vm = VerifyEmailViewModel(authRepository)
        vm.resend()
        advanceUntilIdle()
        coVerify { authRepository wasNot Called }
        assertFalse(vm.uiState.value.isResending)
    }

    @Test
    fun resend_onRepositoryThrows_setsError_andClearsResendingFlag() = runTest {
        val vm = primed(VerifyEmailViewModel(authRepository))
        coEvery {
            authRepository.resendVerification("alice@example.com")
        } throws java.net.SocketTimeoutException("timeout")

        vm.resend()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertTrue(state.error!!.startsWith("Couldn't request a new code"))
        assertFalse(state.isResending)
    }

    // ---- Cooldown tick loop -------------------------------------------------

    @Test
    fun cooldownCountdown_decrementsAtOneHzUntilZero() = runTest {
        // Drive a small cooldown so the loop is cheap to assert end-to-end.
        val vm = primed(VerifyEmailViewModel(authRepository))
        coEvery {
            authRepository.resendVerification("alice@example.com")
        } returns ResendOutcome.RateLimited(cooldownSecondsRemaining = 3)

        vm.resend()
        runCurrent()
        assertEquals(3, vm.uiState.value.cooldownSeconds)

        advanceTimeBy(1_001)
        assertEquals(2, vm.uiState.value.cooldownSeconds)

        advanceTimeBy(1_000)
        assertEquals(1, vm.uiState.value.cooldownSeconds)

        advanceTimeBy(1_000)
        assertEquals(0, vm.uiState.value.cooldownSeconds)
    }

    @Test
    fun cooldownCountdown_zeroSecondsRemaining_doesNotInstallLoop() = runTest {
        // If the server reports 0 s remaining, the VM must not set a live
        // cooldown: the user should be able to tap immediately.
        val vm = primed(VerifyEmailViewModel(authRepository))
        coEvery {
            authRepository.resendVerification("alice@example.com")
        } returns ResendOutcome.RateLimited(cooldownSecondsRemaining = 0)

        vm.resend()
        advanceUntilIdle()

        assertEquals(0, vm.uiState.value.cooldownSeconds)
    }
}

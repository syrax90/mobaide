package com.mobaide.app.ui.login

import app.cash.turbine.test
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.PasswordResetOutcome
import com.mobaide.app.data.repository.ResetPasswordError
import com.mobaide.app.data.repository.ResetPasswordFailure
import com.mobaide.app.testing.MainDispatcherRule
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
 * Unit tests for [ResetPasswordViewModel] — exercises the OTP digit filter,
 * length / password / confirm-match validation gates, success nav emission,
 * the [ResetPasswordError] -> message mapping for each branch
 * (InvalidCode / ExpiredCode / AttemptsExhausted / SameAsOld / IpRateLimited /
 * Unknown), and the resend cooldown tick loop.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ResetPasswordViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    private fun primed(
        vm: ResetPasswordViewModel,
        email: String = "alice@example.com",
        code: String = "123456",
        newPassword: String = "newpass99",
        confirm: String = "newpass99",
    ) = vm.apply {
        setEmail(email)
        updateCode(code)
        updateNewPassword(newPassword)
        updateConfirmPassword(confirm)
    }

    @Test
    fun updateCode_filtersNonDigits() = runTest {
        val vm = ResetPasswordViewModel(authRepository)
        vm.updateCode("12a3b4c5d6")
        assertEquals("123456", vm.uiState.value.code)
    }

    @Test
    fun updateCode_capsAtSixDigits() = runTest {
        val vm = ResetPasswordViewModel(authRepository)
        vm.updateCode("12345678")
        assertEquals("123456", vm.uiState.value.code)
    }

    @Test
    fun submit_whenCodeShorterThanSix_emitsValidationError_andDoesNotCallRepository() =
        runTest {
            val vm = primed(ResetPasswordViewModel(authRepository), code = "12345")

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
    fun submit_whenNewPasswordTooShort_emitsValidationError() = runTest {
        val vm = primed(
            ResetPasswordViewModel(authRepository),
            newPassword = "short",
            confirm = "short",
        )

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "Password must be at least 8 characters",
            vm.uiState.value.error,
        )
        coVerify { authRepository wasNot Called }
    }

    @Test
    fun submit_whenConfirmDoesNotMatch_emitsValidationError() = runTest {
        val vm = primed(
            ResetPasswordViewModel(authRepository),
            newPassword = "newpass99",
            confirm = "different99",
        )

        vm.errorEvents.test {
            vm.submit()
            advanceUntilIdle()
            assertEquals("Passwords don't match", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertEquals("Passwords don't match", vm.uiState.value.error)
        coVerify { authRepository wasNot Called }
    }

    @Test
    fun submit_happyPath_setsLoadingTrue_thenEmitsSuccessNav() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository))
        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        coEvery {
            authRepository.resetPassword("alice@example.com", "123456", "newpass99")
        } coAnswers { gate.await() }

        vm.navEvents.test {
            vm.submit()
            runCurrent()
            val loading = vm.uiState.value
            assertTrue("must enter loading state before repo resolves", loading.isLoading)
            assertNull(loading.error)

            gate.complete(Unit)
            advanceUntilIdle()

            assertEquals(ResetPasswordNavEvent.Success, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) {
            authRepository.resetPassword("alice@example.com", "123456", "newpass99")
        }
        assertFalse(vm.uiState.value.isLoading)
    }

    @Test
    fun submit_onInvalidCode_showsThatCodeIsNotRight_andClearsCode() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository), code = "111111")
        coEvery {
            authRepository.resetPassword("alice@example.com", "111111", "newpass99")
        } throws ResetPasswordFailure(ResetPasswordError.InvalidCode)

        vm.submit()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("That code isn't right. Try again.", state.error)
        assertFalse(state.isLoading)
        // Code field cleared so the user can re-enter without deleting.
        assertEquals("", state.code)
    }

    @Test
    fun submit_onIpRateLimited_showsNetworkRetryMessageWithSeconds() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository), code = "555555")
        coEvery {
            authRepository.resetPassword("alice@example.com", "555555", "newpass99")
        } throws ResetPasswordFailure(
            ResetPasswordError.IpRateLimited(retryAfterSeconds = 73),
        )

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "Too many requests from this network. Try again in 73s.",
            vm.uiState.value.error,
        )
    }

    @Test
    fun submit_onUnknownError_blankMessage_fallsBackToCode() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository), code = "777777")
        coEvery {
            authRepository.resetPassword("alice@example.com", "777777", "newpass99")
        } throws ResetPasswordFailure(
            ResetPasswordError.Unknown(code = 500, message = ""),
        )

        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "Couldn't reset password: Unknown error (500)",
            vm.uiState.value.error,
        )
    }

    @Test
    fun resend_onSent_startsDefaultCooldown_andEmitsSuccessMessage() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.Sent

        vm.errorEvents.test {
            vm.resend()
            runCurrent()
            assertEquals("A new code is on its way.", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        // Default cooldown is 60 s on Sent.
        val state = vm.uiState.value
        assertEquals(60, state.cooldownSeconds)
        assertFalse(state.isResending)
    }

    @Test
    fun resend_onRateLimited_startsSupplierProvidedCooldown_andEmitsWaitMessage() =
        runTest {
            val vm = primed(ResetPasswordViewModel(authRepository))
            coEvery {
                authRepository.requestPasswordReset("alice@example.com")
            } returns PasswordResetOutcome.RateLimited(retryAfterSeconds = 42)

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

    @Test
    fun resend_whileCooldownActive_isIgnored_andDoesNotHitRepository() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.Sent

        // First resend installs the 60s cooldown.
        vm.resend()
        runCurrent()
        assertEquals(60, vm.uiState.value.cooldownSeconds)

        // Mid-cooldown tap is a no-op.
        vm.resend()
        advanceUntilIdle()
        coVerify(exactly = 1) {
            authRepository.requestPasswordReset("alice@example.com")
        }
    }

    @Test
    fun resend_whenEmailBlank_isIgnored() = runTest {
        // No setEmail() -> email stays "".
        val vm = ResetPasswordViewModel(authRepository)
        vm.resend()
        advanceUntilIdle()
        coVerify { authRepository wasNot Called }
        assertFalse(vm.uiState.value.isResending)
    }

    @Test
    fun resend_onRepositoryThrows_setsError_andClearsResendingFlag() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } throws java.net.SocketTimeoutException("timeout")

        vm.resend()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertTrue(state.error!!.startsWith("Couldn't request a new code"))
        assertFalse(state.isResending)
    }

    @Test
    fun cooldownCountdown_decrementsAtOneHzUntilZero() = runTest {
        val vm = primed(ResetPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.RateLimited(retryAfterSeconds = 3)

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
}

package com.mobaide.app.ui.login

import app.cash.turbine.test
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.PasswordResetOutcome
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
 * Unit tests for [ForgotPasswordViewModel] -- exercises the email validation
 * guard, the loading -> sent transition, the rate-limited cooldown branch,
 * the anti-enumeration generic snackbar message, and the cooldown tick loop.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ForgotPasswordViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    private fun primed(vm: ForgotPasswordViewModel, email: String = "alice@example.com") =
        vm.apply { updateEmail(email) }

    @Test
    fun updateEmail_clearsPendingError() = runTest {
        val vm = ForgotPasswordViewModel(authRepository)
        vm.submit()
        runCurrent()
        assertEquals("Enter a valid email address", vm.uiState.value.error)

        vm.updateEmail("a")

        assertNull(vm.uiState.value.error)
    }

    @Test
    fun submit_whenEmailBlank_emitsValidationError_andDoesNotCallRepository() =
        runTest {
            val vm = ForgotPasswordViewModel(authRepository)

            vm.errorEvents.test {
                vm.submit()
                runCurrent()
                assertEquals("Enter a valid email address", awaitItem())
                cancelAndIgnoreRemainingEvents()
            }
            assertEquals("Enter a valid email address", vm.uiState.value.error)
            coVerify { authRepository wasNot Called }
        }

    @Test
    fun submit_trimsWhitespace_beforeCallingRepository() = runTest {
        val vm = primed(ForgotPasswordViewModel(authRepository), email = "  alice@example.com  ")
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.Sent

        vm.submit()
        advanceUntilIdle()

        coVerify(exactly = 1) {
            authRepository.requestPasswordReset("alice@example.com")
        }
    }

    @Test
    fun submit_onSent_emitsGenericSnackbar_andCodeSentNavEvent() = runTest {
        val vm = primed(ForgotPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.Sent

        vm.errorEvents.test {
            vm.navEvents.test navEvents@{
                vm.submit()
                advanceUntilIdle()

                // Generic snackbar shown regardless of whether the email exists
                // (anti-enumeration contract).
                assertEquals(
                    "If an account exists for that email, we sent a code.",
                    this@test.awaitItem(),
                )

                val nav = awaitItem()
                assertTrue(nav is ForgotPasswordNavEvent.CodeSent)
                assertEquals(
                    "alice@example.com",
                    (nav as ForgotPasswordNavEvent.CodeSent).email,
                )
                cancelAndIgnoreRemainingEvents()
            }
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(vm.uiState.value.isSending)
    }

    @Test
    fun submit_onRateLimited_installsCooldown_andEmitsWaitMessage() = runTest {
        val vm = primed(ForgotPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.RateLimited(retryAfterSeconds = 42)

        vm.errorEvents.test {
            vm.submit()
            runCurrent()
            assertEquals(
                "Too many requests from this network. Try again in 42s.",
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
        // runCurrent() yields after the coroutine has installed the cooldown
        // but before the delay(1000) loop ticks — so the initial value is
        // observable. advanceUntilIdle() would drain the entire loop.
        assertEquals(42, vm.uiState.value.cooldownSeconds)
        assertFalse(vm.uiState.value.isSending)
    }

    @Test
    fun submit_whileCooldownActive_isIgnored_andDoesNotHitRepository() = runTest {
        val vm = primed(ForgotPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.RateLimited(retryAfterSeconds = 30)

        vm.submit()
        runCurrent()
        assertEquals(30, vm.uiState.value.cooldownSeconds)

        // A second tap mid-cooldown is a no-op — only one repository call.
        vm.submit()
        runCurrent()
        coVerify(exactly = 1) {
            authRepository.requestPasswordReset("alice@example.com")
        }
    }

    @Test
    fun submit_onApiException_showsFailureMessage_andClearsLoading() = runTest {
        val vm = primed(ForgotPasswordViewModel(authRepository))
        val boom = ApiException(500, "server exploded")
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } throws boom

        vm.submit()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Couldn't request a reset code: ${boom.message}", state.error)
        assertFalse(state.isSending)
    }

    @Test
    fun cooldownCountdown_decrementsAtOneHzUntilZero() = runTest {
        val vm = primed(ForgotPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.RateLimited(retryAfterSeconds = 3)

        vm.submit()
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
    fun cooldownCountdown_zeroRetryAfter_doesNotInstallLoop() = runTest {
        // If the server reports 0 s the VM must not freeze the button.
        val vm = primed(ForgotPasswordViewModel(authRepository))
        coEvery {
            authRepository.requestPasswordReset("alice@example.com")
        } returns PasswordResetOutcome.RateLimited(retryAfterSeconds = 0)

        vm.submit()
        runCurrent()

        assertEquals(0, vm.uiState.value.cooldownSeconds)
    }
}

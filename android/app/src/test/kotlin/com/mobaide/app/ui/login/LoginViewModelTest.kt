package com.mobaide.app.ui.login

import app.cash.turbine.test
import com.mobaide.app.data.auth.GoogleAuthResult
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.mobaide.app.data.repository.AdminLoginNotSupportedException
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.AuthResult
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
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
 * loading→success state transition, Google Sign-In callback, the failure
 * branches (`ApiException(401)`, other [ApiException], non-API [Exception]),
 * the `update*` helpers that clear the pending error, and the reactive
 * "session expired" snackbar emitted when [SessionManager.sessionEvents]
 * surfaces [SessionManager.InvalidationReason.TokenRejected].
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val authRepository: AuthRepository = mockk()

    // ---- Helpers ------------------------------------------------------------

    /** Build a [SessionManager] whose [SessionManager.sessionEvents] is
     *  a caller-controllable [MutableSharedFlow]. Tests that don't care
     *  about that flow just ignore it. */
    private fun newSessionManager(
        events: MutableSharedFlow<SessionManager.InvalidationReason> =
            MutableSharedFlow(extraBufferCapacity = 1),
    ): Pair<SessionManager, MutableSharedFlow<SessionManager.InvalidationReason>> {
        val sm = mockk<SessionManager>(relaxUnitFun = true)
        every { sm.sessionEvents } returns events.asSharedFlow()
        return sm to events
    }

    private fun newViewModel(
        sessionEvents: MutableSharedFlow<SessionManager.InvalidationReason> =
            MutableSharedFlow(extraBufferCapacity = 1),
    ): LoginViewModel {
        val (sm, _) = newSessionManager(sessionEvents)
        return LoginViewModel(authRepository, sm)
    }

    private fun primed(vm: LoginViewModel) = vm.apply {
        updateEmail("alice@example.com")
        updatePassword("secret")
    }

    // ---- Initial state ------------------------------------------------------

    @Test
    fun initialState_hasEmptyFields_andNoErrorOrLoading() = runTest {
        val vm = newViewModel()
        val state = vm.uiState.value
        assertEquals("", state.email)
        assertEquals("", state.password)
        assertFalse(state.isLoading)
        assertNull(state.error)
    }

    // ---- Blank-field validation ---------------------------------------------

    @Test
    fun login_whenEmailBlank_setsError_andDoesNotCallRepository() = runTest {
        val vm = newViewModel()
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
        val vm = newViewModel()
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
        val vm = primed(newViewModel())
        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } coAnswers {
            gate.await()
            AuthResult.Success("alice@example.com")
        }

        assertFalse(vm.uiState.value.isLoading)

        vm.navEvents.test {
            vm.loginWithPassword()
            runCurrent()
            val loading = vm.uiState.value
            assertTrue("must enter loading state before repository resolves", loading.isLoading)
            assertNull(loading.error)

            gate.complete(Unit)
            advanceUntilIdle()

            val event = awaitItem()
            assertTrue(event is LoginNavEvent.Success)
            assertEquals("alice@example.com", (event as LoginNavEvent.Success).email)
            cancelAndIgnoreRemainingEvents()
        }
        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) {
            authRepository.loginWithPassword("alice@example.com", "secret")
        }
    }

    // ---- ApiException(401) → "Invalid email or password" --------------------

    @Test
    fun login_on401_setsInvalidCredentialsError_andClearsLoading() = runTest {
        val vm = primed(newViewModel())
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } throws ApiException(401, """{"detail":"bad creds"}""")

        vm.loginWithPassword()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("Invalid email or password", state.error)
        assertFalse(state.isLoading)
    }

    // ---- Other ApiException → "Login failed: ..." --------------------------

    @Test
    fun login_onNon401ApiException_setsLoginFailedError() = runTest {
        val vm = primed(newViewModel())
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
        val vm = primed(newViewModel())
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
    fun onGoogleAuthResult_success_emitsLoginSuccess() = runTest {
        val vm = newViewModel()
        coEvery { authRepository.loginWithGoogle("google-token") } returns
            AuthResult.Success("google-user@gmail.com")

        vm.navEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.Success("google-token"))
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is LoginNavEvent.Success)
            assertEquals(
                "google-user@gmail.com",
                (event as LoginNavEvent.Success).email,
            )
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { authRepository.loginWithGoogle("google-token") }
    }

    @Test
    fun onGoogleAuthResult_success_onFailure_setsError() = runTest {
        val vm = newViewModel()
        coEvery {
            authRepository.loginWithGoogle("bad-token")
        } throws ApiException(401, "invalid token")

        vm.onGoogleAuthResult(GoogleAuthResult.Success("bad-token"))
        advanceUntilIdle()

        assertTrue(vm.uiState.value.error!!.contains("Google Sign-In failed"))
    }

    @Test
    fun onGoogleAuthResult_success_onGenericFailure_setsError_andClearsLoading() = runTest {
        // Exercises the non-ApiException catch branch in onGoogleIdToken — symmetric
        // to the RegisterViewModel test of the same name, and previously uncovered here.
        val vm = newViewModel()
        coEvery {
            authRepository.loginWithGoogle("bad-token")
        } throws java.net.UnknownHostException("api.example.com")

        vm.onGoogleAuthResult(GoogleAuthResult.Success("bad-token"))
        advanceUntilIdle()

        assertEquals("Google Sign-In failed: api.example.com", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
    }

    @Test
    fun onGoogleAuthResult_success_onApiFailure_alsoEmitsErrorEvent() = runTest {
        // Regression: the snackbar-on-repeat-tap fix must also cover the
        // Success→repository-throws branch, not just the pre-repo result cases.
        val vm = newViewModel()
        coEvery {
            authRepository.loginWithGoogle("bad-token")
        } throws ApiException(401, "invalid token")

        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.Success("bad-token"))
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event.contains("Google Sign-In failed"))
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_notConfigured_emitsConfigError() = runTest {
        val vm = newViewModel()
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.NotConfigured)
            val event = awaitItem()
            assertTrue(event.contains("not configured"))
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(vm.uiState.value.isLoading)
    }

    @Test
    fun onGoogleAuthResult_noAccounts_emitsAccountMessage() = runTest {
        val vm = newViewModel()
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.NoAccounts("Add a Google account."))
            assertEquals("Add a Google account.", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_playServicesUnavailable_emitsPlayServicesMessage() = runTest {
        val vm = newViewModel()
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.PlayServicesUnavailable)
            assertTrue(awaitItem().contains("Google Play services"))
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_failed_emitsFailureMessage() = runTest {
        val vm = newViewModel()
        vm.errorEvents.test {
            vm.onGoogleAuthResult(GoogleAuthResult.Failed("boom"))
            assertEquals("Google Sign-In failed: boom", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_cancelled_clearsLoading_andEmitsNoEvent() = runTest {
        val vm = newViewModel()
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
        // Regression: the old LaunchedEffect(uiState.error) swallowed repeat clicks.
        val vm = newViewModel()
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
        val vm = newViewModel()
        vm.onGoogleAuthResult(GoogleAuthResult.NotConfigured)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.error != null)

        vm.onGoogleAuthStart()

        assertTrue(vm.uiState.value.isLoading)
        assertNull(vm.uiState.value.error)
    }

    // ---- Admin-login mapping ------------------------------------------------

    @Test
    fun login_onAdminLoginNotSupported_setsFriendlySnackbarMessage_andClearsLoading() = runTest {
        // The repository throws AdminLoginNotSupportedException before persisting
        // a session; the ViewModel is responsible for translating that to a
        // specific user-facing message (NOT the generic "Login failed: ...").
        val vm = primed(newViewModel())
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } throws AdminLoginNotSupportedException()

        vm.loginWithPassword()
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals(
            ADMIN_LOGIN_USER_MESSAGE,
            state.error,
        )
        assertFalse(state.isLoading)
    }

    @Test
    fun login_onAdminLoginNotSupported_alsoEmitsErrorEvent() = runTest {
        val vm = primed(newViewModel())
        coEvery {
            authRepository.loginWithPassword("alice@example.com", "secret")
        } throws AdminLoginNotSupportedException()

        vm.errorEvents.test {
            vm.loginWithPassword()
            advanceUntilIdle()
            assertEquals(
                ADMIN_LOGIN_USER_MESSAGE,
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onGoogleAuthResult_onAdminLoginNotSupported_setsFriendlySnackbarMessage() = runTest {
        // Symmetric mapping must exist on the Google branch too.
        val vm = newViewModel()
        coEvery {
            authRepository.loginWithGoogle("google-token")
        } throws AdminLoginNotSupportedException()

        vm.onGoogleAuthResult(GoogleAuthResult.Success("google-token"))
        advanceUntilIdle()

        assertEquals(
            ADMIN_LOGIN_USER_MESSAGE,
            vm.uiState.value.error,
        )
        assertFalse(vm.uiState.value.isLoading)
    }

    // ---- update* helpers clear the pending error ----------------------------

    @Test
    fun updateEmail_clearsPendingError() = runTest {
        val vm = newViewModel()
        vm.loginWithPassword()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updateEmail("alice@example.com")

        assertNull(vm.uiState.value.error)
        assertEquals("alice@example.com", vm.uiState.value.email)
    }

    @Test
    fun updatePassword_clearsPendingError() = runTest {
        val vm = newViewModel()
        vm.loginWithPassword()
        advanceUntilIdle()
        assertEquals("Email and password are required", vm.uiState.value.error)

        vm.updatePassword("secret")

        assertNull(vm.uiState.value.error)
        assertEquals("secret", vm.uiState.value.password)
    }

    // ---- Session-expired snackbar (reactive to SessionManager.sessionEvents) --

    @Test
    fun sessionEvents_tokenRejected_emitsSessionExpiredSnackbar() = runTest {
        // The central-API interceptor or user-scoped WebSocket signals
        // "server rejected my dashboard JWT" by emitting TokenRejected
        // on SessionManager.sessionEvents. The login ViewModel must
        // forward that to its errorEvents flow as a user-friendly
        // "Please sign in again" snackbar.
        val events = MutableSharedFlow<SessionManager.InvalidationReason>(
            extraBufferCapacity = 1,
        )
        val vm = newViewModel(events)

        vm.errorEvents.test {
            // Let the viewModelScope.launch in the VM init block start and subscribe
            // to sessionEvents before we emit — without this the emit races the
            // coroutine startup and the event is dropped.
            advanceUntilIdle()
            events.tryEmit(SessionManager.InvalidationReason.TokenRejected)
            val first = awaitItem()
            assertEquals(
                "Your session has expired. Please sign in again.",
                first,
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionEvents_accountRemoved_emitsAccountRemovedSnackbar() = runTest {
        // The user-scoped WebSocket emits AccountRemoved on
        // SessionManager.sessionEvents when the server closes with code 4010
        // (WS_CLOSE_ADMIN_REVOKED) — the admin destructively unregistered
        // the user. The login ViewModel must distinguish this from the
        // generic TokenRejected ("session expired") path by surfacing a
        // dedicated "account was removed" snackbar; if it copy-pasted the
        // session-expired text or no-op'd like Manual, the user would be
        // dropped back to the login screen with no idea their account is
        // gone — they'd just try to sign in again and fail with an
        // unhelpful "Invalid email or password".
        val events = MutableSharedFlow<SessionManager.InvalidationReason>(
            extraBufferCapacity = 1,
        )
        val vm = newViewModel(events)

        vm.errorEvents.test {
            advanceUntilIdle()
            events.tryEmit(SessionManager.InvalidationReason.AccountRemoved)
            assertEquals(
                "Your account was removed by an administrator. " +
                    "Sign up to start again or contact support.",
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionEvents_accountRemoved_isDistinctFromTokenRejected() = runTest {
        // Regression guard against the obvious refactor mistake: branching
        // the AccountRemoved arm onto the TokenRejected message string
        // (or vice-versa) by accident. The two snackbars must remain
        // distinguishable to the user so they understand WHY they were
        // logged out — "session expired" implies "sign in again" but
        // "account removed" requires the user to register a fresh account
        // or contact support.
        val events = MutableSharedFlow<SessionManager.InvalidationReason>(
            extraBufferCapacity = 1,
        )
        val vm = newViewModel(events)

        vm.errorEvents.test {
            advanceUntilIdle()
            events.tryEmit(SessionManager.InvalidationReason.AccountRemoved)
            val accountMessage = awaitItem()

            // Strong-form distinguishability checks: the message must NOT
            // be the TokenRejected copy, and it must explicitly contain
            // the "removed" keyword so a future translator can't silently
            // collapse the two strings.
            assertEquals(false, accountMessage == "Your session has expired. Please sign in again.")
            assertTrue(
                "account-removed message must explicitly mention removal — got: $accountMessage",
                accountMessage.contains("removed", ignoreCase = true),
            )

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionEvents_manualLogout_emitsNoSnackbar() = runTest {
        // Manual logout is user-initiated — the user pressed "Log out"
        // in Settings, they don't need a redundant "session expired"
        // toast for that.
        val events = MutableSharedFlow<SessionManager.InvalidationReason>(
            extraBufferCapacity = 1,
        )
        val vm = newViewModel(events)

        vm.errorEvents.test {
            events.tryEmit(SessionManager.InvalidationReason.Manual)
            expectNoEvents()
        }
    }

    @Test
    fun sessionEvents_bufferedBeforeCollection_isDeliveredOnAttach() = runTest {
        // Cold-start race: the interceptor (or WS) invalidates the
        // session BEFORE LoginViewModel's errorEvents is being collected.
        // The production SessionManager._sessionEvents uses
        // extraBufferCapacity=1 so a tryEmit() that races the init
        // coroutine startup is buffered for the first subscriber.
        // In the test we replicate that by emitting after the VM's init
        // coroutine has subscribed (via advanceUntilIdle) but before the
        // outer test code starts collecting errorEvents.
        val events = MutableSharedFlow<SessionManager.InvalidationReason>(
            extraBufferCapacity = 1,
        )
        val vm = newViewModel(events)
        // Let the viewModelScope.launch in VM init subscribe to sessionEvents.
        advanceUntilIdle()
        // Emit now: the init coroutine is subscribed, so tryEmit is buffered
        // and will be forwarded to errorEvents when the test starts collecting.
        events.tryEmit(SessionManager.InvalidationReason.TokenRejected)

        vm.errorEvents.test {
            assertEquals(
                "Your session has expired. Please sign in again.",
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionEvents_emittedBeforeViewModelExists_stillFiresSnackbar() = runTest {
        // True cold-start: CentralAuthInterceptor fires its 401 handler
        // from the OkHttp IO thread BEFORE any LoginViewModel is
        // constructed (navigation to LOGIN runs reactively after the
        // authState → Unauthenticated transition, which has not happened
        // yet at emit time). The production SessionManager uses
        // `replay = 1` on _sessionEvents specifically so a `TokenRejected`
        // emitted in this window survives until a LoginViewModel arrives
        // and subscribes. This test models that ordering directly —
        // emit first, then construct the VM.
        val events = MutableSharedFlow<SessionManager.InvalidationReason>(
            replay = 1,
            extraBufferCapacity = 0,
        )
        // Emit BEFORE any ViewModel / subscriber exists. With replay=1
        // the event sits in the replay cache until the first collector
        // attaches.
        events.tryEmit(SessionManager.InvalidationReason.TokenRejected)

        val vm = newViewModel(events)

        vm.errorEvents.test {
            advanceUntilIdle()
            assertEquals(
                "Your session has expired. Please sign in again.",
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }
}

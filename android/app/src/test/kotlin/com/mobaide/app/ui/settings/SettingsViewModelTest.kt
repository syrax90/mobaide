package com.mobaide.app.ui.settings

import com.mobaide.app.data.auth.GoogleAuthResult
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.DeleteAccountResult
import com.mobaide.app.data.repository.DeletionMode
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [SettingsViewModel] — the ViewModel reads the user's email
 * synchronously from [AuthRepository] inside its constructor, then exposes an
 * immutable [SettingsUiState] and delegates `logout()` to the repository.
 *
 * "Logged in as" sources from [AuthRepository.getUserEmail] (i.e. the
 * `UserIdentityStore` populated by the central-server login flows) rather
 * than the agent JWT `sub` claim, so the value is the same regardless of
 * whether the user signed in with email/password or Google.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun stubPreferencesStore(
        mode: TerminalDisplayMode = TerminalDisplayMode.Full,
        staticColumns: Int = TERMINAL_STATIC_COLUMNS_DEFAULT,
    ): PreferencesStore = mockk(relaxed = true) {
        every { terminalDisplayMode } returns flowOf(mode)
        every { terminalStaticColumns } returns flowOf(staticColumns)
    }

    @Test
    fun initialState_userEmail_sourcedFromAuthRepository() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns "carol@example.com"

        val vm = SettingsViewModel(authRepository, stubPreferencesStore())

        assertEquals("carol@example.com", vm.uiState.value.userEmail)
    }

    @Test
    fun initialState_userEmailNull_fallsBackToEmptyString() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns null

        val vm = SettingsViewModel(authRepository, stubPreferencesStore())

        assertEquals("", vm.uiState.value.userEmail)
    }

    @Test
    fun logout_delegatesToAuthRepository_withoutMutatingUiState() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns "alice@example.com"
        every { authRepository.userLogout() } just Runs

        val vm = SettingsViewModel(authRepository, stubPreferencesStore())
        val before = vm.uiState.value

        vm.logout()

        verify(exactly = 1) { authRepository.userLogout() }
        assertEquals(before, vm.uiState.value)
    }

    @Test
    fun initialState_loadsTerminalDisplayPreferences() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val prefs = stubPreferencesStore(
            mode = TerminalDisplayMode.Static,
            staticColumns = 100,
        )

        val vm = SettingsViewModel(authRepository, prefs)
        advanceUntilIdle()

        assertEquals(TerminalDisplayMode.Static, vm.uiState.value.terminalDisplayMode)
        assertEquals(100, vm.uiState.value.terminalStaticColumns)
    }

    @Test
    fun setTerminalDisplayMode_writesThroughToPreferencesStore() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val prefs = stubPreferencesStore()
        coEvery { prefs.setTerminalDisplayMode(any()) } just Runs

        val vm = SettingsViewModel(authRepository, prefs)
        vm.setTerminalDisplayMode(TerminalDisplayMode.Static)
        advanceUntilIdle()

        coVerify(exactly = 1) { prefs.setTerminalDisplayMode(TerminalDisplayMode.Static) }
    }

    @Test
    fun setTerminalStaticColumns_clampsToValidRangeBeforeWrite() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true)
        every { authRepository.getUserEmail() } returns "alice@example.com"
        val prefs = stubPreferencesStore()
        coEvery { prefs.setTerminalStaticColumns(any()) } just Runs

        val vm = SettingsViewModel(authRepository, prefs)
        vm.setTerminalStaticColumns(40)
        vm.setTerminalStaticColumns(300)
        vm.setTerminalStaticColumns(150)
        advanceUntilIdle()

        coVerify(exactly = 1) { prefs.setTerminalStaticColumns(60) }
        coVerify(exactly = 1) { prefs.setTerminalStaticColumns(240) }
        coVerify(exactly = 1) { prefs.setTerminalStaticColumns(150) }
    }

    private fun newDeleteVm(
        email: String = "user@example.com",
        authRepository: AuthRepository = mockk(relaxed = true) {
            every { getUserEmail() } returns email
        },
    ): SettingsViewModel = SettingsViewModel(
        authRepository,
        stubPreferencesStore(),
    )

    @Test
    fun startDeleteAccount_transitionsHiddenToExplain() = runTest {
        val vm = newDeleteVm(email = "alice@example.com")
        assertEquals(DeleteAccountDialogState.Hidden, vm.uiState.value.deleteDialog)

        vm.startDeleteAccount()

        val state = vm.uiState.value.deleteDialog
        assertTrue(
            "expected Explain, got $state",
            state is DeleteAccountDialogState.Explain,
        )
        assertEquals("alice@example.com", (state as DeleteAccountDialogState.Explain).email)
    }

    @Test
    fun startDeleteAccount_isNoopWhenUserEmailMissing() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns null
        }
        val vm = newDeleteVm(authRepository = authRepository)

        vm.startDeleteAccount()

        // No email → stays Hidden, no NPE.
        assertEquals(DeleteAccountDialogState.Hidden, vm.uiState.value.deleteDialog)
    }

    @Test
    fun chooseReAuthMethod_googleFalse_transitionsToReAuthPassword() = runTest {
        val vm = newDeleteVm(email = "bob@example.com")
        vm.startDeleteAccount()

        vm.chooseReAuthMethod(useGoogle = false)

        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ReAuthPassword)
        assertEquals("bob@example.com", (state as DeleteAccountDialogState.ReAuthPassword).email)
        assertEquals("", state.password)
        assertNull(state.errorMessage)
    }

    @Test
    fun chooseReAuthMethod_googleTrue_transitionsToReAuthGoogle() = runTest {
        val vm = newDeleteVm(email = "carol@example.com")
        vm.startDeleteAccount()

        vm.chooseReAuthMethod(useGoogle = true)

        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ReAuthGoogle)
        assertEquals("carol@example.com", (state as DeleteAccountDialogState.ReAuthGoogle).email)
        assertFalse(state.submitting)
        assertNull(state.errorMessage)
    }

    @Test
    fun onPasswordChange_updatesPasswordOnReAuthPassword() = runTest {
        val vm = newDeleteVm()
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)

        vm.onPasswordChange("hunter22")

        val state = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ReAuthPassword
        assertEquals("hunter22", state.password)
    }

    @Test
    fun confirmPasswordReauth_blockedWhenPasswordBlank() = runTest {
        val vm = newDeleteVm()
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)

        vm.confirmPasswordReauth()

        // Stays on ReAuthPassword with an inline error.
        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ReAuthPassword)
        assertNotNull((state as DeleteAccountDialogState.ReAuthPassword).errorMessage)
    }

    @Test
    fun confirmPasswordReauth_advancesToConfirmEmailWithPassword() = runTest {
        val vm = newDeleteVm(email = "eve@example.com")
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("secret")

        vm.confirmPasswordReauth()

        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ConfirmEmail)
        val confirm = state as DeleteAccountDialogState.ConfirmEmail
        assertEquals("eve@example.com", confirm.email)
        assertEquals("secret", confirm.password)
        // Google fields must remain null on the password path.
        assertNull(confirm.idToken)
        assertNull(confirm.nonce)
    }

    @Test
    fun onGoogleAuthResult_success_transitionsToConfirmEmailWithToken() = runTest {
        val vm = newDeleteVm(email = "frank@example.com")
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = true)

        vm.onGoogleAuthResult(
            GoogleAuthResult.Success(idToken = "id-tok-123", nonce = "n-abc"),
        )

        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ConfirmEmail)
        val confirm = state as DeleteAccountDialogState.ConfirmEmail
        assertEquals("id-tok-123", confirm.idToken)
        assertEquals("n-abc", confirm.nonce)
        assertNull(confirm.password)
    }

    @Test
    fun onGoogleAuthResult_cancelled_surfacesErrorOnReAuthGoogle() = runTest {
        val vm = newDeleteVm()
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = true)

        vm.onGoogleAuthResult(GoogleAuthResult.Cancelled)

        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ReAuthGoogle)
        val gs = state as DeleteAccountDialogState.ReAuthGoogle
        assertFalse(gs.submitting)
        assertNotNull(gs.errorMessage)
    }

    @Test
    fun onTypedEmailChange_updatesTypedEmailOnConfirmEmail() = runTest {
        val vm = newDeleteVm(email = "grace@example.com")
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("x")
        vm.confirmPasswordReauth()

        vm.onTypedEmailChange("partial@")

        val state = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ConfirmEmail
        assertEquals("partial@", state.typedEmail)
    }

    @Test
    fun submitDelete_blockedWhenTypedEmailMismatch() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "henry@example.com"
        }
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("x")
        vm.confirmPasswordReauth()
        vm.onTypedEmailChange("wrong@example.com")

        var scheduledCallbackInvoked = false
        vm.submitDelete { scheduledCallbackInvoked = true }
        advanceUntilIdle()

        // The repository delete must NOT have been called.
        coVerify(exactly = 0) { authRepository.deleteAccountWithPassword(any(), any()) }
        coVerify(exactly = 0) { authRepository.deleteAccountWithGoogle(any(), any(), any()) }
        assertFalse(scheduledCallbackInvoked)
        // Error surfaced inline.
        val state = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ConfirmEmail
        assertNotNull(state.errorMessage)
    }

    @Test
    fun submitDelete_passwordPath_success_invokesOnScheduledAndHidesDialog() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "ida@example.com"
        }
        coEvery { authRepository.deleteAccountWithPassword("pw", DeletionMode.Scheduled) } returns
            DeleteAccountResult.Submitted(DeletionMode.Scheduled)
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("pw")
        vm.confirmPasswordReauth()
        vm.onTypedEmailChange("ida@example.com")

        var scheduledCallbackInvoked = false
        vm.submitDelete { scheduledCallbackInvoked = true }
        advanceUntilIdle()

        assertTrue(scheduledCallbackInvoked)
        assertEquals(DeleteAccountDialogState.Hidden, vm.uiState.value.deleteDialog)
        coVerify(exactly = 1) { authRepository.deleteAccountWithPassword("pw", DeletionMode.Scheduled) }
    }

    @Test
    fun submitDelete_googlePath_threadsIdTokenAndNonceIntoRepository() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "jane@example.com"
        }
        coEvery {
            authRepository.deleteAccountWithGoogle("id-tok", "nonce-1", DeletionMode.Scheduled)
        } returns DeleteAccountResult.Submitted(DeletionMode.Scheduled)
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = true)
        vm.onGoogleAuthResult(GoogleAuthResult.Success("id-tok", "nonce-1"))
        vm.onTypedEmailChange("jane@example.com")

        var scheduledCallbackInvoked = false
        vm.submitDelete { scheduledCallbackInvoked = true }
        advanceUntilIdle()

        assertTrue(scheduledCallbackInvoked)
        coVerify(exactly = 1) {
            authRepository.deleteAccountWithGoogle("id-tok", "nonce-1", DeletionMode.Scheduled)
        }
        // Password path is not exercised in the Google branch.
        coVerify(exactly = 0) { authRepository.deleteAccountWithPassword(any(), any()) }
    }

    @Test
    fun submitDelete_reauthFailed_surfacesServerMessageAndKeepsConfirmEmail() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "kate@example.com"
        }
        coEvery { authRepository.deleteAccountWithPassword("pw", DeletionMode.Scheduled) } returns
            DeleteAccountResult.ReauthFailed("Incorrect password")
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("pw")
        vm.confirmPasswordReauth()
        vm.onTypedEmailChange("kate@example.com")

        var scheduledCallbackInvoked = false
        vm.submitDelete { scheduledCallbackInvoked = true }
        advanceUntilIdle()

        assertFalse(scheduledCallbackInvoked)
        val state = vm.uiState.value.deleteDialog
        assertTrue(state is DeleteAccountDialogState.ConfirmEmail)
        val confirm = state as DeleteAccountDialogState.ConfirmEmail
        assertFalse(confirm.submitting)
        assertEquals("Incorrect password", confirm.errorMessage)
    }

    @Test
    fun submitDelete_forbidden_surfacesFriendlySeedAdminError() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "root@example.com"
        }
        coEvery { authRepository.deleteAccountWithPassword(any(), any()) } returns
            DeleteAccountResult.Forbidden
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("pw")
        vm.confirmPasswordReauth()
        vm.onTypedEmailChange("root@example.com")

        var scheduledCallbackInvoked = false
        vm.submitDelete { scheduledCallbackInvoked = true }
        advanceUntilIdle()

        assertFalse(scheduledCallbackInvoked)
        val state = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ConfirmEmail
        assertNotNull(state.errorMessage)
        // The message should mention the policy reason so the user can
        // tell they're not just hitting a bad-password case.
        assertTrue(
            "expected message to mention seed-admin policy, got ${state.errorMessage}",
            state.errorMessage!!.contains("seed", ignoreCase = true)
                || state.errorMessage!!.contains("admin", ignoreCase = true)
                || state.errorMessage!!.contains("cannot", ignoreCase = true),
        )
    }

    @Test
    fun dismissDeleteDialog_returnsToHiddenFromAnyState() = runTest {
        val vm = newDeleteVm()
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("x")
        vm.confirmPasswordReauth()
        assertTrue(vm.uiState.value.deleteDialog is DeleteAccountDialogState.ConfirmEmail)

        vm.dismissDeleteDialog()

        assertEquals(DeleteAccountDialogState.Hidden, vm.uiState.value.deleteDialog)
    }

    @Test
    fun startDeleteAccount_defaultsToScheduledMode() = runTest {
        val vm = newDeleteVm(email = "alice@example.com")
        vm.startDeleteAccount()

        val state = vm.uiState.value.deleteDialog as DeleteAccountDialogState.Explain
        // Default mode on Explain step is Scheduled (recommended path).
        assertEquals(DeletionMode.Scheduled, state.mode)
    }

    @Test
    fun setDeletionMode_immediate_updatesExplainState() = runTest {
        val vm = newDeleteVm()
        vm.startDeleteAccount()

        vm.setDeletionMode(DeletionMode.Immediate)

        val state = vm.uiState.value.deleteDialog as DeleteAccountDialogState.Explain
        assertEquals(DeletionMode.Immediate, state.mode)
    }

    @Test
    fun setDeletionMode_noopWhenNotOnExplainState() = runTest {
        val vm = newDeleteVm()
        vm.startDeleteAccount()
        vm.chooseReAuthMethod(useGoogle = false)
        // Now on ReAuthPassword — setDeletionMode should not mutate state.
        val before = vm.uiState.value.deleteDialog

        vm.setDeletionMode(DeletionMode.Immediate)

        assertEquals(before, vm.uiState.value.deleteDialog)
    }

    @Test
    fun mode_threadsThroughReAuthPassword_andConfirmEmail() = runTest {
        val vm = newDeleteVm(email = "bob@example.com")
        vm.startDeleteAccount()
        vm.setDeletionMode(DeletionMode.Immediate)
        vm.chooseReAuthMethod(useGoogle = false)

        val reAuth = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ReAuthPassword
        assertEquals(DeletionMode.Immediate, reAuth.mode)

        vm.onPasswordChange("pw")
        vm.confirmPasswordReauth()

        val confirm = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ConfirmEmail
        assertEquals(DeletionMode.Immediate, confirm.mode)
    }

    @Test
    fun mode_threadsThroughReAuthGoogle_andConfirmEmail() = runTest {
        val vm = newDeleteVm(email = "carol@example.com")
        vm.startDeleteAccount()
        vm.setDeletionMode(DeletionMode.Immediate)
        vm.chooseReAuthMethod(useGoogle = true)

        val google = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ReAuthGoogle
        assertEquals(DeletionMode.Immediate, google.mode)

        vm.onGoogleAuthResult(GoogleAuthResult.Success("id-tok", "nonce-z"))

        val confirm = vm.uiState.value.deleteDialog as DeleteAccountDialogState.ConfirmEmail
        assertEquals(DeletionMode.Immediate, confirm.mode)
    }

    @Test
    fun submitDelete_immediateMode_passesImmediateToRepository() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "dave@example.com"
        }
        coEvery {
            authRepository.deleteAccountWithPassword("pw", DeletionMode.Immediate)
        } returns DeleteAccountResult.Submitted(DeletionMode.Immediate)
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.setDeletionMode(DeletionMode.Immediate)
        vm.chooseReAuthMethod(useGoogle = false)
        vm.onPasswordChange("pw")
        vm.confirmPasswordReauth()
        vm.onTypedEmailChange("dave@example.com")

        var submittedMode: DeletionMode? = null
        vm.submitDelete { submittedMode = it }
        advanceUntilIdle()

        assertEquals(DeletionMode.Immediate, submittedMode)
        coVerify(exactly = 1) {
            authRepository.deleteAccountWithPassword("pw", DeletionMode.Immediate)
        }
        // The scheduled overload must not be touched.
        coVerify(exactly = 0) {
            authRepository.deleteAccountWithPassword("pw", DeletionMode.Scheduled)
        }
    }

    @Test
    fun submitDelete_immediateMode_googlePath_passesImmediateToRepository() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getUserEmail() } returns "eve@example.com"
        }
        coEvery {
            authRepository.deleteAccountWithGoogle("tok", "n", DeletionMode.Immediate)
        } returns DeleteAccountResult.Submitted(DeletionMode.Immediate)
        val vm = newDeleteVm(authRepository = authRepository)
        vm.startDeleteAccount()
        vm.setDeletionMode(DeletionMode.Immediate)
        vm.chooseReAuthMethod(useGoogle = true)
        vm.onGoogleAuthResult(GoogleAuthResult.Success("tok", "n"))
        vm.onTypedEmailChange("eve@example.com")

        var submittedMode: DeletionMode? = null
        vm.submitDelete { submittedMode = it }
        advanceUntilIdle()

        assertEquals(DeletionMode.Immediate, submittedMode)
        coVerify(exactly = 1) {
            authRepository.deleteAccountWithGoogle("tok", "n", DeletionMode.Immediate)
        }
    }
}

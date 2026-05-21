package com.mobaide.app.ui.common

import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.CancelDeletionResult
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
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
 * Unit tests for [GraceBannerViewModel] — covers the
 * request/dismiss/confirm split introduced so a single tap on
 * "Cancel scheduled deletion" no longer silently restores the account.
 *
 * The previous flow called `cancel()` directly on click; the new flow:
 *
 *   1. tap → [GraceBannerViewModel.requestCancel] → `showConfirm = true`
 *   2. dialog "Dismiss" → [GraceBannerViewModel.dismissConfirm]
 *      → `showConfirm = false`, NO API call
 *   3. dialog "Keep account active" → [GraceBannerViewModel.confirmCancel]
 *      → fires the API call, clears the banner on success
 *
 * The state's [GraceBannerState.scheduledFor] is seeded synchronously
 * from [AuthRepository.getScheduledDeletionAt] in the constructor.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GraceBannerViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun newVm(
        scheduledFor: String? = "2026-06-04T12:00:00+00:00",
        authRepository: AuthRepository = mockk(relaxed = true) {
            every { getScheduledDeletionAt() } returns scheduledFor
        },
    ): GraceBannerViewModel = GraceBannerViewModel(authRepository)

    @Test
    fun initialState_seedsScheduledForFromAuthRepository() = runTest {
        val vm = newVm(scheduledFor = "2026-06-04T12:00:00+00:00")

        val state = vm.state.value
        assertEquals("2026-06-04T12:00:00+00:00", state.scheduledFor)
        assertFalse(state.cancelling)
        assertNull(state.errorMessage)
        assertFalse(state.showConfirm)
    }

    @Test
    fun initialState_nullScheduledFor_meansBannerHidden() = runTest {
        val vm = newVm(scheduledFor = null)

        assertNull(vm.state.value.scheduledFor)
    }

    @Test
    fun requestCancel_setsShowConfirm_doesNotCallApi() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        val vm = GraceBannerViewModel(authRepository)

        vm.requestCancel()
        advanceUntilIdle()

        assertTrue(vm.state.value.showConfirm)
        // No API call yet — the user still has to confirm in the dialog.
        coVerify(exactly = 0) { authRepository.cancelAccountDeletion() }
        // Banner still visible — the schedule is unchanged at this point.
        assertEquals("2026-06-04T12:00:00+00:00", vm.state.value.scheduledFor)
    }

    @Test
    fun requestCancel_clearsAnyExistingErrorMessage() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        coEvery { authRepository.cancelAccountDeletion() } throws
            RuntimeException("Network error")
        val vm = GraceBannerViewModel(authRepository)

        // Trigger a failing confirm to populate errorMessage.
        vm.requestCancel()
        vm.confirmCancel()
        advanceUntilIdle()
        assertNotNull(vm.state.value.errorMessage)

        // A fresh requestCancel must clear the stale error so the dialog
        // does not show a misleading banner from the previous attempt.
        vm.requestCancel()
        assertNull(vm.state.value.errorMessage)
        assertTrue(vm.state.value.showConfirm)
    }

    @Test
    fun dismissConfirm_clearsShowConfirm_doesNotCallApi() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        val vm = GraceBannerViewModel(authRepository)
        vm.requestCancel()
        assertTrue(vm.state.value.showConfirm)

        vm.dismissConfirm()

        assertFalse(vm.state.value.showConfirm)
        // Dismiss must NOT touch the API — that was the whole point of
        // the confirmation step.
        coVerify(exactly = 0) { authRepository.cancelAccountDeletion() }
        // Schedule remains in place; banner stays visible.
        assertEquals("2026-06-04T12:00:00+00:00", vm.state.value.scheduledFor)
    }

    @Test
    fun confirmCancel_onCancelled_clearsScheduleAndShowConfirm() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        coEvery { authRepository.cancelAccountDeletion() } returns
            CancelDeletionResult.Cancelled
        val vm = GraceBannerViewModel(authRepository)
        vm.requestCancel()

        vm.confirmCancel()
        advanceUntilIdle()

        val state = vm.state.value
        assertNull(state.scheduledFor)
        assertFalse(state.cancelling)
        assertFalse(state.showConfirm)
        coVerify(exactly = 1) { authRepository.cancelAccountDeletion() }
    }

    @Test
    fun confirmCancel_onNotScheduled_alsoClearsBanner() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        coEvery { authRepository.cancelAccountDeletion() } returns
            CancelDeletionResult.NotScheduled
        val vm = GraceBannerViewModel(authRepository)
        vm.requestCancel()

        vm.confirmCancel()
        advanceUntilIdle()

        // ``NotScheduled`` means the server says "nothing to cancel" — the
        // banner should also disappear so the local view stays consistent
        // (covered by the matching repository test).
        assertNull(vm.state.value.scheduledFor)
        assertFalse(vm.state.value.showConfirm)
    }

    @Test
    fun confirmCancel_onException_surfacesErrorAndKeepsBanner() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        coEvery { authRepository.cancelAccountDeletion() } throws
            RuntimeException("Boom")
        val vm = GraceBannerViewModel(authRepository)
        vm.requestCancel()

        vm.confirmCancel()
        advanceUntilIdle()

        val state = vm.state.value
        // Schedule untouched — the user is NOT silently moved to "cancelled".
        assertEquals("2026-06-04T12:00:00+00:00", state.scheduledFor)
        assertFalse(state.cancelling)
        assertEquals("Boom", state.errorMessage)
        // Dialog auto-closes regardless so the user can retry from the
        // banner without an orphan dialog stuck on top.
        assertFalse(state.showConfirm)
    }

    @Test
    fun confirmCancel_setsCancellingTrueWhileInFlight() = runTest {
        val authRepository = mockk<AuthRepository>(relaxed = true) {
            every { getScheduledDeletionAt() } returns "2026-06-04T12:00:00+00:00"
        }
        // Use a CompletableDeferred-like pattern: have the suspend never
        // resolve in this test — we only care about the in-flight flag.
        coEvery { authRepository.cancelAccountDeletion() } coAnswers {
            kotlinx.coroutines.awaitCancellation()
        }
        val vm = GraceBannerViewModel(authRepository)
        vm.requestCancel()

        vm.confirmCancel()
        // Do NOT advanceUntilIdle — the suspend would never complete.
        // The cancelling flag should be true right after we launch.

        val state = vm.state.value
        assertTrue(state.cancelling)
        assertFalse(state.showConfirm)
        // Dialog closes so the spinner on the banner button is visible.
    }
}

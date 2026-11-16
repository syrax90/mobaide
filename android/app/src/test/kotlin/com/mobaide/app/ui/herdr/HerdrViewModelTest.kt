package com.mobaide.app.ui.herdr

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.network.dto.HerdrCreateRequest
import com.mobaide.app.data.network.dto.HerdrPane
import com.mobaide.app.data.network.dto.HerdrServer
import com.mobaide.app.data.network.dto.HerdrSnapshot
import com.mobaide.app.data.network.dto.HerdrTab
import com.mobaide.app.data.network.dto.HerdrWorkspace
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.repository.HerdrException
import com.mobaide.app.data.repository.HerdrRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Where the Herdr dashboard puts the reason of a failed action.
 *
 * A dialog covers the snackbar, so a dialog action's failure belongs to the
 * screen state that the open dialog draws. A split has no dialog, so its
 * failure still is a message.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HerdrViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val snapshot = MutableStateFlow<HerdrSnapshot?>(
        HerdrSnapshot(
            listOf(
                HerdrServer(
                    server_id = SERVER,
                    workspaces = listOf(
                        HerdrWorkspace(
                            "w1",
                            "mobaide",
                            listOf(HerdrTab("w1:t1", "main", panes = listOf(HerdrPane(PANE, agent = "claude", status = "working")))),
                        ),
                    ),
                ),
            ),
        ),
    )
    private val repository = mockk<HerdrRepository>(relaxed = true).also {
        every { it.snapshot } returns snapshot
        every { it.agentId() } returns "agent-1"
    }
    private val seenStore = mockk<HerdrSeenStore>().also {
        every { it.seen } returns MutableStateFlow(emptyMap())
    }
    private val projectsRepository = mockk<ProjectsRepository>(relaxed = true).also {
        every { it.observeProjects() } returns MutableStateFlow(emptyList<ProjectInfo>())
    }

    private fun viewModel() = HerdrViewModel(SavedStateHandle(), repository, seenStore, projectsRepository)

    /** Collect the screen state and the one-shot events, as the screen does. */
    private fun TestScope.eventsOf(vm: HerdrViewModel): List<HerdrEvent> {
        val events = mutableListOf<HerdrEvent>()
        backgroundScope.launch { vm.uiState.collect {} }
        backgroundScope.launch { vm.events.collect { events += it } }
        advanceUntilIdle()
        return events
    }

    private val HerdrViewModel.success: HerdrUiState.Success
        get() = uiState.value as HerdrUiState.Success

    @Test
    fun failedDialogAction_putsItsReasonInTheState_andSendsNoMessage() = runTest {
        coEvery { repository.create(any()) } throws HerdrException("invalid_folder", REFUSAL)
        val vm = viewModel()
        val events = eventsOf(vm)

        vm.create(HerdrCreateRequest(SERVER, HerdrCreateRequest.PLACEMENT_WORKSPACE, cwd = "/missing"))
        advanceUntilIdle()

        assertEquals(REFUSAL, vm.success.actionError)
        assertEquals(false, vm.success.busy)
        // No message under the dialog, and no "done": the dialog stays open
        // with what the user typed.
        assertEquals(emptyList<HerdrEvent>(), events)
    }

    @Test
    fun failedSplit_isAMessage_becauseItHasNoDialog() = runTest {
        coEvery { repository.create(any()) } throws HerdrException("herdr_refused", REFUSAL)
        val vm = viewModel()
        val events = eventsOf(vm)

        vm.create(
            HerdrCreateRequest(SERVER, HerdrCreateRequest.PLACEMENT_SPLIT_RIGHT, pane_id = PANE),
            fromDialog = false,
        )
        advanceUntilIdle()

        assertEquals(listOf<HerdrEvent>(HerdrEvent.ShowMessage(REFUSAL)), events)
        // The next dialog must not open with the reason of a split.
        assertNull(vm.success.actionError)
    }

    @Test
    fun actionError_goesAway_whenADialogOpensOrCloses_andWhenTheNextActionStarts() = runTest {
        coEvery { repository.close(any(), any(), any()) } throws HerdrException("herdr_refused", REFUSAL)
        val vm = viewModel()
        val events = eventsOf(vm)
        vm.close(SERVER, "pane", PANE)
        advanceUntilIdle()
        assertEquals(REFUSAL, vm.success.actionError)

        // The screen calls this when a dialog opens or closes: the reason of
        // "close" must not show in the rename dialog that opens next.
        vm.clearActionError()
        advanceUntilIdle()
        assertNull(vm.success.actionError)

        vm.close(SERVER, "pane", PANE)
        advanceUntilIdle()
        assertEquals(REFUSAL, vm.success.actionError)

        // A second try, still on its way: the old reason is not under its progress.
        val answer = CompletableDeferred<Unit>()
        coEvery { repository.rename(any(), any(), any(), any()) } coAnswers { answer.await() }
        vm.rename(SERVER, "pane", PANE, "api")
        advanceUntilIdle()
        assertNull(vm.success.actionError)
        assertTrue(vm.success.busy)

        answer.complete(Unit)
        advanceUntilIdle()
        assertEquals(listOf<HerdrEvent>(HerdrEvent.ActionDone), events)
        assertNull(vm.success.actionError)
    }

    @Test
    fun openPane_namesItsRowWhileItIsOnItsWay_andASecondTapStartsNoSecondOpen() = runTest {
        val answer = CompletableDeferred<String>()
        coEvery { repository.openPane(any(), any()) } coAnswers { answer.await() }
        val vm = viewModel()
        val events = eventsOf(vm)
        assertNull(vm.success.openingKey)

        vm.openPane(SERVER, PANE)
        advanceUntilIdle()
        // The key of the row, so the screen finds the row that shows progress.
        assertEquals(vm.success.rows.single().key, vm.success.openingKey)

        // The open is a round trip to the agent: a tap on another row while it
        // runs must not open a second terminal over the first.
        vm.openPane(SERVER, "w1:p2")
        answer.complete("herdr-0123")
        advanceUntilIdle()

        assertEquals(listOf<HerdrEvent>(HerdrEvent.OpenPane("herdr-0123", SERVER, PANE)), events)
        assertNull(vm.success.openingKey)
    }

    @Test
    fun failedOpen_freesTheRow_soTheNextTapOpens() = runTest {
        coEvery { repository.openPane(any(), any()) } throws HerdrException("herdr_refused", REFUSAL)
        val vm = viewModel()
        val events = eventsOf(vm)

        vm.openPane(SERVER, PANE)
        advanceUntilIdle()

        assertEquals(listOf<HerdrEvent>(HerdrEvent.ShowMessage(REFUSAL)), events)
        assertNull(vm.success.openingKey)

        // A row that kept its progress after a refusal would drop every tap.
        coEvery { repository.openPane(any(), any()) } returns "herdr-0123"
        vm.openPane(SERVER, PANE)
        advanceUntilIdle()

        assertEquals(HerdrEvent.OpenPane("herdr-0123", SERVER, PANE), events.last())
    }

    private companion object {
        const val SERVER = "tui:default"
        const val PANE = "w1:p1"
        const val REFUSAL = "This folder does not exist: /missing"
    }
}

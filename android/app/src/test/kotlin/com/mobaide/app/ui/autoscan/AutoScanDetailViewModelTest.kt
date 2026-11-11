package com.mobaide.app.ui.autoscan

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.events.ScanProgressPayload
import com.mobaide.app.data.network.AutoScanWsClient
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.RunNowResponse
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.data.repository.AutoScanUiEvent
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
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
 * Unit tests for [AutoScanDetailViewModel] — exercises the frame reducer
 * (Connected / Snapshot / CaughtUp / Event / Disconnected), phase-state
 * transitions around `scan_started` / `scan_completed`, the issue circular
 * buffer (max 50), the log-ring overflow at [LOG_RING_MAX] (2000), `lastSeq`
 * tracking, the autoscroll-pause flag, and the `runNow()` / `stop()` dispatch
 * to the repository.
 *
 * The repository is mocked with MockK; `observeDetailFrames` is wired to a
 * real [MutableSharedFlow] so the test body can push synthetic frames and
 * observe the reducer's effect on `uiState` deterministically under
 * [MainDispatcherRule]'s StandardTestDispatcher.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AutoScanDetailViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private fun instance(name: String = PROJECT, cycle: Int = 3): AutoScanInstanceDto =
        AutoScanInstanceDto(
            project_name = name,
            status = "running",
            started_at = 1.0,
            last_activity_at = 1.0,
            next_scan_at = "2026-04-11T01:00:00Z",
            cycle_number = cycle,
        )

    private fun parsedEvent(
        seq: Long,
        payload: ScanProgressPayload,
        project: String = PROJECT,
        type: String = "phase",
    ): AutoScanWsClient.ParsedEvent = AutoScanWsClient.ParsedEvent(
        projectName = project,
        seq = seq,
        ts = "2026-04-11T00:00:00Z",
        type = type,
        payload = payload,
    )

    private fun phase(
        name: String,
        cycle: Int = 3,
        next: String? = null,
        issuesProcessed: Int = 0,
    ) = ScanProgressPayload.Phase(
        phase = name,
        cycleNumber = cycle,
        nextScanAt = next,
        issuesProcessed = issuesProcessed,
    )

    private fun issue(
        id: String,
        step: String = "state_update",
        status: String = "started",
        error: String? = null,
    ) = ScanProgressPayload.Issue(
        identifier = id,
        title = "title-$id",
        step = step,
        status = status,
        error = error,
    )

    private class Fixture(
        val repository: AutoScanRepository,
        val frames: MutableSharedFlow<AutoScanWsClient.Frame>,
        val events: MutableSharedFlow<AutoScanUiEvent>,
    )

    private fun newFixture(): Fixture {
        val repository = mockk<AutoScanRepository>(relaxed = true)
        // extraBufferCapacity=128 so the test can emit a batch (including the
        // 2001-frame log-overflow test) without suspending before the VM's
        // collector attaches.
        val frames = MutableSharedFlow<AutoScanWsClient.Frame>(
            replay = 0,
            extraBufferCapacity = 4096,
        )
        val events = MutableSharedFlow<AutoScanUiEvent>(replay = 0, extraBufferCapacity = 4)
        every { repository.events } returns events
        every { repository.observeDetailFrames(PROJECT, any()) } returns frames
        return Fixture(repository, frames, events)
    }

    private fun Fixture.buildVm(): AutoScanDetailViewModel {
        val handle = SavedStateHandle(mapOf("projectName" to PROJECT))
        return AutoScanDetailViewModel(handle, repository, AppForegroundTracker())
    }

    // ---- Frame reducer ------------------------------------------------------

    @Test
    fun snapshotFrame_seedsInstance_andReducesBufferedEvents() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val bufferedStarted = parsedEvent(seq = 10, payload = phase("scan_started", cycle = 5))
        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Snapshot(
                scans = listOf(instance(cycle = 5)),
                events = mapOf(PROJECT to listOf(bufferedStarted)),
            ),
        )
        advanceUntilIdle()

        val s = vm.uiState.value
        assertNotNull(s.instance)
        assertEquals(PROJECT, s.instance!!.project_name)
        assertEquals(5, s.cycleNumber)
        assertEquals(
            AutoScanDetailViewModel.ConnectionState.Replaying,
            s.connection,
        )
        // The buffered scan_started event must have run through reduceEvent.
        assertEquals("scan", s.activePhase)
        assertEquals(10L, s.lastSeq)
    }

    @Test
    fun eventFrame_forDifferentProject_isIgnored() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(seq = 7, payload = phase("scan_started"), project = "other"),
            ),
        )
        advanceUntilIdle()

        // lastSeq is not advanced because the foreign-project event is skipped.
        assertEquals(0L, vm.uiState.value.lastSeq)
        assertNull(vm.uiState.value.activePhase)
    }

    // ---- Phase transitions --------------------------------------------------

    @Test
    fun phase_scanStarted_then_scanCompleted_transitionsActiveToOk_idleMarksPendingSkipped() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(seq = 1, payload = phase("scan_started", cycle = 3)),
            ),
        )
        advanceUntilIdle()
        assertEquals("scan", vm.uiState.value.activePhase)
        assertEquals("active", vm.uiState.value.phases["scan"]?.status)

        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(
                    seq = 2,
                    payload = phase(
                        "scan_completed",
                        cycle = 3,
                        next = "2026-04-11T02:00:00Z",
                        issuesProcessed = 0, // idle
                    ),
                ),
            ),
        )
        advanceUntilIdle()

        val s = vm.uiState.value
        assertNull(s.activePhase)
        // The previously "active" scan phase flips to "ok".
        assertEquals("ok", s.phases["scan"]?.status)
        // The remaining "pending" phases flip to "skipped" because of the
        // idle path (issuesProcessed == 0).
        assertEquals("skipped", s.phases["state"]?.status)
        assertEquals("skipped", s.phases["label"]?.status)
        assertEquals("2026-04-11T02:00:00Z", s.nextScanAt)
        assertEquals(2L, s.lastSeq)
    }

    @Test
    fun phase_scanCompleted_nonIdle_leavesPendingPhasesUnchanged() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(seq = 1, payload = phase("scan_started", cycle = 4)),
            ),
        )
        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(
                    seq = 2,
                    payload = phase("scan_completed", cycle = 4, issuesProcessed = 3),
                ),
            ),
        )
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("ok", s.phases["scan"]?.status)
        // Non-idle => pending phases stay pending.
        assertEquals("pending", s.phases["state"]?.status)
    }

    // ---- Issue circular buffer (max 50) -------------------------------------

    @Test
    fun issueEvents_beyondFifty_dropsOldest_keepingMaxSize() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        repeat(55) { i ->
            fx.frames.tryEmit(
                AutoScanWsClient.Frame.Event(
                    event = parsedEvent(
                        seq = (i + 1).toLong(),
                        payload = issue(id = "ISSUE-$i"),
                        type = "issue",
                    ),
                ),
            )
        }
        advanceUntilIdle()

        val issues = vm.uiState.value.recentIssues
        assertEquals(50, issues.size)
        // The oldest five entries (ISSUE-0..ISSUE-4) were evicted from the
        // head, so the surviving head is ISSUE-5 and the tail is ISSUE-54.
        assertEquals("ISSUE-5", issues.first().identifier)
        assertEquals("ISSUE-54", issues.last().identifier)
        assertEquals(55L, vm.uiState.value.lastSeq)
    }

    // ---- Log ring overflow (LOG_RING_MAX = 2000) ----------------------------

    @Test
    fun logRing_overflowsAtMax_keepingExactly2000Lines() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val total = LOG_RING_MAX + 1 // 2001 entries, one over the cap
        repeat(total) { i ->
            fx.frames.tryEmit(
                AutoScanWsClient.Frame.Event(
                    event = parsedEvent(
                        seq = (i + 1).toLong(),
                        payload = ScanProgressPayload.Log(
                            line = "log-$i",
                            level = "info",
                            ansi = false,
                        ),
                        type = "log",
                    ),
                ),
            )
        }
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals(LOG_RING_MAX, s.logLines.size)
        // First surviving entry is the second emission (index 1), last is 2000.
        assertEquals("log-1", s.logLines.first().text.text)
        assertEquals("log-$total".let { "log-${total - 1}" }, s.logLines.last().text.text)
        // Log line ids are monotonically increasing and never recycled.
        assertTrue(s.logLines.last().id > s.logLines.first().id)
        assertEquals(total.toLong(), s.lastSeq)
    }

    // ---- lastSeq tracking ---------------------------------------------------

    @Test
    fun lastSeq_isMonotonicCoerceAtLeast_soOutOfOrderEventsDoNotRegress() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(seq = 10, payload = phase("scan_started")),
            ),
        )
        advanceUntilIdle()
        assertEquals(10L, vm.uiState.value.lastSeq)

        // Out-of-order late event with smaller seq — must not roll back.
        fx.frames.tryEmit(
            AutoScanWsClient.Frame.Event(
                event = parsedEvent(seq = 5, payload = phase("scan_stopped")),
            ),
        )
        advanceUntilIdle()
        assertEquals(10L, vm.uiState.value.lastSeq)
    }

    // ---- Autoscroll-pause flag ---------------------------------------------

    @Test
    fun setPaused_true_causesNewLineCountToAccumulate_andResumeResetsIt() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setPaused(true)
        assertTrue(vm.uiState.value.isPausedAutoscroll)

        // Three log lines while paused.
        repeat(3) { i ->
            fx.frames.tryEmit(
                AutoScanWsClient.Frame.Event(
                    event = parsedEvent(
                        seq = (i + 1).toLong(),
                        payload = ScanProgressPayload.Log("l$i", "info", false),
                        type = "log",
                    ),
                ),
            )
        }
        advanceUntilIdle()

        assertEquals(3, vm.uiState.value.newLineCount)
        assertTrue(vm.uiState.value.isPausedAutoscroll)

        vm.resumeAutoscroll()
        val s = vm.uiState.value
        assertFalse(s.isPausedAutoscroll)
        assertEquals(0, s.newLineCount)
    }

    // ---- runNow() / stop() dispatch -----------------------------------------

    @Test
    fun runNow_invokesRepository_andAppliesNextScanAtFromResponse() = runTest {
        val fx = newFixture()
        coEvery { fx.repository.runNow(PROJECT) } returns RunNowResponse(
            ok = true,
            project_name = PROJECT,
            next_scan_at = "2026-04-11T03:00:00Z",
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.runNow()
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.repository.runNow(PROJECT) }
        assertEquals("2026-04-11T03:00:00Z", vm.uiState.value.nextScanAt)
    }

    companion object {
        private const val PROJECT = "alpha"
    }
}

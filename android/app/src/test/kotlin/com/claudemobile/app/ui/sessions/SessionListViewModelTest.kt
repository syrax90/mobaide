package com.claudemobile.app.ui.sessions

import app.cash.turbine.test
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import com.claudemobile.app.data.network.dto.SessionResponse
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.SessionRepository
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [SessionListViewModel] — exercises auto-resume matching
 * (last-session id + running/starting state filter via [PreferencesStore]),
 * [ServerEventBus.connection]-driven `isReconnecting` state, the
 * `deleteSession` / `stopAutoScan` error-emission paths, and the
 * `observeSessionList` / `observeList` pass-through to the UI state.
 *
 * Dependencies exposing flows are stubbed with real [MutableStateFlow]
 * instances so that the ViewModel's `init { }` collectors attach and react to
 * emissions. [MainDispatcherRule] installs a `StandardTestDispatcher` as the
 * Main replacement so that the four `viewModelScope.launch { }` blocks are
 * deterministic under `runTest { advanceUntilIdle() }`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SessionListViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private fun session(
        id: String,
        state: String = "running",
    ): SessionResponse = SessionResponse(
        session_id = id,
        state = state,
        created_at = "2026-04-11T00:00:00Z",
    )

    private class Fixture(
        val sessionRepo: SessionRepository,
        val autoScanRepo: AutoScanRepository,
        val prefs: PreferencesStore,
        val bus: ServerEventBus,
        val sessionsFlow: MutableStateFlow<List<SessionResponse>>,
        val scanListFlow: MutableStateFlow<AutoScanListResponse>,
        val connectionFlow: MutableStateFlow<ServerEventBus.ConnState>,
    )

    /**
     * Builds all mocks and returns a [Fixture] WITHOUT constructing the
     * ViewModel — the caller decides when to instantiate it (so that
     * `preferencesStore.getLastUsedSessionId()` stubbing can happen first).
     */
    private fun newFixture(
        initialSessions: List<SessionResponse> = emptyList(),
        initialScanList: AutoScanListResponse = AutoScanListResponse(
            scans = emptyList(),
            capacity = 0,
            max_scans = 5,
            available_projects = emptyList(),
        ),
        initialConnection: ServerEventBus.ConnState = ServerEventBus.ConnState.Disconnected,
    ): Fixture {
        val sessionRepo = mockk<SessionRepository>(relaxed = true)
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        val prefs = mockk<PreferencesStore>(relaxed = true)
        val bus = mockk<ServerEventBus>(relaxed = true)

        val sessionsFlow = MutableStateFlow(initialSessions)
        val scanListFlow = MutableStateFlow(initialScanList)
        val connectionFlow = MutableStateFlow(initialConnection)

        every { sessionRepo.observeSessionList() } returns sessionsFlow
        every { autoScanRepo.observeList() } returns scanListFlow
        every { bus.connection } returns connectionFlow
        every { bus.start() } just Runs
        every { bus.forceReconnect() } just Runs
        coEvery { autoScanRepo.refresh() } returns initialScanList
        // Safe default — individual tests may re-stub with a real id.
        coEvery { prefs.getLastUsedSessionId() } returns null
        coEvery { prefs.setLastUsedSessionId(any()) } just Runs

        return Fixture(
            sessionRepo, autoScanRepo, prefs, bus,
            sessionsFlow, scanListFlow, connectionFlow,
        )
    }

    private fun Fixture.buildVm(): SessionListViewModel =
        SessionListViewModel(sessionRepo, autoScanRepo, prefs, bus)

    // ---- init() side-effects -----------------------------------------------

    @Test
    fun init_startsEventBus_andSeedsAutoScanList() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        verify(exactly = 1) { fx.bus.start() }
        coVerify(exactly = 1) { fx.autoScanRepo.refresh() }
        assertEquals(emptyList<SessionResponse>(), vm.uiState.value.sessions)
        assertEquals(emptyList<AutoScanInstanceDto>(), vm.uiState.value.activeScans)
        assertFalse(vm.uiState.value.isReconnecting)
        assertNull(vm.uiState.value.error)
    }

    @Test
    fun init_whenAutoScanRefreshThrows_doesNotCrash_andLeavesStateEmpty() = runTest {
        val fx = newFixture()
        coEvery { fx.autoScanRepo.refresh() } throws RuntimeException("no autoscan")
        val vm = fx.buildVm()
        advanceUntilIdle()

        // Ignored exception path — UI state is still the default empty.
        assertEquals(emptyList<AutoScanInstanceDto>(), vm.uiState.value.activeScans)
        assertNull(vm.uiState.value.error)
    }

    // ---- Session list pass-through -----------------------------------------

    @Test
    fun observeSessionList_emissions_flowIntoUiState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.uiState.test {
            assertEquals(emptyList<SessionResponse>(), awaitItem().sessions)

            fx.sessionsFlow.value = listOf(session("s1"), session("s2", state = "exited"))
            advanceUntilIdle()

            val updated = awaitItem()
            assertEquals(2, updated.sessions.size)
            assertEquals("s1", updated.sessions[0].session_id)
            assertEquals("s2", updated.sessions[1].session_id)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun observeScanList_emissions_flowIntoActiveScansUiState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val pushed = AutoScanListResponse(
            scans = listOf(
                AutoScanInstanceDto(
                    project_name = "p1",
                    status = "running",
                    started_at = 1.0,
                    last_activity_at = 1.0,
                ),
            ),
            capacity = 1,
            max_scans = 5,
            available_projects = listOf("p1"),
        )
        fx.scanListFlow.value = pushed
        advanceUntilIdle()

        assertEquals(1, vm.uiState.value.activeScans.size)
        assertEquals("p1", vm.uiState.value.activeScans[0].project_name)
    }

    // ---- Auto-resume: PreferencesStore + state filter ----------------------

    @Test
    fun maybeAutoResume_emitsLastSessionId_whenStateIsRunning() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedSessionId() } returns "s-running"
        val vm = fx.buildVm()

        vm.autoResumeSessionId.test {
            fx.sessionsFlow.value = listOf(
                session("s-exited", state = "exited"),
                session("s-running", state = "running"),
            )
            advanceUntilIdle()

            assertEquals("s-running", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 0) { fx.prefs.setLastUsedSessionId(null) }
    }

    @Test
    fun maybeAutoResume_emitsLastSessionId_whenStateIsStarting() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedSessionId() } returns "s-starting"
        val vm = fx.buildVm()

        vm.autoResumeSessionId.test {
            fx.sessionsFlow.value = listOf(session("s-starting", state = "starting"))
            advanceUntilIdle()

            assertEquals("s-starting", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun maybeAutoResume_clearsStoredId_whenSessionExists_butStateIsDead() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedSessionId() } returns "s-exited"
        val vm = fx.buildVm()

        // Collector attached before the sessions emission so we can prove
        // nothing is emitted on this path.
        vm.autoResumeSessionId.test {
            fx.sessionsFlow.value = listOf(session("s-exited", state = "exited"))
            advanceUntilIdle()

            // No emission because the session is not running/starting.
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId(null) }
    }

    @Test
    fun maybeAutoResume_noOp_whenLastSessionIdMissing() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedSessionId() } returns null
        val vm = fx.buildVm()

        vm.autoResumeSessionId.test {
            fx.sessionsFlow.value = listOf(session("s1", state = "running"))
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 0) { fx.prefs.setLastUsedSessionId(any()) }
    }

    @Test
    fun maybeAutoResume_noOp_whenLastSessionIdNotInCurrentList() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedSessionId() } returns "ghost"
        val vm = fx.buildVm()

        vm.autoResumeSessionId.test {
            fx.sessionsFlow.value = listOf(session("s1", state = "running"))
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        // No clear is called when the session isn't found at all — only when
        // it's found but in a non-resumable state.
        coVerify(exactly = 0) { fx.prefs.setLastUsedSessionId(null) }
    }

    @Test
    fun maybeAutoResume_runsAtMostOnce_acrossSubsequentEmissions() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedSessionId() } returns "s1"
        val vm = fx.buildVm()

        vm.autoResumeSessionId.test {
            fx.sessionsFlow.value = listOf(session("s1", state = "running"))
            advanceUntilIdle()
            assertEquals("s1", awaitItem())

            // Second list emission must NOT re-trigger auto-resume, even if
            // another matching session arrives.
            fx.sessionsFlow.value = listOf(
                session("s1", state = "running"),
                session("s2", state = "running"),
            )
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        // getLastUsedSessionId should have been consulted exactly once.
        coVerify(exactly = 1) { fx.prefs.getLastUsedSessionId() }
    }

    // ---- isReconnecting driven by ServerEventBus.connection ----------------

    @Test
    fun isReconnecting_isTrue_whenConnectionStateIsConnecting() = runTest {
        val fx = newFixture(initialConnection = ServerEventBus.ConnState.Disconnected)
        val vm = fx.buildVm()
        advanceUntilIdle()
        assertFalse(vm.uiState.value.isReconnecting)

        fx.connectionFlow.value = ServerEventBus.ConnState.Connecting
        advanceUntilIdle()

        assertTrue(vm.uiState.value.isReconnecting)

        fx.connectionFlow.value = ServerEventBus.ConnState.Connected
        advanceUntilIdle()

        assertFalse(vm.uiState.value.isReconnecting)
    }

    // ---- refresh() ----------------------------------------------------------

    @Test
    fun refresh_forcesBusReconnect_andRefreshesAutoScanList() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        // Initial refresh from init() consumed one call — reset expectations.
        coEvery { fx.autoScanRepo.refresh() } returns AutoScanListResponse(
            scans = emptyList(),
            capacity = 0,
            max_scans = 5,
            available_projects = emptyList(),
        )

        vm.refresh()
        advanceUntilIdle()

        verify(exactly = 1) { fx.bus.forceReconnect() }
        coVerify(atLeast = 2) { fx.autoScanRepo.refresh() }
    }

    @Test
    fun refresh_swallowsAutoScanRefreshException() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.autoScanRepo.refresh() } throws RuntimeException("down")

        // Must not throw.
        vm.refresh()
        advanceUntilIdle()

        assertNull(vm.uiState.value.error)
    }

    // ---- deleteSession error path ------------------------------------------

    @Test
    fun deleteSession_happyPath_doesNotSetError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.sessionRepo.deleteSession("s1") } just Runs

        vm.deleteSession("s1")
        advanceUntilIdle()

        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) { fx.sessionRepo.deleteSession("s1") }
    }

    @Test
    fun deleteSession_onException_setsErrorWithMessage() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.sessionRepo.deleteSession("s1") } throws RuntimeException("boom")

        vm.deleteSession("s1")
        advanceUntilIdle()

        val err = vm.uiState.value.error
        assertNotNull(err)
        assertEquals("Failed to stop session: boom", err)
    }

    // ---- stopAutoScan error path -------------------------------------------

    @Test
    fun stopAutoScan_happyPath_doesNotSetError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.autoScanRepo.stopScan("p1") } just Runs

        vm.stopAutoScan("p1")
        advanceUntilIdle()

        assertNull(vm.uiState.value.error)
        coVerify(exactly = 1) { fx.autoScanRepo.stopScan("p1") }
    }

    @Test
    fun stopAutoScan_onException_setsErrorWithMessage() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.autoScanRepo.stopScan("p1") } throws RuntimeException("network")

        vm.stopAutoScan("p1")
        advanceUntilIdle()

        assertEquals("Failed to stop scan: network", vm.uiState.value.error)
    }

    // ---- clearError ---------------------------------------------------------

    @Test
    fun clearError_resetsErrorFieldToNull_leavingOtherStateIntact() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // Seed an error via the deleteSession failure path.
        coEvery { fx.sessionRepo.deleteSession("s1") } throws RuntimeException("oops")
        vm.deleteSession("s1")
        advanceUntilIdle()
        assertNotNull(vm.uiState.value.error)

        // Seed some sessions so we can prove clearError doesn't wipe them.
        fx.sessionsFlow.value = listOf(session("s1", state = "running"))
        advanceUntilIdle()

        vm.clearError()

        assertNull(vm.uiState.value.error)
        assertEquals(1, vm.uiState.value.sessions.size)
    }
}

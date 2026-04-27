package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.AutoScanWsClient
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.AutoScanListResponse
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.RunNowResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [AutoScanRepository] — exercises optimistic add / remove
 * with rollback, 409→[AutoScanAlreadyRunningException], 429→[AutoScanCapacityException]
 * error classification, `observeScans` flow, and event-bus-driven list refresh.
 *
 * All collaborators are mocked. [AgentEventBus.events] is stubbed to a real
 * [MutableSharedFlow] so that `eventBus.stream<T>()` (an inline function that
 * expands to `events.filterIsInstance()`) works without needing a real bus.
 *
 * Note: [AutoScanRepository] launches its two event-bus collectors on
 * `Dispatchers.Default` via an internal [kotlinx.coroutines.CoroutineScope],
 * so event-driven tests rely on Turbine's default timeout for cross-thread
 * synchronisation.
 */
class AutoScanRepositoryTest {

    private fun instance(
        projectName: String,
        status: String = "running",
        startedAt: Double = 1712822400.0,
    ): AutoScanInstanceDto = AutoScanInstanceDto(
        project_name = projectName,
        status = status,
        started_at = startedAt,
        last_activity_at = startedAt,
    )

    private fun newRepo(): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        val wsClient = mockk<AutoScanWsClient>(relaxed = true)
        val repo = AutoScanRepository(restApi, bus, wsClient)
        return Fixture(repo, restApi, bus, eventFlow, wsClient)
    }

    private data class Fixture(
        val repo: AutoScanRepository,
        val restApi: RestApi,
        val bus: AgentEventBus,
        val eventFlow: MutableSharedFlow<AgentEvent>,
        val wsClient: AutoScanWsClient,
    )

    // ---- observeList / observeScans initial state ---------------------------

    @Test
    fun observeList_hasEmptyInitialState_withDefaultMaxScans5() = runTest {
        val (repo) = newRepo()
        val list = repo.observeList().value
        assertTrue(list.scans.isEmpty())
        assertEquals(5, list.max_scans)
        assertEquals(0, list.capacity)
    }

    @Test
    fun observeScans_emitsInitialEmptyList() = runTest {
        val (repo) = newRepo()
        repo.observeScans().test {
            assertEquals(emptyList<AutoScanInstanceDto>(), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- refresh() ----------------------------------------------------------

    @Test
    fun refresh_storesResponseInList_andReturnsIt() = runTest {
        val (repo, restApi) = newRepo()
        val payload = AutoScanListResponse(
            scans = listOf(instance("p1")),
            capacity = 1,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
            auto_scan_prompt = "Do the thing",
        )
        coEvery { restApi.getAutoScanList() } returns payload

        val returned = repo.refresh()

        assertSame(payload, returned)
        assertEquals(payload, repo.observeList().value)
        assertEquals("Do the thing", repo.getAutoScanPrompt())
    }

    // ---- startScan: optimistic add + rollback -------------------------------

    @Test
    fun startScan_happyPath_addsPlaceholder_andPersistsAfterServerOk() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startAutoScan("p1", null) } returns Unit

        repo.startScan("p1")

        val list = repo.observeList().value
        assertEquals(1, list.scans.size)
        val placeholder = list.scans[0]
        assertEquals("p1", placeholder.project_name)
        assertEquals("starting", placeholder.status)
        coVerify(exactly = 1) { restApi.startAutoScan("p1", null) }
    }

    @Test
    fun startScan_doesNotDuplicatePlaceholder_whenProjectAlreadyPresent() = runTest {
        val (repo, restApi) = newRepo()
        // Seed the repo with an existing scan by calling refresh().
        coEvery { restApi.getAutoScanList() } returns AutoScanListResponse(
            scans = listOf(instance("p1", status = "running")),
            capacity = 1,
            max_scans = 5,
            available_projects = listOf("p1"),
        )
        repo.refresh()
        coEvery { restApi.startAutoScan("p1", null) } returns Unit

        repo.startScan("p1")

        val list = repo.observeList().value
        assertEquals(1, list.scans.size)
        // Existing "running" status retained — no starting placeholder added.
        assertEquals("running", list.scans[0].status)
    }

    @Test
    fun startScan_409_rollsBackList_andThrowsAlreadyRunningException() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startAutoScan("p1", null) } throws
            ApiException(409, """{"detail":"already"}""")

        try {
            repo.startScan("p1")
            fail("expected AutoScanAlreadyRunningException")
        } catch (e: AutoScanAlreadyRunningException) {
            assertEquals("p1", e.projectName)
        }

        // Rollback: list is back to empty.
        assertTrue(repo.observeList().value.scans.isEmpty())
    }

    @Test
    fun startScan_429_rollsBackList_andThrowsCapacityException_withMaxScans() = runTest {
        val (repo, restApi) = newRepo()
        // Seed with a non-default max_scans to prove the exception carries it.
        coEvery { restApi.getAutoScanList() } returns AutoScanListResponse(
            scans = listOf(),
            capacity = 5,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
        )
        repo.refresh()

        coEvery { restApi.startAutoScan("p2", null) } throws
            ApiException(429, """{"detail":"capacity"}""")

        try {
            repo.startScan("p2")
            fail("expected AutoScanCapacityException")
        } catch (e: AutoScanCapacityException) {
            assertEquals(5, e.maxScans)
        }

        assertTrue(repo.observeList().value.scans.isEmpty())
    }

    @Test
    fun startScan_otherApiException_rollsBackList_andRethrowsOriginal() = runTest {
        val (repo, restApi) = newRepo()
        val boom = ApiException(500, "server exploded")
        coEvery { restApi.startAutoScan("p1", null) } throws boom

        try {
            repo.startScan("p1")
            fail("expected ApiException to propagate")
        } catch (e: ApiException) {
            assertEquals(500, e.code)
            assertTrue(e.errorBody.contains("exploded"))
        }

        assertTrue(repo.observeList().value.scans.isEmpty())
    }

    @Test
    fun startScan_nonApiException_rollsBackList_andRethrowsUntyped() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startAutoScan("p1", null) } throws
            IllegalArgumentException("bad arg")

        try {
            repo.startScan("p1")
            fail("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            assertEquals("bad arg", e.message)
        }

        assertTrue(repo.observeList().value.scans.isEmpty())
    }

    @Test
    fun startScan_forwardsClaudeOptions_toRestApi() = runTest {
        val (repo, restApi) = newRepo()
        val options = ClaudeOptionsModel(
            worktree = true,
            initial_prompt = "go",
        )
        coEvery { restApi.startAutoScan("p1", options) } returns Unit

        repo.startScan("p1", options)

        coVerify(exactly = 1) { restApi.startAutoScan("p1", options) }
    }

    // ---- startScan: error side-channel --------------------------------------

    @Test
    fun startScan_failure_emitsUiErrorEvent_withClassifiedMessage() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startAutoScan("p1", null) } throws ApiException(409, "{}")

        repo.events.test {
            try {
                repo.startScan("p1")
                fail("expected AutoScanAlreadyRunningException")
            } catch (_: AutoScanAlreadyRunningException) {
                // Expected.
            }
            val evt = awaitItem() as AutoScanUiEvent.Error
            // Classified exception message carries the project name.
            assertTrue(
                "ui error should mention project name — was: ${evt.message}",
                evt.message.contains("p1"),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- stopScan: optimistic removal + rollback ----------------------------

    @Test
    fun stopScan_happyPath_removesFromList_andCallsRestApi() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getAutoScanList() } returns AutoScanListResponse(
            scans = listOf(instance("p1"), instance("p2")),
            capacity = 2,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
        )
        repo.refresh()
        coEvery { restApi.stopAutoScan("p1") } returns Unit

        repo.stopScan("p1")

        val scans = repo.observeList().value.scans
        assertEquals(1, scans.size)
        assertEquals("p2", scans[0].project_name)
        coVerify(exactly = 1) { restApi.stopAutoScan("p1") }
    }

    @Test
    fun stopScan_failure_rollsBackList_andRethrows_andEmitsUiError() = runTest {
        val (repo, restApi) = newRepo()
        val seeded = AutoScanListResponse(
            scans = listOf(instance("p1"), instance("p2")),
            capacity = 2,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
        )
        coEvery { restApi.getAutoScanList() } returns seeded
        repo.refresh()
        coEvery { restApi.stopAutoScan("p1") } throws
            RuntimeException("network down")

        repo.events.test {
            try {
                repo.stopScan("p1")
                fail("expected RuntimeException")
            } catch (e: RuntimeException) {
                assertEquals("network down", e.message)
            }
            val evt = awaitItem() as AutoScanUiEvent.Error
            assertEquals("network down", evt.message)
            cancelAndIgnoreRemainingEvents()
        }

        // Rollback: both scans still present.
        assertEquals(2, repo.observeList().value.scans.size)
    }

    // ---- runNow: optimistic reset + reconcile --------------------------------

    @Test
    fun runNow_happyPath_reconcilesFromServerResponse_andReturnsIt() = runTest {
        val (repo, restApi) = newRepo()
        val seeded = AutoScanListResponse(
            scans = listOf(
                instance("p1").copy(next_scan_at = "2026-04-11T12:00:00Z"),
            ),
            capacity = 1,
            max_scans = 5,
            available_projects = listOf("p1"),
        )
        coEvery { restApi.getAutoScanList() } returns seeded
        repo.refresh()

        val resp = RunNowResponse(
            ok = true,
            project_name = "p1",
            next_scan_at = "2026-04-11T12:30:00Z",
        )
        coEvery { restApi.runAutoScanNow("p1") } returns resp

        val returned = repo.runNow("p1")

        assertEquals(resp, returned)
        val updated = repo.observeList().value.scans.first { it.project_name == "p1" }
        assertEquals("2026-04-11T12:30:00Z", updated.next_scan_at)
    }

    @Test
    fun runNow_failure_rollsBackList_andRethrows() = runTest {
        val (repo, restApi) = newRepo()
        val seeded = AutoScanListResponse(
            scans = listOf(
                instance("p1").copy(next_scan_at = "2026-04-11T12:00:00Z"),
            ),
            capacity = 1,
            max_scans = 5,
            available_projects = listOf("p1"),
        )
        coEvery { restApi.getAutoScanList() } returns seeded
        repo.refresh()
        coEvery { restApi.runAutoScanNow("p1") } throws RuntimeException("boom")

        try {
            repo.runNow("p1")
            fail("expected RuntimeException")
        } catch (_: RuntimeException) {
            // Expected.
        }

        // Rollback: original next_scan_at is back.
        val afterRollback = repo.observeList().value.scans.first { it.project_name == "p1" }
        assertEquals("2026-04-11T12:00:00Z", afterRollback.next_scan_at)
    }

    // ---- observeScans filtering ---------------------------------------------

    @Test
    fun observeScans_mapsListResponse_toScansOnly() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getAutoScanList() } returns AutoScanListResponse(
            scans = listOf(instance("p1"), instance("p2")),
            capacity = 2,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
        )

        repo.observeScans().test {
            // Initial empty state.
            assertEquals(emptyList<AutoScanInstanceDto>(), awaitItem())

            repo.refresh()

            val scans = awaitItem()
            assertEquals(2, scans.size)
            assertEquals("p1", scans[0].project_name)
            assertEquals("p2", scans[1].project_name)
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Event-bus-driven list refresh --------------------------------------

    @Test
    fun autoScanListChangedEvent_replacesInternalList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeList().test {
            // Initial empty state.
            val initial = awaitItem()
            assertTrue(initial.scans.isEmpty())

            val pushed = AutoScanListResponse(
                scans = listOf(instance("p1"), instance("p2")),
                capacity = 2,
                max_scans = 10,
                available_projects = listOf("p1", "p2", "p3"),
            )
            eventFlow.emit(AgentEvent.AutoScanListChanged(pushed))

            val updated = awaitItem()
            assertEquals(pushed, updated)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun autoScanStatusEvent_replacesMatchingInstance_inPlace() = runTest {
        val (repo, restApi, _, eventFlow) = newRepo()
        coEvery { restApi.getAutoScanList() } returns AutoScanListResponse(
            scans = listOf(
                instance("p1", status = "running"),
                instance("p2", status = "running"),
            ),
            capacity = 2,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
        )
        repo.refresh()

        repo.observeList().test {
            // Drop initial seeded state.
            awaitItem()

            val updatedInstance = instance("p1", status = "idle")
            eventFlow.emit(AgentEvent.AutoScanStatus(updatedInstance))

            val afterUpdate = awaitItem()
            assertEquals(2, afterUpdate.scans.size)
            assertEquals("idle", afterUpdate.scans.first { it.project_name == "p1" }.status)
            assertEquals("running", afterUpdate.scans.first { it.project_name == "p2" }.status)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun autoScanStatusEvent_forUnknownProject_leavesListUnchanged() = runTest {
        val (repo, restApi, _, eventFlow) = newRepo()
        val seeded = AutoScanListResponse(
            scans = listOf(instance("p1", status = "running")),
            capacity = 1,
            max_scans = 5,
            available_projects = listOf("p1", "p2"),
        )
        coEvery { restApi.getAutoScanList() } returns seeded
        repo.refresh()

        // Emit a status for a project that's not in the list.
        eventFlow.emit(AgentEvent.AutoScanStatus(instance("p2", status = "running")))

        // The `map { ... }` in the status handler leaves the list untouched
        // when no entry matches — still exactly one scan with the original
        // name/status.
        val list = repo.observeList().value
        assertEquals(1, list.scans.size)
        assertEquals("p1", list.scans[0].project_name)
        assertEquals("running", list.scans[0].status)
        assertFalse(list.scans.any { it.project_name == "p2" })
    }

    // ---- observeDetailFrames -----------------------------------------------

    @Test
    fun observeDetailFrames_delegatesToAutoScanWsClient_withSingleProjectSubscribe() {
        val (repo, _, _, _, wsClient) = newRepo()
        val stubFlow = kotlinx.coroutines.flow.emptyFlow<AutoScanWsClient.Frame>()
        every {
            wsClient.connect(subscribe = listOf("p1"), resumeFromSeq = emptyMap())
        } returns stubFlow

        val flow = repo.observeDetailFrames("p1")

        // Must forward the exact flow returned by the client — no wrapping.
        assertSame(stubFlow, flow)
        io.mockk.verify(exactly = 1) {
            wsClient.connect(subscribe = listOf("p1"), resumeFromSeq = emptyMap())
        }
    }

    @Test
    fun observeDetailFrames_forwardsResumeFromSeqMap() {
        val (repo, _, _, _, wsClient) = newRepo()
        val resume = mapOf("p1" to 99L)
        val stubFlow = kotlinx.coroutines.flow.emptyFlow<AutoScanWsClient.Frame>()
        every {
            wsClient.connect(subscribe = listOf("p1"), resumeFromSeq = resume)
        } returns stubFlow

        repo.observeDetailFrames("p1", resume)

        io.mockk.verify(exactly = 1) {
            wsClient.connect(subscribe = listOf("p1"), resumeFromSeq = resume)
        }
    }
}

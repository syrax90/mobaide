package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.data.network.dto.WorkspaceOrderRequest
import com.mobaide.app.data.network.dto.WorkspaceUpdate
import com.mobaide.app.data.network.dto.WorkspaceWrite
import com.mobaide.app.testing.awaitSubscribers
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [WorkspacesRepository] — one-shot REST fetch, initial empty
 * state, and event-bus-driven refresh on [AgentEvent.WorkspacesChanged].
 *
 * Collaborators are mocked. [AgentEventBus.events] is stubbed to a real
 * [MutableSharedFlow] so that `eventBus.stream<T>()` (an inline function that
 * expands to `events.filterIsInstance()`) works without needing a real bus.
 * The repository launches its event collector on `Dispatchers.Default` via an
 * internal [kotlinx.coroutines.CoroutineScope], so the fixture waits for its
 * subscription before a test emits (a replay-0 emit would otherwise be lost).
 */
class WorkspacesRepositoryTest {

    private fun workspace(name: String, path: String = "/projects/$name"): WorkspaceInfo =
        WorkspaceInfo(name = name, path = path)

    private fun newRepo(): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        val repo = WorkspacesRepository(restApi, bus)
        eventFlow.awaitSubscribers()
        return Fixture(repo, restApi, bus, eventFlow)
    }

    private data class Fixture(
        val repo: WorkspacesRepository,
        val restApi: RestApi,
        val bus: AgentEventBus,
        val eventFlow: MutableSharedFlow<AgentEvent>,
    )

    // ---- one-shot REST fetch ------------------------------------------------

    @Test
    fun listWorkspaces_doesNotMutateObserveWorkspacesState() = runTest {
        // One-shot fetch is a fallback seed — it does NOT push into the
        // internal MutableStateFlow. Only WorkspacesChanged events do that.
        val (repo, restApi) = newRepo()
        coEvery { restApi.getWorkspaces() } returns listOf(workspace("alpha"))

        repo.listWorkspaces()

        assertTrue(
            "listWorkspaces must not update the observeWorkspaces() state",
            repo.observeWorkspaces().value.isEmpty(),
        )
    }

    // ---- event-bus-driven refresh -------------------------------------------

    @Test
    fun workspacesChangedEvent_replacesInternalList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeWorkspaces().test {
            // Drop initial empty state.
            assertEquals(emptyList<WorkspaceInfo>(), awaitItem())

            val pushed = listOf(workspace("alpha"), workspace("beta"))
            eventFlow.emit(AgentEvent.WorkspacesChanged(pushed))

            assertEquals(pushed, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- mutations: conflict mapping ---------------------------------------

    @Test
    fun createWorkspace_409_mapsToWorkspaceConflictException() = runTest {
        val (repo, restApi) = newRepo()
        val body = WorkspaceWrite(name = "alpha", path = "/p/alpha")
        coEvery { restApi.createWorkspace(body) } throws ApiException(
            code = 409,
            errorBody = "{\"detail\":\"Workspace 'alpha' already exists\"}",
        )

        try {
            repo.createWorkspace(body)
            org.junit.Assert.fail("expected WorkspaceConflictException")
        } catch (e: WorkspaceConflictException) {
            assertEquals("Workspace 'alpha' already exists", e.detail)
        }
    }

    @Test
    fun updateWorkspace_nonConflict_errorPassesThrough() = runTest {
        val (repo, restApi) = newRepo()
        val body = WorkspaceUpdate(path = "/new/path")
        coEvery { restApi.updateWorkspace("alpha", body) } throws ApiException(
            code = 404,
            errorBody = "{\"detail\":\"Workspace 'alpha' not found\"}",
        )

        try {
            repo.updateWorkspace("alpha", body)
            org.junit.Assert.fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(404, e.code)
        }
    }

    @Test
    fun reorderWorkspaces_forwardsNamesAsRequest() = runTest {
        val (repo, restApi) = newRepo()
        val captured = slot<WorkspaceOrderRequest>()
        coEvery { restApi.reorderWorkspaces(capture(captured)) } returns emptyList()

        repo.reorderWorkspaces(listOf("beta", "alpha"))
        assertEquals(listOf("beta", "alpha"), captured.captured.names)
    }

    @Test
    fun parseDetail_invalidJson_fallsBackToRawBody() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.createWorkspace(any()) } throws ApiException(
            code = 409,
            errorBody = "plain text body",
        )
        try {
            repo.createWorkspace(WorkspaceWrite(name = "a", path = "/p"))
            org.junit.Assert.fail("expected WorkspaceConflictException")
        } catch (e: WorkspaceConflictException) {
            assertEquals("plain text body", e.detail)
        }
    }
}

package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.testing.awaitSubscribers
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [GitRepository].
 *
 * Collaborators are mocked with MockK; [AgentEventBus.events] is stubbed to a
 * real [MutableSharedFlow] so the inline `eventBus.stream<T>()` (which expands
 * to `events.filterIsInstance()`) works without needing a real bus. The
 * repository launches its event collector on `Dispatchers.Default`, so
 * event-driven tests rely on Turbine's default timeout for cross-thread
 * synchronisation — same pattern as `ProjectsRepositoryTest`.
 */
class GitRepositoryTest {

    private data class Fixture(
        val repo: GitRepository,
        val restApi: RestApi,
        val bus: AgentEventBus,
        val eventFlow: MutableSharedFlow<AgentEvent>,
    )

    private fun newRepo(): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        val repo = GitRepository(restApi, bus)
        eventFlow.awaitSubscribers()
        return Fixture(repo, restApi, bus, eventFlow)
    }

    @Test
    fun deleteGitItems_409_mapsToGitBusyException_withParsedHolders() = runTest {
        val (repo, restApi) = newRepo()
        val items = listOf(GitDeleteItem(
            kind = GitItem.KIND_BRANCH_WITH_WORKTREE,
            branch = "topic",
            worktree_path = "/repo/wt/topic",
        ))
        // FastAPI shape: {"detail": {"busy_by": [...]}}
        val body = """
            {"detail":{"busy_by":[
              {"kind":"session","session_id":"s-1","cwd":"/repo/wt/topic"},
              {"kind":"auto_scan","project_name":"alpha","worktree_path":"/repo/wt/topic"}
            ]}}
        """.trimIndent()
        coEvery { restApi.deleteGitItems("alpha", any()) } throws ApiException(409, body)

        try {
            repo.deleteGitItems("alpha", items)
            org.junit.Assert.fail("expected GitBusyException")
        } catch (e: GitBusyException) {
            assertEquals(2, e.busyBy.size)
            assertEquals("session", e.busyBy[0].kind)
            assertEquals("s-1", e.busyBy[0].session_id)
            assertEquals("auto_scan", e.busyBy[1].kind)
            assertEquals("alpha", e.busyBy[1].project_name)
        }
    }

    @Test
    fun deleteGitItems_409_malformedBody_mapsToBusyException_withEmptyHolders() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.deleteGitItems("alpha", any()) } throws ApiException(409, "not json {")

        try {
            repo.deleteGitItems("alpha", emptyList())
            org.junit.Assert.fail("expected GitBusyException")
        } catch (e: GitBusyException) {
            assertTrue(e.busyBy.isEmpty())
        }
    }

    @Test
    fun deleteGitItems_nonConflict_apiErrorPropagatesUnchanged() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.deleteGitItems("alpha", any()) } throws
            ApiException(500, """{"detail":"boom"}""")

        try {
            repo.deleteGitItems("alpha", emptyList())
            org.junit.Assert.fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(500, e.code)
        } catch (e: GitBusyException) {
            org.junit.Assert.fail("non-409 must NOT map to GitBusyException")
        }
    }

    @Test
    fun gitChangedEvent_emitsProjectName_onChangedFlow() = runTest {
        val (repo, _, _, eventFlow) = newRepo()
        repo.changed.test {
            eventFlow.emit(AgentEvent.GitChanged("alpha"))
            assertEquals("alpha", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }
}

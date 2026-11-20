package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteJobResponse
import com.mobaide.app.data.network.dto.GitDeleteResultItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.testing.awaitSubscribers
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.time.TimeSource

/**
 * Unit tests for [GitRepository].
 *
 * Collaborators are mocked with MockK; [AgentEventBus.events] is stubbed to a
 * real [MutableSharedFlow] so the inline `eventBus.stream<T>()` (which expands
 * to `events.filterIsInstance()`) works without needing a real bus. The
 * repository launches its event collector on `Dispatchers.Default`, so
 * event-driven tests rely on Turbine's default timeout for cross-thread
 * synchronisation — same pattern as `WorkspacesRepositoryTest`.
 */
class GitRepositoryTest {

    private data class Fixture(
        val repo: GitRepository,
        val restApi: RestApi,
        val bus: AgentEventBus,
        val eventFlow: MutableSharedFlow<AgentEvent>,
    )

    private fun newRepo(timeSource: TimeSource = TimeSource.Monotonic): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        val repo = GitRepository(restApi, bus, timeSource)
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
            assertEquals("alpha", e.busyBy[1].workspace_name)
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

    private val deleteBatch = listOf(
        GitDeleteItem(kind = GitItem.KIND_BRANCH_WITH_WORKTREE, branch = "topic", worktree_path = "/wt/topic"),
        GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"),
    )

    private fun job(completed: Int = 0, results: List<GitDeleteResultItem>? = null) = GitDeleteJobResponse(
        job_id = "j1",
        status = if (results == null) "running" else "done",
        total = 2,
        completed = completed,
        results = results,
    )

    private val doneResults = listOf(
        GitDeleteResultItem(kind = GitItem.KIND_BRANCH_WITH_WORKTREE, target = "topic", ok = true),
        GitDeleteResultItem(kind = GitItem.KIND_BRANCH_ONLY, target = "stale", ok = false, error = "force_required"),
    )

    @Test
    fun deleteGitItems_withJobs_pollsThroughATransientFailure_untilDone() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startGitDeleteJob("alpha", any()) } returns job()
        coEvery { restApi.getGitDeleteJob("alpha", "j1") } throws
            ApiException(503, "agent unreachable") andThen job(completed = 1) andThen
            job(completed = 2, results = doneResults)
        val progress = mutableListOf<Pair<Int, Int>>()

        val response = repo.deleteGitItems("alpha", deleteBatch, useJobs = true) { completed, total ->
            progress += completed to total
        }

        assertEquals(doneResults, response.results)
        assertTrue(progress.contains(1 to 2))
        assertEquals(2 to 2, progress.last())
    }

    @Test
    fun deleteGitItems_withJobs_aForgottenJob_failsAtOnce() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startGitDeleteJob("alpha", any()) } returns job()
        coEvery { restApi.getGitDeleteJob("alpha", "j1") } throws
            ApiException(404, """{"detail":{"code":"job_not_found"}}""") andThen
            job(completed = 2, results = doneResults)

        try {
            repo.deleteGitItems("alpha", deleteBatch, useJobs = true)
            org.junit.Assert.fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(404, e.code)
        }
    }

    // Polls in a row that fit inside the give-up budget, with no success between them.
    private val pollsInBudget =
        (GitRepository.DELETE_JOB_GIVE_UP_AFTER / GitRepository.DELETE_JOB_POLL_INTERVAL).toInt()

    @Test
    fun deleteGitItems_withJobs_outagesShorterThanTheBudget_restartedByEachSuccess_stillDeliverResults() = runTest {
        val (repo, restApi) = newRepo(testScheduler.timeSource)
        coEvery { restApi.startGitDeleteJob("alpha", any()) } returns job()
        val outage = List(pollsInBudget - 1) { ApiException(503, "down") }
        coEvery { restApi.getGitDeleteJob("alpha", "j1") } throwsMany outage andThen
            job(completed = 1) andThenThrowsMany outage andThen job(completed = 2, results = doneResults)

        val response = repo.deleteGitItems("alpha", deleteBatch, useJobs = true)

        assertEquals(doneResults, response.results)
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun deleteGitItems_withJobs_givesUpOnceNoPollSucceededForTheWholeBudget() = runTest {
        val (repo, restApi) = newRepo(testScheduler.timeSource)
        coEvery { restApi.startGitDeleteJob("alpha", any()) } returns job()
        coEvery { restApi.getGitDeleteJob("alpha", "j1") } throws ApiException(503, "down")

        try {
            repo.deleteGitItems("alpha", deleteBatch, useJobs = true)
            org.junit.Assert.fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(503, e.code)
        }
        assertEquals(GitRepository.DELETE_JOB_GIVE_UP_AFTER.inWholeMilliseconds, testScheduler.currentTime)
    }

    @Test
    fun deleteGitItems_withJobs_codedTooManyJobs409_isNotTheBusyGuard() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.startGitDeleteJob("alpha", any()) } throws
            ApiException(409, """{"detail":{"code":"too_many_jobs","message":""}}""")

        try {
            repo.deleteGitItems("alpha", deleteBatch, useJobs = true)
            org.junit.Assert.fail("expected ApiException")
        } catch (e: GitBusyException) {
            org.junit.Assert.fail("a coded 409 must NOT map to GitBusyException")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
        }
    }

    @Test
    fun gitChangedEvent_emitsWorkspaceName_onChangedFlow() = runTest {
        val (repo, _, _, eventFlow) = newRepo()
        repo.changed.test {
            eventFlow.emit(AgentEvent.GitChanged("alpha"))
            assertEquals("alpha", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }
}

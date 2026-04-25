package com.claudemobile.app.data.repository

import app.cash.turbine.test
import com.claudemobile.app.data.events.AgentEvent
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.BusyByResponse
import com.claudemobile.app.data.network.dto.BusyHolder
import com.claudemobile.app.data.network.dto.GitDeleteItem
import com.claudemobile.app.data.network.dto.GitDeleteRequest
import com.claudemobile.app.data.network.dto.GitDeleteResponse
import com.claudemobile.app.data.network.dto.GitDeleteResultItem
import com.claudemobile.app.data.network.dto.GitItem
import com.claudemobile.app.data.network.dto.GitItemsResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
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

    private fun item(branch: String, kind: String = GitItem.KIND_BRANCH_ONLY): GitItem =
        GitItem(kind = kind, branch = branch)

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
        return Fixture(repo, restApi, bus, eventFlow)
    }

    @Test
    fun listGitItems_unwrapsResponseToItems() = runTest {
        val (repo, restApi) = newRepo()
        val items = listOf(item("main"), item("topic"))
        coEvery { restApi.getGitItems("alpha") } returns GitItemsResponse(items = items)

        val returned = repo.listGitItems("alpha")
        assertSame(items, returned)
        coVerify(exactly = 1) { restApi.getGitItems("alpha") }
    }

    @Test
    fun listGitItems_emptyResponse_returnsEmptyList() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getGitItems("alpha") } returns GitItemsResponse(items = emptyList())
        assertTrue(repo.listGitItems("alpha").isEmpty())
    }

    @Test
    fun listGitItems_propagatesRestApiException() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getGitItems("alpha") } throws RuntimeException("net down")
        try {
            repo.listGitItems("alpha")
            org.junit.Assert.fail("expected RuntimeException")
        } catch (e: RuntimeException) {
            assertEquals("net down", e.message)
        }
    }

    @Test
    fun deleteGitItems_wrapsItemsInRequestAndForwardsResponse() = runTest {
        val (repo, restApi) = newRepo()
        val items = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"))
        val response = GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_BRANCH_ONLY, target = "stale", ok = true,
                ),
            ),
        )
        coEvery { restApi.deleteGitItems("alpha", GitDeleteRequest(items)) } returns response

        val returned = repo.deleteGitItems("alpha", items)
        assertSame(response, returned)
        coVerify(exactly = 1) { restApi.deleteGitItems("alpha", GitDeleteRequest(items)) }
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
    fun deleteGitItems_409_emptyBody_mapsToBusyException_withEmptyHolders() = runTest {
        // Defensive: a 409 with no body should still surface as GitBusyException
        // (the screen will fall back to a generic "busy" message).
        val (repo, restApi) = newRepo()
        coEvery { restApi.deleteGitItems("alpha", any()) } throws ApiException(409, "")

        try {
            repo.deleteGitItems("alpha", emptyList())
            org.junit.Assert.fail("expected GitBusyException")
        } catch (e: GitBusyException) {
            assertTrue(e.busyBy.isEmpty())
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

    @Test
    fun unrelatedAgentEvent_doesNotEmitOnChangedFlow() = runTest {
        val (repo, _, _, eventFlow) = newRepo()
        repo.changed.test {
            eventFlow.emit(AgentEvent.SessionsChanged(emptyList()))
            // No git change → the changed flow must stay silent.
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun multipleGitChangedEmissions_eachReachChangedFlow() = runTest {
        val (repo, _, _, eventFlow) = newRepo()
        repo.changed.test {
            eventFlow.emit(AgentEvent.GitChanged("alpha"))
            assertEquals("alpha", awaitItem())
            eventFlow.emit(AgentEvent.GitChanged("beta"))
            assertEquals("beta", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun parseBusyBy_acceptsMissingOptionalFields() = runTest {
        // The wire shape uses optional fields per holder kind; the parser must
        // not throw when only the kind-specific subset is populated.
        val (repo, restApi) = newRepo()
        val body = """
            {"detail":{"busy_by":[{"kind":"session","session_id":"s-only"}]}}
        """.trimIndent()
        coEvery { restApi.deleteGitItems("alpha", any()) } throws ApiException(409, body)

        try {
            repo.deleteGitItems("alpha", emptyList())
            org.junit.Assert.fail("expected GitBusyException")
        } catch (e: GitBusyException) {
            assertEquals(1, e.busyBy.size)
            val holder: BusyHolder = e.busyBy[0]
            assertEquals("session", holder.kind)
            assertEquals("s-only", holder.session_id)
            // Other optional fields default to null without crashing.
            org.junit.Assert.assertNull(holder.project_name)
            org.junit.Assert.assertNull(holder.cwd)
            org.junit.Assert.assertNull(holder.worktree_path)
        }
    }

    // Compile-time spot-check: BusyByResponse keeps its surface as expected.
    @Test
    fun busyByResponse_dtoShape_isStable() {
        val r = BusyByResponse(busy_by = listOf(BusyHolder(kind = "auto_scan")))
        assertEquals(1, r.busy_by.size)
        assertEquals("auto_scan", r.busy_by[0].kind)
    }
}

package com.claudemobile.app.data.repository

import app.cash.turbine.test
import com.claudemobile.app.data.events.AgentEvent
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ProjectInfo
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
 * Unit tests for [ProjectsRepository] — one-shot REST fetch, initial empty
 * state, and event-bus-driven refresh on [AgentEvent.ProjectsChanged].
 *
 * Collaborators are mocked. [AgentEventBus.events] is stubbed to a real
 * [MutableSharedFlow] so that `eventBus.stream<T>()` (an inline function that
 * expands to `events.filterIsInstance()`) works without needing a real bus.
 * The repository launches its event collector on `Dispatchers.Default` via an
 * internal [kotlinx.coroutines.CoroutineScope], so event-driven tests rely on
 * Turbine's default timeout for cross-thread synchronisation.
 */
class ProjectsRepositoryTest {

    private fun project(name: String, path: String = "/projects/$name"): ProjectInfo =
        ProjectInfo(name = name, path = path)

    private fun newRepo(): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        val repo = ProjectsRepository(restApi, bus)
        return Fixture(repo, restApi, bus, eventFlow)
    }

    private data class Fixture(
        val repo: ProjectsRepository,
        val restApi: RestApi,
        val bus: AgentEventBus,
        val eventFlow: MutableSharedFlow<AgentEvent>,
    )

    // ---- initial state ------------------------------------------------------

    @Test
    fun observeProjects_initialState_isEmptyList() = runTest {
        val (repo) = newRepo()
        assertTrue(repo.observeProjects().value.isEmpty())
    }

    @Test
    fun observeProjects_initialFlowEmission_isEmptyList() = runTest {
        val (repo) = newRepo()
        repo.observeProjects().test {
            assertEquals(emptyList<ProjectInfo>(), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- one-shot REST fetch ------------------------------------------------

    @Test
    fun listProjects_delegatesToRestApi_andReturnsSamePayload() = runTest {
        val (repo, restApi) = newRepo()
        val payload = listOf(project("alpha"), project("beta"))
        coEvery { restApi.getProjects() } returns payload

        val returned = repo.listProjects()

        assertSame(
            "listProjects must forward RestApi.getProjects() verbatim",
            payload,
            returned,
        )
        coVerify(exactly = 1) { restApi.getProjects() }
    }

    @Test
    fun listProjects_doesNotMutateObserveProjectsState() = runTest {
        // One-shot fetch is a fallback seed — it does NOT push into the
        // internal MutableStateFlow. Only ProjectsChanged events do that.
        val (repo, restApi) = newRepo()
        coEvery { restApi.getProjects() } returns listOf(project("alpha"))

        repo.listProjects()

        assertTrue(
            "listProjects must not update the observeProjects() state",
            repo.observeProjects().value.isEmpty(),
        )
    }

    @Test
    fun listProjects_propagatesThrowable_fromRestApi() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getProjects() } throws RuntimeException("network down")

        try {
            repo.listProjects()
            org.junit.Assert.fail("expected RuntimeException")
        } catch (e: RuntimeException) {
            assertEquals("network down", e.message)
        }
    }

    // ---- event-bus-driven refresh -------------------------------------------

    @Test
    fun projectsChangedEvent_replacesInternalList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeProjects().test {
            // Drop initial empty state.
            assertEquals(emptyList<ProjectInfo>(), awaitItem())

            val pushed = listOf(project("alpha"), project("beta"))
            eventFlow.emit(AgentEvent.ProjectsChanged(pushed))

            assertEquals(pushed, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun projectsChangedEvent_emptyPayload_clearsTheList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeProjects().test {
            // Drop initial empty state.
            awaitItem()

            // First push a non-empty list.
            eventFlow.emit(AgentEvent.ProjectsChanged(listOf(project("alpha"))))
            assertEquals(1, awaitItem().size)

            // Then clear it.
            eventFlow.emit(AgentEvent.ProjectsChanged(emptyList()))
            assertEquals(emptyList<ProjectInfo>(), awaitItem())

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun projectsChangedEvent_secondEmission_fullyReplacesPriorList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeProjects().test {
            awaitItem()

            eventFlow.emit(AgentEvent.ProjectsChanged(listOf(project("alpha"), project("beta"))))
            assertEquals(2, awaitItem().size)

            // Different, overlapping-by-name set — must be a full replace, not a merge.
            val replacement = listOf(project("beta"), project("gamma"))
            eventFlow.emit(AgentEvent.ProjectsChanged(replacement))
            val after = awaitItem()
            assertEquals(replacement, after)
            assertEquals(listOf("beta", "gamma"), after.map { it.name })

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun unrelatedAgentEvent_doesNotMutateProjectsState() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeProjects().test {
            awaitItem() // initial empty

            // Seed with a known list so we can detect spurious mutations.
            eventFlow.emit(AgentEvent.ProjectsChanged(listOf(project("alpha"))))
            val seeded = awaitItem()
            assertEquals(listOf("alpha"), seeded.map { it.name })

            // Emit a SessionsChanged event — must not touch the projects flow.
            eventFlow.emit(AgentEvent.SessionsChanged(emptyList()))

            // No new emission from observeProjects().
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }
}

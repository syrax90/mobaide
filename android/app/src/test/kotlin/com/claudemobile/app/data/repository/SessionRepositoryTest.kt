package com.claudemobile.app.data.repository

import app.cash.turbine.test
import com.claudemobile.app.data.events.ServerEvent
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.network.dto.CreateSessionRequest
import com.claudemobile.app.data.network.dto.ScrollbackResponse
import com.claudemobile.app.data.network.dto.SessionResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [SessionRepository] — exercises the [ServerEvent]-driven
 * list-merging logic ([ServerEvent.SessionsChanged] full replace,
 * [ServerEvent.SessionUpdated] add-if-new / replace-if-existing by id),
 * REST delegation, and [WebSocketManager.createConnection] path construction
 * for per-session I/O connections.
 *
 * Collaborators are mocked. [ServerEventBus.events] is stubbed to a real
 * [MutableSharedFlow] so that `eventBus.stream<T>()` (an inline function that
 * expands to `events.filterIsInstance()`) works without needing a real bus.
 * The repository launches its event collectors on `Dispatchers.Default` via an
 * internal [kotlinx.coroutines.CoroutineScope], so event-driven tests rely on
 * Turbine's default timeout for cross-thread synchronisation.
 */
class SessionRepositoryTest {

    private fun session(
        id: String,
        state: String = "running",
        createdAt: String = "2026-04-11T12:00:00Z",
    ): SessionResponse = SessionResponse(
        session_id = id,
        state = state,
        created_at = createdAt,
    )

    private fun newRepo(): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<ServerEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<ServerEventBus>()
        every { bus.events } returns eventFlow
        val wsManager = mockk<WebSocketManager>(relaxed = true)
        val repo = SessionRepository(restApi, wsManager, bus)
        return Fixture(repo, restApi, bus, eventFlow, wsManager)
    }

    private data class Fixture(
        val repo: SessionRepository,
        val restApi: RestApi,
        val bus: ServerEventBus,
        val eventFlow: MutableSharedFlow<ServerEvent>,
        val wsManager: WebSocketManager,
    )

    // ---- observeSessionList initial state -----------------------------------

    @Test
    fun observeSessionList_initialState_isEmptyList() = runTest {
        val (repo) = newRepo()
        assertTrue(repo.observeSessionList().value.isEmpty())
    }

    @Test
    fun observeSessionList_initialFlowEmission_isEmptyList() = runTest {
        val (repo) = newRepo()
        repo.observeSessionList().test {
            assertEquals(emptyList<SessionResponse>(), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- REST delegation ----------------------------------------------------

    @Test
    fun createSession_delegatesToRestApi_andReturnsSamePayload() = runTest {
        val (repo, restApi) = newRepo()
        val request = CreateSessionRequest(command = listOf("bash"))
        val response = session("s-1")
        coEvery { restApi.createSession(request) } returns response

        val returned = repo.createSession(request)

        assertSame(response, returned)
        coVerify(exactly = 1) { restApi.createSession(request) }
    }

    @Test
    fun listSessions_delegatesToRestApi() = runTest {
        val (repo, restApi) = newRepo()
        val payload = listOf(session("s-1"), session("s-2"))
        coEvery { restApi.listSessions() } returns payload

        val returned = repo.listSessions()

        assertSame(payload, returned)
        coVerify(exactly = 1) { restApi.listSessions() }
    }

    @Test
    fun deleteSession_forwardsForceFlag_toRestApi() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.deleteSession("s-1", true) } returns Unit

        repo.deleteSession("s-1", force = true)

        coVerify(exactly = 1) { restApi.deleteSession("s-1", true) }
    }

    @Test
    fun deleteSession_defaultsForceToFalse() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.deleteSession("s-1", false) } returns Unit

        repo.deleteSession("s-1")

        coVerify(exactly = 1) { restApi.deleteSession("s-1", false) }
    }

    @Test
    fun getScrollback_delegatesToRestApi_withDefaultLineCount() = runTest {
        val (repo, restApi) = newRepo()
        val payload = ScrollbackResponse(
            session_id = "s-1",
            lines = listOf("line1", "line2"),
            total = 2,
        )
        coEvery { restApi.getScrollback("s-1", 1000) } returns payload

        val returned = repo.getScrollback("s-1")

        assertSame(payload, returned)
        coVerify(exactly = 1) { restApi.getScrollback("s-1", 1000) }
    }

    @Test
    fun getScrollback_forwardsCustomLineCount() = runTest {
        val (repo, restApi) = newRepo()
        val payload = ScrollbackResponse(session_id = "s-1", lines = emptyList(), total = 0)
        coEvery { restApi.getScrollback("s-1", 42) } returns payload

        repo.getScrollback("s-1", lines = 42)

        coVerify(exactly = 1) { restApi.getScrollback("s-1", 42) }
    }

    // ---- SessionsChanged: full replace --------------------------------------

    @Test
    fun sessionsChangedEvent_replacesInternalList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            assertEquals(emptyList<SessionResponse>(), awaitItem())

            val pushed = listOf(session("s-1"), session("s-2"))
            eventFlow.emit(ServerEvent.SessionsChanged(pushed))

            assertEquals(pushed, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionsChangedEvent_secondEmission_fullyReplacesPriorList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            awaitItem() // initial empty

            eventFlow.emit(ServerEvent.SessionsChanged(listOf(session("s-1"), session("s-2"))))
            assertEquals(2, awaitItem().size)

            // Full replace — the old s-1 / s-2 must not leak through.
            val replacement = listOf(session("s-3"))
            eventFlow.emit(ServerEvent.SessionsChanged(replacement))
            val after = awaitItem()
            assertEquals(replacement, after)
            assertEquals(listOf("s-3"), after.map { it.session_id })

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionsChangedEvent_emptyPayload_clearsTheList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            awaitItem() // initial empty

            eventFlow.emit(ServerEvent.SessionsChanged(listOf(session("s-1"))))
            assertEquals(1, awaitItem().size)

            eventFlow.emit(ServerEvent.SessionsChanged(emptyList()))
            assertEquals(emptyList<SessionResponse>(), awaitItem())

            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- SessionUpdated: add-if-new / replace-if-existing -------------------

    @Test
    fun sessionUpdatedEvent_addsNewSession_whenIdNotInList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            awaitItem() // initial empty

            // Seed the list via SessionsChanged.
            eventFlow.emit(ServerEvent.SessionsChanged(listOf(session("s-1"))))
            assertEquals(listOf("s-1"), awaitItem().map { it.session_id })

            // Update for a brand-new id → append.
            eventFlow.emit(ServerEvent.SessionUpdated(session("s-2")))
            val after = awaitItem()
            assertEquals(2, after.size)
            assertEquals(listOf("s-1", "s-2"), after.map { it.session_id })

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionUpdatedEvent_replacesExistingSession_byId_inPlace() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            awaitItem() // initial empty

            // Seed the list via SessionsChanged with two sessions.
            eventFlow.emit(
                ServerEvent.SessionsChanged(
                    listOf(
                        session("s-1", state = "running"),
                        session("s-2", state = "running"),
                    )
                )
            )
            assertEquals(2, awaitItem().size)

            // Update s-1 → same id, new state. Must replace in place (not duplicate).
            val updated = session("s-1", state = "stopped")
            eventFlow.emit(ServerEvent.SessionUpdated(updated))
            val after = awaitItem()
            assertEquals(
                "replacement must preserve size and order",
                listOf("s-1", "s-2"),
                after.map { it.session_id },
            )
            assertEquals("stopped", after.first { it.session_id == "s-1" }.state)
            assertEquals("running", after.first { it.session_id == "s-2" }.state)

            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionUpdatedEvent_onEmptyList_appendsTheSession() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            awaitItem() // initial empty

            // Update without a prior SessionsChanged — starts from empty list.
            eventFlow.emit(ServerEvent.SessionUpdated(session("s-7")))
            val after = awaitItem()
            assertEquals(1, after.size)
            assertEquals("s-7", after[0].session_id)

            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- createSessionIoConnection: path construction -----------------------

    @Test
    fun createSessionIoConnection_usesCorrectWsPath_forSessionIo() {
        val (repo, _, _, _, wsManager) = newRepo()
        val stub = mockk<WebSocketConnection>(relaxed = true)
        every { wsManager.createConnection("ws/session/abc-123") } returns stub

        val result = repo.createSessionIoConnection("abc-123")

        assertSame(
            "must forward the WebSocketConnection returned by the manager",
            stub,
            result,
        )
        verify(exactly = 1) { wsManager.createConnection("ws/session/abc-123") }
    }

    @Test
    fun createSessionIoConnection_embedsRawSessionId_inPath_withoutTrimOrEncoding() {
        // The repository delegates the path as-is; the manager is responsible for
        // any URL hygiene. This test pins that contract so a future refactor that
        // silently normalises the session id is caught.
        val (repo, _, _, _, wsManager) = newRepo()
        val stub = mockk<WebSocketConnection>(relaxed = true)
        every { wsManager.createConnection(any()) } returns stub

        repo.createSessionIoConnection("weird id /with slash")

        verify(exactly = 1) {
            wsManager.createConnection("ws/session/weird id /with slash")
        }
    }
}

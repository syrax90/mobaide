package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.testing.awaitSubscribers
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [SessionRepository] — exercises the [AgentEvent]-driven
 * list-merging logic ([AgentEvent.SessionsChanged] full replace,
 * [AgentEvent.SessionUpdated] add-if-new / replace-if-existing by id),
 * create-time cache seeding, and [WebSocketManager.createConnection] path construction
 * for per-session I/O connections.
 *
 * Collaborators are mocked. [AgentEventBus.events] is stubbed to a real
 * [MutableSharedFlow] so that `eventBus.stream<T>()` (an inline function that
 * expands to `events.filterIsInstance()`) works without needing a real bus.
 * The repository launches its event collectors on `Dispatchers.Default` via an
 * internal [kotlinx.coroutines.CoroutineScope], so the fixture waits for its
 * subscription before a test emits (a replay-0 emit would otherwise be lost).
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

    private fun newRepo(
        initialConnection: AgentEventBus.ConnState = AgentEventBus.ConnState.Connected,
    ): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val connectionFlow = MutableStateFlow(initialConnection)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        every { bus.connection } returns connectionFlow
        val wsManager = mockk<WebSocketManager>(relaxed = true)
        val repo = SessionRepository(restApi, wsManager, bus)
        eventFlow.awaitSubscribers(2)
        connectionFlow.awaitSubscribers()
        return Fixture(repo, restApi, bus, eventFlow, wsManager, connectionFlow)
    }

    private data class Fixture(
        val repo: SessionRepository,
        val restApi: RestApi,
        val bus: AgentEventBus,
        val eventFlow: MutableSharedFlow<AgentEvent>,
        val wsManager: WebSocketManager,
        val connectionFlow: MutableStateFlow<AgentEventBus.ConnState>,
    )

    // ---- SessionsChanged: full replace --------------------------------------

    @Test
    fun sessionsChangedEvent_secondEmission_fullyReplacesPriorList() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionList().test {
            awaitItem() // initial empty

            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1"), session("s-2"))))
            assertEquals(2, awaitItem().size)

            // Full replace — the old s-1 / s-2 must not leak through.
            val replacement = listOf(session("s-3"))
            eventFlow.emit(AgentEvent.SessionsChanged(replacement))
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

            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1"))))
            assertEquals(1, awaitItem().size)

            eventFlow.emit(AgentEvent.SessionsChanged(emptyList()))
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
            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1"))))
            assertEquals(listOf("s-1"), awaitItem().map { it.session_id })

            // Update for a brand-new id → append.
            eventFlow.emit(AgentEvent.SessionUpdated(session("s-2")))
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
                AgentEvent.SessionsChanged(
                    listOf(
                        session("s-1", state = "running"),
                        session("s-2", state = "running"),
                    )
                )
            )
            assertEquals(2, awaitItem().size)

            // Update s-1 → same id, new state. Must replace in place (not duplicate).
            val updated = session("s-1", state = "stopped")
            eventFlow.emit(AgentEvent.SessionUpdated(updated))
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
    fun sessionsSynced_isFalse_untilASnapshotArrives() = runTest {
        val (repo, _, _, eventFlow) = newRepo()

        repo.observeSessionsSynced().test {
            assertFalse(
                "absence from a list nobody pushed yet is not evidence of anything",
                awaitItem(),
            )

            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1"))))

            assertTrue(awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionsSynced_isClearedOnDisconnect_whileTheListIsKept() = runTest {
        val (repo, _, _, eventFlow, _, connectionFlow) = newRepo()

        repo.observeSessionsSynced().test {
            assertFalse(awaitItem())
            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1"))))
            assertTrue(awaitItem())

            connectionFlow.value = AgentEventBus.ConnState.Disconnected

            assertFalse(
                "a list from a dropped tunnel can still show sessions the agent " +
                    "lost when it restarted",
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
        assertEquals(listOf("s-1"), repo.observeSessionList().value.map { it.session_id })
    }

    @Test
    fun sessionsSynced_staysFalseOnReconnect_untilTheNextSnapshot() = runTest {
        val (repo, _, _, eventFlow, _, connectionFlow) = newRepo()

        repo.observeSessionsSynced().test {
            assertFalse(awaitItem())
            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1"))))
            assertTrue(awaitItem())

            connectionFlow.value = AgentEventBus.ConnState.Connecting
            assertFalse(awaitItem())

            // The socket being open again proves nothing: the agent replays its
            // snapshot right after, and only that frame is authoritative.
            connectionFlow.value = AgentEventBus.ConnState.Connected
            expectNoEvents()

            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-2"))))
            assertTrue(awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun createSession_insertsTheCreatedSession_intoTheList() = runTest {
        val (repo, restApi) = newRepo()
        val request = CreateSessionRequest(command = listOf("bash"))
        coEvery { restApi.createSession(request) } returns session("s-new")

        repo.createSession(request)

        // Without this the create→navigate window reads as "not in the list",
        // which the terminal treats as proof the session is gone.
        assertEquals(
            listOf("s-new"),
            repo.observeSessionList().value.map { it.session_id },
        )
    }

    @Test
    fun createSession_replacesTheRow_whenTheSnapshotAlreadyCarriesTheId() = runTest {
        val (repo, restApi, _, eventFlow) = newRepo()
        val request = CreateSessionRequest(command = listOf("bash"))
        coEvery { restApi.createSession(request) } returns session("s-1", state = "running")

        repo.observeSessionList().test {
            awaitItem()
            eventFlow.emit(AgentEvent.SessionsChanged(listOf(session("s-1", state = "starting"))))
            assertEquals(1, awaitItem().size)

            repo.createSession(request)

            val after = awaitItem()
            assertEquals("insert must be idempotent per id", 1, after.size)
            assertEquals("running", after[0].state)
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

}

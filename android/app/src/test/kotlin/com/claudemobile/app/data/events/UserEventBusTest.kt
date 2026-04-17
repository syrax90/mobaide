package com.claudemobile.app.data.events

import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.UserIdentityStore
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.repository.AgentRepository
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import okio.ByteString
import org.junit.Test

/**
 * Unit tests for [UserEventBus] — the user-scoped sister of [AgentEventBus].
 *
 * Focuses on behavior unique to the user-events path:
 *   - `agent.status_changed` envelopes are forwarded to
 *     [AgentRepository.updateAgentOnline] with the correct args.
 *   - Malformed envelopes / unknown types / wrong-typed payloads are
 *     silently dropped and do not crash the collector.
 *   - `WsEvent.Connected` triggers `refreshAgents()` (reconnect resync).
 *   - `WsEvent.Binary` is ignored (channel is text-only).
 *
 * The connection state machine and auth-lifecycle wiring are already
 * covered structurally by [AgentEventBusTest]; they are not re-tested here.
 *
 * The bus's event collector runs on `Dispatchers.Default`, so verification
 * is performed with MockK's `verify(timeout = ...)` — the same pattern
 * used by the project's existing `TerminalViewModelResizeTest` — which
 * polls on real wall time rather than the `runTest { }` virtual clock.
 * A 2000 ms budget is effectively instant on CI and keeps the tests
 * deterministic on slow machines.
 */
class UserEventBusTest {

    // ---- Fixture ------------------------------------------------------------

    private data class Fixture(
        val bus: UserEventBus,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
        val wsm: WebSocketManager,
        val agentRepository: AgentRepository,
    )

    private fun newBus(initiallyLoggedIn: Boolean = true): Fixture {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents

        val wsm = mockk<WebSocketManager>()
        every { wsm.createUserConnection("ws/user-events") } returns conn

        val sessionManager = mockk<SessionManager>(relaxed = true)
        val initial: SessionManager.AuthState =
            if (initiallyLoggedIn) SessionManager.AuthState.FullyConnected
            else SessionManager.AuthState.Unauthenticated
        val authStateFlow = MutableStateFlow<SessionManager.AuthState>(initial)
        every { sessionManager.authState } returns authStateFlow

        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.isUserLoggedIn() } returns initiallyLoggedIn

        val agentRepository = mockk<AgentRepository>()
        every { agentRepository.updateAgentOnline(any(), any()) } just Runs
        coEvery { agentRepository.refreshAgents() } just Runs

        val bus = UserEventBus(wsm, sessionManager, userIdentityStore, agentRepository)
        return Fixture(bus, connEvents, conn, wsm, agentRepository)
    }

    /** Emit a WS event from a real coroutine context.
     *
     *  The bus's collector runs on `Dispatchers.Default`; if we tried to
     *  `emit()` from the `runTest { }` virtual-time scope the SharedFlow's
     *  suspension would never be dispatched to the real dispatcher. Using
     *  `runBlocking` here hops onto the real scheduler so the collector
     *  actually sees the emission. */
    private fun emit(events: MutableSharedFlow<WsEvent>, ev: WsEvent) {
        runBlocking { events.emit(ev) }
    }

    /** Wait until the bus has actually subscribed to the events flow.
     *
     *  `MutableSharedFlow(replay = 0)` silently drops emissions that happen
     *  before any subscriber has registered. `bus.start()` launches the
     *  subscribing coroutine on `Dispatchers.Default` asynchronously, so
     *  `emit()` immediately after `start()` races against the subscription.
     *  Polling `subscriptionCount` on wall time removes that race
     *  deterministically. */
    private fun awaitSubscribed(events: MutableSharedFlow<WsEvent>) = runBlocking {
        val deadline = System.currentTimeMillis() + 2000
        while (events.subscriptionCount.value == 0 &&
            System.currentTimeMillis() < deadline
        ) {
            kotlinx.coroutines.delay(10)
        }
        check(events.subscriptionCount.value > 0) {
            "UserEventBus did not subscribe within 2s"
        }
    }

    // ---- parse(): agent.status_changed -> updateAgentOnline -----------------

    @Test
    fun message_agentStatusChangedOnline_callsUpdateAgentOnline_withTrue() = runTest {
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """
                {
                  "type": "agent.status_changed",
                  "payload": {"agent_id":"a-42","online":true}
                }
                """.trimIndent(),
            ),
        )

        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-42", true) }

        bus.stop()
    }

    @Test
    fun message_agentStatusChangedOffline_callsUpdateAgentOnline_withFalse() = runTest {
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-9","online":false}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-9", false) }

        bus.stop()
    }

    @Test
    fun message_multipleAgentStatusChanges_areAllForwardedInOrder() = runTest {
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":true}}""",
            ),
        )
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-2","online":false}}""",
            ),
        )
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":false}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-1", true) }
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-2", false) }
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-1", false) }

        bus.stop()
    }

    // ---- parse(): malformed payloads are dropped silently -------------------

    @Test
    fun message_malformedEnvelopeJson_isSilentlySkipped_noRepoCall() = runTest {
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Message("this is not json at all {{{"))

        // A subsequent well-formed envelope must still be processed —
        // proves the bus didn't crash or stop consuming. Using the
        // positive verification as the synchronization point (waits for
        // the collector to actually process the follow-up message)
        // guarantees that by the time we assert the negative path above,
        // the malformed message has been fully handled.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-1", true) }

        // The malformed message must NOT have produced any other call.
        verify(exactly = 0) { repo.updateAgentOnline(neq("a-1"), any()) }

        bus.stop()
    }

    @Test
    fun message_envelopeMissingPayloadField_isSilentlySkipped() = runTest {
        // Envelope parses (has `type`) but `payload` is missing. The outer
        // decodeFromString call must throw MissingFieldException and the
        // catch block must swallow it.
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Message("""{"type":"agent.status_changed"}"""))

        // Synchronize on a valid follow-up call, then assert nothing else.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    @Test
    fun message_agentStatusChangedPayloadMissingAgentId_isSilentlySkipped() = runTest {
        // Valid envelope, matching type, but inner payload lacks `agent_id`.
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"online":true}}""",
            ),
        )

        // Synchronize on a valid follow-up.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    @Test
    fun message_agentStatusChangedPayloadWrongTypes_isSilentlySkipped() = runTest {
        // `online` as a string, not a bool — deserialization must fail
        // and the bus must remain alive for the next message.
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":"yes"}}""",
            ),
        )

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    // ---- parse(): unknown event types are ignored ---------------------------

    @Test
    fun message_unknownEventType_isIgnored_noRepoCall() = runTest {
        // Forward-compatibility: any type other than agent.status_changed
        // must fall through the `else -> Unit` branch.
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"some.future.event","payload":{"whatever":1}}""",
            ),
        )
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"sessions.changed","payload":{"sessions":[]}}""",
            ),
        )

        // Synchronize on a valid message, then assert nothing else fired.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    // ---- Non-Message WsEvents: Connected refreshes, Binary is ignored -------

    @Test
    fun connected_event_triggersRefreshAgents() = runTest {
        // On every WS reconnect, the bus must call refreshAgents() to
        // reconcile any transitions that happened while disconnected.
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Connected)

        coVerify(timeout = 2000, exactly = 1) { repo.refreshAgents() }

        bus.stop()
    }

    @Test
    fun binary_event_isIgnored_noRepoCall() = runTest {
        // The user-events channel is text-only. Binary frames must not
        // reach the parser, and must not throw.
        val (bus, connEvents, _, _, repo) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Binary(ByteString.of(1, 2, 3, 4)))

        // Synchronize on a valid follow-up Message — by the time that
        // arrives the Binary frame has been fully observed by the
        // collector (SharedFlow preserves emission order to a subscriber).
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }

        // No other updateAgentOnline calls — the Binary frame was silent.
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }
        // refreshAgents must NOT have been called — Binary is neither
        // Connected nor Message.
        coVerify(exactly = 0) { repo.refreshAgents() }

        bus.stop()
    }
}

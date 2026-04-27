package com.mobaide.app.data.events

import app.cash.turbine.test
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.WsEvent
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit tests for [AgentEventBus.start] subscribe-before-connect race fix and
 * for the init-block `authState.collect` cold-start path that replaces the
 * previous `drop(1)` swallow.
 *
 * ## The subscribe-before-connect race
 *
 * Before the fix, [AgentEventBus.start] invoked `c.connect()` **before**
 * launching the events collector. The underlying [WebSocketConnection._events]
 * is `MutableSharedFlow(replay = 1, extraBufferCapacity = 100)` — if the WS
 * opened and the server fired `Connected` + `Message(sessions.changed)` +
 * `Message(projects.changed)` + `Message(autoscan.list.changed)` before the
 * collector attached, only the **last** envelope survived (replay=1) and the
 * initial session list was silently dropped. After app restart with a
 * restored token, the mobile's session list stayed empty.
 *
 * The fix launches the collector with `CoroutineStart.UNDISPATCHED` before
 * `c.connect()`. UNDISPATCHED runs the coroutine body inline on the caller's
 * thread up to the first suspension (`c.events.collect {…}`), which
 * subscribes synchronously. This test proves the bus emits **every** typed
 * event even when the mock connection fires the burst synchronously from
 * inside `connect()` — the worst-case timing.
 *
 * ## Cold-start FullyConnected → start()
 *
 * Before the fix, the init-block collector dropped the initial `authState`
 * replay with `drop(1)` so a cold-start where `authState` was already
 * `FullyConnected` (restored token) never triggered `start()` from this
 * path. The only thing that triggered it was the explicit call from
 * `SessionListViewModel.init`, which meant the race above got no fresh
 * `sessions.changed` snapshot to consume — and a lost initial snapshot
 * was lost forever. The fix removes `drop(1)`, adding a guard against
 * re-triggering on the same state (tracked via `conn` nullability + the
 * `@Synchronized` / idempotent `start`).
 *
 * Style matches [AgentEventBusTest]: real `MutableSharedFlow` for connection
 * events; MockK for [WebSocketManager] / [WebSocketConnection] / [SessionManager];
 * Turbine to observe state transitions. Coroutines hop off
 * `Dispatchers.Default` inside the bus, so Turbine's default 3s timeout
 * absorbs the thread switch.
 */
class AgentEventBusStartRaceTest {

    private data class BusFixture(
        val bus: AgentEventBus,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
        val wsm: WebSocketManager,
        val sessionManager: SessionManager,
        val authStateFlow: MutableStateFlow<SessionManager.AuthState>,
    )

    /**
     * Build a bus fixture.  The `events` flow on the mock connection is
     * provisioned with the exact same shape the production WebSocketConnection
     * uses (`replay = 1, extraBufferCapacity = 100`) so this test reproduces
     * the race rather than a test-only artifact.
     */
    private fun newBus(
        initialAuthState: SessionManager.AuthState =
            SessionManager.AuthState.FullyConnected,
    ): BusFixture {
        val connEvents = MutableSharedFlow<WsEvent>(
            replay = 1,
            extraBufferCapacity = 100,
        )
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/events") } returns conn
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val authStateFlow = MutableStateFlow<SessionManager.AuthState>(initialAuthState)
        every { sessionManager.authState } returns authStateFlow
        every { sessionManager.isLoggedIn() } answers {
            authStateFlow.value is SessionManager.AuthState.FullyConnected
        }
        val bus = AgentEventBus(wsm, sessionManager)
        return BusFixture(bus, connEvents, conn, wsm, sessionManager, authStateFlow)
    }

    @Test
    fun start_collectorSeesInitialSessionsChangedEnvelope_firedSynchronously() = runTest {
        // Arrange: build a bus in the Unauthenticated state so the init-block
        // authState collector has nothing to trigger off of yet.  We drive
        // the race explicitly by flipping authState to FullyConnected
        // *after* Turbine has subscribed to bus.events — that way the
        // burst emissions and the collector subscription race the way they
        // would on a real cold-start login.
        //
        // Without UNDISPATCHED on the events collector, `c.events` is
        // MutableSharedFlow(replay = 1, extraBufferCapacity = 100); a
        // late collector would only replay the *last* envelope and silently
        // drop the earlier three.  UNDISPATCHED makes the collector attach
        // synchronously inside start() before connect() returns, so every
        // tryEmit above lands while the subscriber is active.
        val fixture = newBus(
            initialAuthState = SessionManager.AuthState.Unauthenticated,
        )
        val (bus, connEvents, conn, _, _, authStateFlow) = fixture
        every { conn.connect() } answers {
            connEvents.tryEmit(WsEvent.Connected)
            connEvents.tryEmit(
                WsEvent.Message(
                    """
                    {
                      "type": "sessions.changed",
                      "payload": {
                        "sessions": [
                          {"session_id":"s1","state":"running","created_at":"2026-04-11T10:00:00Z"}
                        ]
                      }
                    }
                    """.trimIndent(),
                ),
            )
            connEvents.tryEmit(
                WsEvent.Message(
                    """
                    {
                      "type": "projects.changed",
                      "payload": {"projects": []}
                    }
                    """.trimIndent(),
                ),
            )
            connEvents.tryEmit(
                WsEvent.Message(
                    """
                    {
                      "type": "autoscan.list.changed",
                      "payload": {
                        "scans": [],
                        "capacity": 0,
                        "max_scans": 5,
                        "available_projects": []
                      }
                    }
                    """.trimIndent(),
                ),
            )
        }

        // Act + Assert: once Turbine is subscribed, flip authState to
        // FullyConnected which fires start() via the init-block collector.
        // start() synchronously subscribes the events collector before
        // c.connect() emits the burst, so all three typed events reach us.
        bus.events.test {
            authStateFlow.value = SessionManager.AuthState.FullyConnected

            val seen = mutableListOf<AgentEvent>()
            seen.add(awaitItem())
            seen.add(awaitItem())
            seen.add(awaitItem())

            val sessions = seen.single { it is AgentEvent.SessionsChanged }
                as AgentEvent.SessionsChanged
            assertEquals(1, sessions.sessions.size)
            assertEquals("s1", sessions.sessions[0].session_id)

            val projects = seen.single { it is AgentEvent.ProjectsChanged }
                as AgentEvent.ProjectsChanged
            assertEquals(emptyList<Any>(), projects.projects)

            val autoscan = seen.single { it is AgentEvent.AutoScanListChanged }
                as AgentEvent.AutoScanListChanged
            assertEquals(5, autoscan.list.max_scans)

            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun coldStart_withRestoredToken_triggersStart_fromInitBlockCollector() = runTest {
        // Regression guard for the `drop(1)` removal.  On cold-start with a
        // restored token, SessionManager.authState emits FullyConnected as
        // its initial replay value.  Before the fix, the init-block
        // collector dropped that replay and the bus never auto-started
        // from this path (only SessionListViewModel.init's explicit start()
        // saved it, which was too late to observe the initial
        // sessions.changed snapshot landing on the race-vulnerable flow).
        //
        // The new code collects without drop(1) and relies on
        // `start()` being @Synchronized + idempotent (guarded by
        // `if (conn != null) return`) to be safe to re-call.
        val fixture = newBus()
        val (bus, _, conn, wsm, _, _) = fixture

        // The init-block collector runs on Dispatchers.Default and may have
        // already driven connection to Connecting by the time Turbine
        // subscribes — StateFlow only replays the current value, so we
        // cannot rely on observing the Disconnected → Connecting edge.
        // Instead, assert that the bus *reaches* Connecting (accept any
        // intermediate replays).  If it never transitions off Disconnected,
        // Turbine's 3s default times out and the test fails loudly.
        bus.connection.test {
            while (awaitItem() != AgentEventBus.ConnState.Connecting) {
                // StateFlow replays the current value; keep draining until
                // the init-block collector has triggered start().
            }
            cancelAndIgnoreRemainingEvents()
        }

        verify(exactly = 1) { wsm.createConnection("ws/events") }
        verify(exactly = 1) { conn.connect() }

        bus.stop()
    }

    @Test
    fun coldStart_unauthenticated_doesNotStart_andStaysDisconnected() = runTest {
        // Complement: when the cold-start authState is Unauthenticated the
        // init-block collector must NOT call start() — otherwise every
        // logged-out app launch would open a doomed WS and spam the server.
        val fixture = newBus(
            initialAuthState = SessionManager.AuthState.Unauthenticated,
        )
        val (bus, _, _, wsm, _, _) = fixture

        assertEquals(AgentEventBus.ConnState.Disconnected, bus.connection.value)
        verify(exactly = 0) { wsm.createConnection(any()) }
    }

    @Test
    fun coldStart_userOnly_doesNotStart_andStaysDisconnected() = runTest {
        // UserOnly is the "logged into user account but no agent paired"
        // state — the bus has no agent URL to target, so the init-block
        // collector must map this to stop() (which is a no-op from
        // Disconnected).  This guards against a regression where someone
        // adds a branch that mistakenly treats UserOnly as fully paired.
        val fixture = newBus(
            initialAuthState = SessionManager.AuthState.UserOnly,
        )
        val (bus, _, _, wsm, _, _) = fixture

        assertEquals(AgentEventBus.ConnState.Disconnected, bus.connection.value)
        verify(exactly = 0) { wsm.createConnection(any()) }
    }
}

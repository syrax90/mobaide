package com.mobaide.app.data.events

import app.cash.turbine.test
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.VoiceProgress
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [AgentEventBus] — exercises the connection state machine,
 * envelope parsing, typed demultiplexing into the five [AgentEvent] shapes,
 * and malformed-JSON resilience.
 *
 * [WebSocketManager] and [WebSocketConnection] are mocked with MockK. The
 * connection's `events` flow is provided as a real [MutableSharedFlow] so we
 * can emit [WsEvent]s into the bus and observe the downstream [AgentEvent]s.
 *
 * Note: [AgentEventBus] internally hardcodes `CoroutineScope(Dispatchers.Default)`,
 * so the event-collector loop runs on real background threads. We rely on
 * Turbine's default timeout (3 s) — the operations under test are a single
 * field assignment or a `SharedFlow.emit`, both of which complete in
 * microseconds on any machine.
 */
class AgentEventBusTest {

    // ---- Helpers ------------------------------------------------------------

    /**
     * Builds a bus wired to a mock [WebSocketConnection] whose `events` flow
     * is a real [MutableSharedFlow] caller can emit into. Returns the bus, the
     * connection events flow, and the connection mock (for verifying
     * connect/disconnect calls).
     */
    private fun newBus(initiallyLoggedIn: Boolean = true): BusFixture =
        newBusWithAuthStateFlow(initiallyLoggedIn).toBusFixture()

    /**
     * Like [newBus], but also exposes the underlying [MutableStateFlow] that
     * backs `sessionManager.authState`, so tests can drive mid-session
     * `LoggedIn`/`LoggedOut` transitions and observe the bus's reactive
     * start/stop behavior (the `init` block that collects `authState.drop(1)`).
     */
    private fun newBusWithAuthStateFlow(
        initiallyLoggedIn: Boolean = true,
    ): ReactiveBusFixture {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/events") } returns conn
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val initialState: SessionManager.AuthState = if (initiallyLoggedIn) {
            SessionManager.AuthState.FullyConnected
        } else {
            SessionManager.AuthState.Unauthenticated
        }
        val authStateFlow = MutableStateFlow<SessionManager.AuthState>(initialState)
        every { sessionManager.authState } returns authStateFlow
        // Keep isLoggedIn() in lock-step with the live flow value, so the
        // bus's `@Synchronized fun start()` guard (which consults
        // `sessionManager.isLoggedIn()`) agrees with whatever state the
        // test last emitted into `authStateFlow`.
        every { sessionManager.isLoggedIn() } answers {
            authStateFlow.value is SessionManager.AuthState.FullyConnected
        }
        val bus = AgentEventBus(wsm, sessionManager)
        return ReactiveBusFixture(bus, connEvents, conn, wsm, sessionManager, authStateFlow)
    }

    private data class BusFixture(
        val bus: AgentEventBus,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
        val wsm: WebSocketManager,
        val sessionManager: SessionManager,
    )

    private data class ReactiveBusFixture(
        val bus: AgentEventBus,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
        val wsm: WebSocketManager,
        val sessionManager: SessionManager,
        val authStateFlow: MutableStateFlow<SessionManager.AuthState>,
    ) {
        fun toBusFixture(): BusFixture =
            BusFixture(bus, connEvents, conn, wsm, sessionManager)
    }

    // ---- Connection state machine -------------------------------------------

    @Test
    fun connection_startsDisconnected_andBecomesConnecting_onStart() = runTest {
        // Start unauthenticated so the init-block's authState collector does
        // not trigger auto-start. The bus stays Disconnected. Once we
        // transition to FullyConnected and call start() manually, it moves to
        // Connecting, which is what this test verifies.
        val fixture = newBusWithAuthStateFlow(initiallyLoggedIn = false)
        val bus = fixture.bus
        val conn = fixture.conn

        bus.connection.test {
            assertEquals(AgentEventBus.ConnState.Disconnected, awaitItem())
            fixture.authStateFlow.value = SessionManager.AuthState.FullyConnected
            bus.start()
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 1) { conn.connect() }

        bus.stop()
    }

    @Test
    fun connection_revertsToConnecting_onWsDisconnected_andOnError() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.connection.test {
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())
            connEvents.emit(WsEvent.Connected)
            assertEquals(AgentEventBus.ConnState.Connected, awaitItem())

            connEvents.emit(WsEvent.Disconnected)
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())

            connEvents.emit(WsEvent.Connected)
            assertEquals(AgentEventBus.ConnState.Connected, awaitItem())

            connEvents.emit(WsEvent.Error(RuntimeException("boom")))
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun stop_setsConnectionBackToDisconnected_andCallsConnDisconnect() = runTest {
        val (bus, _, conn) = newBus()
        bus.start()

        bus.connection.test {
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())
            bus.stop()
            assertEquals(AgentEventBus.ConnState.Disconnected, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 1) { conn.disconnect() }
    }

    @Test
    fun start_isIdempotent_secondCallIsIgnoredWhileConnected() = runTest {
        val (bus, _, _, wsm) = newBus()
        bus.start()
        bus.start()
        bus.start()
        // createConnection is called only once across the idempotent start() calls.
        verify(exactly = 1) { wsm.createConnection("ws/events") }

        bus.stop()
    }

    @Test
    fun start_whenNotLoggedIn_isNoOp_andStaysDisconnected() = runTest {
        // When the SessionManager reports LoggedOut, the bus must not even
        // ask WebSocketManager for a connection — there's nothing to
        // connect to, and any attempt to open a socket with a stale token
        // would spam the server with failing handshakes.
        val fixture = newBus(initiallyLoggedIn = false)
        val (bus, _, _, wsm, _) = fixture

        bus.start()

        assertEquals(AgentEventBus.ConnState.Disconnected, bus.connection.value)
        verify(exactly = 0) { wsm.createConnection(any()) }
    }

    @Test
    fun authState_transitionLoggedOutToLoggedIn_autoStartsTheBus() = runTest {
        // Regression guard for the bus's reactive `init` collector
        // (`authState.drop(1).collect { ... }`): when the session flips to
        // LoggedIn mid-run (e.g. the user logs in after a cold-start on the
        // login screen), the bus must auto-start without anybody calling
        // `start()` explicitly. MainActivity no longer calls start() on
        // resume — the bus self-starts through this path.
        val fixture = newBusWithAuthStateFlow(initiallyLoggedIn = false)
        val (bus, _, conn, wsm, _, authStateFlow) = fixture

        // Pre-condition: cold-start LoggedOut → no connection, no calls to wsm.
        assertEquals(AgentEventBus.ConnState.Disconnected, bus.connection.value)
        verify(exactly = 0) { wsm.createConnection(any()) }

        // Drive the transition LoggedOut → LoggedIn and wait for the bus to
        // move into Connecting via its init-block collector. Turbine's default
        // 3s timeout comfortably covers the `Dispatchers.Default` hop.
        bus.connection.test {
            assertEquals(AgentEventBus.ConnState.Disconnected, awaitItem())
            authStateFlow.value = SessionManager.AuthState.FullyConnected
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        verify(exactly = 1) { wsm.createConnection("ws/events") }
        verify(exactly = 1) { conn.connect() }

        bus.stop()
    }

    @Test
    fun authState_transitionLoggedInToLoggedOut_autoStopsTheBus_andDisconnectsSocket() = runTest {
        // Regression guard for the second half of the init-block collector:
        // on LoggedIn → LoggedOut (e.g. a refresh-token rejection inside
        // SessionManager.forceLogout), the bus must tear down the live
        // WebSocketConnection so the reconnect loop stops hammering
        // `/ws/events` with a dead token.
        val fixture = newBusWithAuthStateFlow(initiallyLoggedIn = true)
        val (bus, _, conn, _, _, authStateFlow) = fixture

        // Explicitly start the bus from the LoggedIn cold-start path so the
        // subsequent logout transition has something to tear down.
        bus.start()
        assertEquals(AgentEventBus.ConnState.Connecting, bus.connection.value)

        bus.connection.test {
            // Replay of the current value, then the LoggedOut-triggered stop.
            assertEquals(AgentEventBus.ConnState.Connecting, awaitItem())
            authStateFlow.value = SessionManager.AuthState.Unauthenticated
            assertEquals(AgentEventBus.ConnState.Disconnected, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        // The connection we created in start() must have been disconnected.
        verify(atLeast = 1) { conn.disconnect() }
    }

    // ---- Envelope parsing & typed demultiplexing ----------------------------

    @Test
    fun message_sessionsChanged_parsesAndEmitsTypedEvent() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
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
            val evt = awaitItem()
            assertTrue(evt is AgentEvent.SessionsChanged)
            val typed = evt as AgentEvent.SessionsChanged
            assertEquals(1, typed.sessions.size)
            assertEquals("s1", typed.sessions[0].session_id)
            assertEquals("running", typed.sessions[0].state)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_sessionUpdated_parsesAndEmitsTypedEvent() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "session.updated",
                      "payload": {
                        "session":
                          {"session_id":"s2","state":"stopped","created_at":"2026-04-11T10:00:00Z"}
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as AgentEvent.SessionUpdated
            assertEquals("s2", evt.session.session_id)
            assertEquals("stopped", evt.session.state)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_sessionAttention_parsesAndEmitsTypedEvent_withAttentionFields() = runTest {
        // proto-v4 (CLA-64): a session.attention envelope reuses the
        // {"session": ...} payload and must surface as SessionAttention
        // carrying the attention + exit_reason fields off the session.
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "session.attention",
                      "payload": {
                        "session": {
                          "session_id": "s3",
                          "state": "crashed",
                          "created_at": "2026-04-11T10:00:00Z",
                          "attention": "ended",
                          "exit_reason": "process_exited"
                        }
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as AgentEvent.SessionAttention
            assertEquals("s3", evt.session.session_id)
            assertEquals("ended", evt.session.attention)
            assertEquals("process_exited", evt.session.exit_reason)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun stream_sessionAttention_filtersFromOtherEvents() = runTest {
        // The foreground service subscribes via stream<SessionAttention>();
        // a session.updated must NOT surface on that typed stream, but the
        // following session.attention must.
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.stream<AgentEvent.SessionAttention>().test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "session.updated",
                      "payload": {
                        "session": {"session_id":"s1","state":"running","created_at":"2026-04-11T10:00:00Z"}
                      }
                    }
                    """.trimIndent(),
                ),
            )
            expectNoEvents()

            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "session.attention",
                      "payload": {
                        "session": {"session_id":"s1","state":"running","created_at":"2026-04-11T10:00:00Z","attention":"turn_finished"}
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem()
            assertEquals("s1", evt.session.session_id)
            assertEquals("turn_finished", evt.session.attention)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_projectsChanged_parsesAndEmitsTypedEvent() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "projects.changed",
                      "payload": {
                        "projects": [
                          {"name":"p1","path":"/p/1","is_git":true}
                        ]
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as AgentEvent.ProjectsChanged
            assertEquals(1, evt.projects.size)
            assertEquals("p1", evt.projects[0].name)
            assertTrue(evt.projects[0].is_git)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_autoscanListChanged_parsesAndEmitsTypedEvent() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "autoscan.list.changed",
                      "payload": {
                        "scans": [],
                        "capacity": 0,
                        "max_scans": 5,
                        "available_projects": ["p1","p2"]
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as AgentEvent.AutoScanListChanged
            assertEquals(5, evt.list.max_scans)
            assertEquals(listOf("p1", "p2"), evt.list.available_projects)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_autoscanStatus_parsesAndEmitsTypedEvent() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "autoscan.status",
                      "payload": {
                        "project_name": "proj-a",
                        "status": "running",
                        "started_at": 1712822400.0,
                        "last_activity_at": 1712822460.0
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as AgentEvent.AutoScanStatus
            assertEquals("proj-a", evt.instance.project_name)
            assertEquals("running", evt.instance.status)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_autoscanEvent_parsesPhasePayload_andForwardsSeqAndTs() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "autoscan.event",
                      "payload": {
                        "project_name": "proj-a",
                        "event": {
                          "seq": 42,
                          "ts": "2026-04-11T10:00:00Z",
                          "type": "phase",
                          "payload": {
                            "phase": "scan_started",
                            "cycle_number": 3,
                            "next_scan_at": "2026-04-11T10:05:00Z",
                            "issues_processed": 1
                          }
                        }
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as AgentEvent.AutoScanEvent
            assertEquals("proj-a", evt.projectName)
            assertEquals(42L, evt.seq)
            assertEquals("2026-04-11T10:00:00Z", evt.ts)
            val phase = evt.payload as ScanProgressPayload.Phase
            assertEquals("scan_started", phase.phase)
            assertEquals(3, phase.cycleNumber)
            assertEquals("2026-04-11T10:05:00Z", phase.nextScanAt)
            assertEquals(1, phase.issuesProcessed)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_unknownType_isSilentlySkipped_andBusKeepsRunning() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message("""{"type":"totally.unknown","payload":{}}"""),
            )
            // The bus emits an UnknownEnvelopeType sentinel rather than
            // silently dropping the frame — this is intentional so callers
            // can log / metric unknown types. The bus must still keep running.
            val unknown = awaitItem() as AgentEvent.UnknownEnvelopeType
            assertEquals("totally.unknown", unknown.type)

            // A subsequent valid message still arrives — the bus kept running.
            connEvents.emit(
                WsEvent.Message(
                    """{"type":"sessions.changed","payload":{"sessions":[]}}""",
                ),
            )
            val evt = awaitItem() as AgentEvent.SessionsChanged
            assertEquals(emptyList<SessionResponse>(), evt.sessions)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_malformedJson_isSilentlySkipped_andBusKeepsRunning() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(WsEvent.Message("this is not json {"))
            expectNoEvents()

            // Bus should still be able to process the next valid frame.
            connEvents.emit(
                WsEvent.Message(
                    """{"type":"sessions.changed","payload":{"sessions":[]}}""",
                ),
            )
            awaitItem() as AgentEvent.SessionsChanged
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun message_payloadSchemaMismatch_isSilentlySkipped() = runTest {
        // Valid envelope, valid type, but inner payload is missing the required
        // `sessions` field. The inner try/catch inside parseAndEmit must
        // swallow the deserialisation exception and emit nothing.
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message("""{"type":"sessions.changed","payload":{"oops":true}}"""),
            )
            expectNoEvents()

            connEvents.emit(
                WsEvent.Message("""{"type":"sessions.changed","payload":{"sessions":[]}}"""),
            )
            awaitItem() as AgentEvent.SessionsChanged
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }
}

/** The two job frames: each is a whole `GitJobResponse`, decoded as is. */
class AgentEventBusGitJobTest {

    private fun newBus(): Triple<AgentEventBus, MutableSharedFlow<WsEvent>, WebSocketConnection> {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/events") } returns conn
        val sessionManager = mockk<SessionManager>(relaxed = true)
        every { sessionManager.authState } returns
            MutableStateFlow<SessionManager.AuthState>(SessionManager.AuthState.FullyConnected)
        every { sessionManager.isLoggedIn() } returns true
        return Triple(AgentEventBus(wsm, sessionManager), connEvents, conn)
    }

    private val progressFrame = """
        {
          "type": "git.job.progress",
          "payload": {
            "job_id": "3f9c2b7e1d4a4c0f9b8e7d6c5a4b3c2d",
            "session_id": "sess-42",
            "op": "push",
            "status": "running",
            "phase": "Writing objects",
            "percent": 45,
            "line": "Writing objects:  45% (14/32)",
            "ok": null,
            "error_code": null,
            "message": null
          }
        }
    """.trimIndent()

    private val doneFrame = """
        {
          "type": "git.job.done",
          "payload": {
            "job_id": "3f9c2b7e1d4a4c0f9b8e7d6c5a4b3c2d",
            "session_id": "sess-42",
            "op": "push",
            "status": "done",
            "phase": "Writing objects",
            "percent": 100,
            "line": "Writing objects: 100% (32/32), 587.39 KiB | 26.70 MiB/s, done.",
            "ok": true,
            "error_code": null,
            "message": "443f30d..9419146 main -> main"
          }
        }
    """.trimIndent()

    @Test
    fun message_gitJobProgressAndDone_carryTheWholeSnapshot() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(WsEvent.Message(progressFrame))
            val progress = awaitItem() as AgentEvent.GitJobProgress
            assertEquals(
                GitJobResponse(
                    job_id = "3f9c2b7e1d4a4c0f9b8e7d6c5a4b3c2d",
                    session_id = "sess-42",
                    op = "push",
                    status = "running",
                    phase = "Writing objects",
                    percent = 45,
                    line = "Writing objects:  45% (14/32)",
                ),
                progress.job,
            )
            assertTrue(progress.job.running)

            connEvents.emit(WsEvent.Message(doneFrame))
            val done = awaitItem() as AgentEvent.GitJobDone
            assertFalse(done.job.running)
            assertEquals(true, done.job.ok)
            assertEquals(100, done.job.percent)
            assertEquals("443f30d..9419146 main -> main", done.job.message)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }
}

/** `voice.changed`: a whole `VoiceStatusResponse`, decoded as is. */
class AgentEventBusVoiceTest {

    private fun newBus(): Triple<AgentEventBus, MutableSharedFlow<WsEvent>, WebSocketConnection> {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/events") } returns conn
        val sessionManager = mockk<SessionManager>(relaxed = true)
        every { sessionManager.authState } returns
            MutableStateFlow<SessionManager.AuthState>(SessionManager.AuthState.FullyConnected)
        every { sessionManager.isLoggedIn() } returns true
        return Triple(AgentEventBus(wsm, sessionManager), connEvents, conn)
    }

    @Test
    fun message_voiceChanged_carriesTheWholeStatus() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(WsEvent.Message(voiceFrame(state = "downloading", repairing = true)))
            val changed = awaitItem() as AgentEvent.VoiceChanged
            assertEquals("downloading", changed.status.state)
            assertTrue(changed.status.repairing)
            assertEquals(VoiceProgress(done_bytes = 43, total_bytes = 100), changed.status.progress)
            assertEquals("CC-BY-4.0", changed.status.model.notice)
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    private fun voiceFrame(state: String, repairing: Boolean) = """
        {
          "type": "voice.changed",
          "payload": {
            "state": "$state",
            "enabled": true,
            "repairing": $repairing,
            "model": {
              "id": "parakeet", "name": "Parakeet", "languages": ["en"],
              "download_bytes": 100, "disk_bytes": 200, "ram_bytes": 300, "notice": "CC-BY-4.0"
            },
            "progress": {"done_bytes": 43, "total_bytes": 100},
            "host": {
              "ram_total_bytes": null, "disk_free_bytes": null, "cpu_count": null,
              "recommendation": "ok", "reasons": []
            },
            "speed_factor": null,
            "error": null,
            "added_later": {"ignored": true}
          }
        }
    """.trimIndent()
}

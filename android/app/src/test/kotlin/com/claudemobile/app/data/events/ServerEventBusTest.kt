package com.claudemobile.app.data.events

import app.cash.turbine.test
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.network.dto.SessionResponse
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import okio.ByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [ServerEventBus] — exercises the connection state machine,
 * envelope parsing, typed demultiplexing into the five [ServerEvent] shapes,
 * and malformed-JSON resilience.
 *
 * [WebSocketManager] and [WebSocketConnection] are mocked with MockK. The
 * connection's `events` flow is provided as a real [MutableSharedFlow] so we
 * can emit [WsEvent]s into the bus and observe the downstream [ServerEvent]s.
 *
 * Note: [ServerEventBus] internally hardcodes `CoroutineScope(Dispatchers.Default)`,
 * so the event-collector loop runs on real background threads. We rely on
 * Turbine's default timeout (3 s) — the operations under test are a single
 * field assignment or a `SharedFlow.emit`, both of which complete in
 * microseconds on any machine.
 */
class ServerEventBusTest {

    // ---- Helpers ------------------------------------------------------------

    /**
     * Builds a bus wired to a mock [WebSocketConnection] whose `events` flow
     * is a real [MutableSharedFlow] caller can emit into. Returns the bus, the
     * connection events flow, and the connection mock (for verifying
     * connect/disconnect calls).
     */
    private fun newBus(): BusFixture {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/events") } returns conn
        val bus = ServerEventBus(wsm)
        return BusFixture(bus, connEvents, conn, wsm)
    }

    private data class BusFixture(
        val bus: ServerEventBus,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
        val wsm: WebSocketManager,
    )

    // ---- Connection state machine -------------------------------------------

    @Test
    fun connection_startsDisconnected_andBecomesConnecting_onStart() = runTest {
        val (bus, _, conn) = newBus()

        bus.connection.test {
            assertEquals(ServerEventBus.ConnState.Disconnected, awaitItem())
            bus.start()
            assertEquals(ServerEventBus.ConnState.Connecting, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 1) { conn.connect() }

        bus.stop()
    }

    @Test
    fun connection_transitionsToConnected_onWsConnectedEvent() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.connection.test {
            // Connecting (current state after start()).
            assertEquals(ServerEventBus.ConnState.Connecting, awaitItem())
            connEvents.emit(WsEvent.Connected)
            assertEquals(ServerEventBus.ConnState.Connected, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun connection_revertsToConnecting_onWsDisconnected_andOnError() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.connection.test {
            assertEquals(ServerEventBus.ConnState.Connecting, awaitItem())
            connEvents.emit(WsEvent.Connected)
            assertEquals(ServerEventBus.ConnState.Connected, awaitItem())

            connEvents.emit(WsEvent.Disconnected)
            assertEquals(ServerEventBus.ConnState.Connecting, awaitItem())

            connEvents.emit(WsEvent.Connected)
            assertEquals(ServerEventBus.ConnState.Connected, awaitItem())

            connEvents.emit(WsEvent.Error(RuntimeException("boom")))
            assertEquals(ServerEventBus.ConnState.Connecting, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun stop_setsConnectionBackToDisconnected_andCallsConnDisconnect() = runTest {
        val (bus, _, conn) = newBus()
        bus.start()

        bus.connection.test {
            assertEquals(ServerEventBus.ConnState.Connecting, awaitItem())
            bus.stop()
            assertEquals(ServerEventBus.ConnState.Disconnected, awaitItem())
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
    fun start_whenUrlNotConfigured_fallsBackToDisconnected_silently() = runTest {
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/events") } throws
            IllegalStateException("Server URL not configured")
        val bus = ServerEventBus(wsm)

        // Must not propagate the exception.
        bus.start()

        assertEquals(ServerEventBus.ConnState.Disconnected, bus.connection.value)
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
            assertTrue(evt is ServerEvent.SessionsChanged)
            val typed = evt as ServerEvent.SessionsChanged
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
            val evt = awaitItem() as ServerEvent.SessionUpdated
            assertEquals("s2", evt.session.session_id)
            assertEquals("stopped", evt.session.state)
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
            val evt = awaitItem() as ServerEvent.ProjectsChanged
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
            val evt = awaitItem() as ServerEvent.AutoScanListChanged
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
            val evt = awaitItem() as ServerEvent.AutoScanStatus
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
            val evt = awaitItem() as ServerEvent.AutoScanEvent
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
    fun message_autoscanEvent_withoutInnerEventField_isSilentlyDropped() = runTest {
        // Inner `event` key missing -> parseAndEmit yields `null`, no emission.
        // We then emit a valid follow-up message to prove the collector is still
        // alive and the preceding malformed payload did not throw.
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(
                WsEvent.Message(
                    """
                    {"type":"autoscan.event","payload":{"project_name":"proj-a"}}
                    """.trimIndent(),
                ),
            )
            // No emission expected yet.
            expectNoEvents()
            // Follow-up valid message — must arrive, proving the bus did not crash.
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "projects.changed",
                      "payload": {"projects": []}
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem() as ServerEvent.ProjectsChanged
            assertEquals(emptyList<ProjectInfo>(), evt.projects)
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
            expectNoEvents()

            // A subsequent valid message still arrives.
            connEvents.emit(
                WsEvent.Message(
                    """{"type":"sessions.changed","payload":{"sessions":[]}}""",
                ),
            )
            val evt = awaitItem() as ServerEvent.SessionsChanged
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
            awaitItem() as ServerEvent.SessionsChanged
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
            awaitItem() as ServerEvent.SessionsChanged
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    @Test
    fun binary_frame_isIgnored_noEmission() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.events.test {
            connEvents.emit(WsEvent.Binary(ByteString.of(1, 2, 3)))
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }

    // ---- stream<T>() typed filter -------------------------------------------

    @Test
    fun stream_filtersByReifiedType_andIgnoresOtherEvents() = runTest {
        val (bus, connEvents, _) = newBus()
        bus.start()

        bus.stream<ServerEvent.AutoScanListChanged>().test {
            // First a SessionsChanged — the stream<T>() flow must NOT emit.
            connEvents.emit(
                WsEvent.Message(
                    """{"type":"sessions.changed","payload":{"sessions":[]}}""",
                ),
            )
            expectNoEvents()

            // Then an AutoScanListChanged — this one should surface.
            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "autoscan.list.changed",
                      "payload": {"scans":[],"capacity":1,"max_scans":5,"available_projects":[]}
                    }
                    """.trimIndent(),
                ),
            )
            val evt = awaitItem()
            assertEquals(1, evt.list.capacity)
            assertEquals(
                AutoScanListResponse(
                    scans = emptyList<AutoScanInstanceDto>(),
                    capacity = 1,
                    max_scans = 5,
                    available_projects = emptyList(),
                ),
                evt.list,
            )
            cancelAndIgnoreRemainingEvents()
        }

        bus.stop()
    }
}

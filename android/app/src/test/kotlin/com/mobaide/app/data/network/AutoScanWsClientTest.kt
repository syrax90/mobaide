package com.mobaide.app.data.network

import app.cash.turbine.test
import com.mobaide.app.data.events.ScanProgressPayload
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

/**
 * Unit tests for [AutoScanWsClient] — exercises the frame parser (snapshot /
 * caught_up / event), the null-on-error resilience of [AutoScanWsClient.parseFrame],
 * and the initial config-frame generation (subscribe + resume_from_seq).
 *
 * [WebSocketManager]/[WebSocketConnection] are mocked with MockK; the
 * connection's `events` flow is a real [MutableSharedFlow] that the test
 * emits into to drive the `callbackFlow`.
 */
class AutoScanWsClientTest {

    private fun newFixture(): Fixture {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents
        val wsm = mockk<WebSocketManager>()
        every { wsm.createConnection("ws/autoscan") } returns conn
        return Fixture(AutoScanWsClient(wsm), connEvents, conn)
    }

    private data class Fixture(
        val client: AutoScanWsClient,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
    )

    // ---- Connected frame + config frame generation --------------------------

    @Test
    fun connect_emitsSubscribeAndResumeFromSeq_inConfigFrame() = runTest {
        val (client, connEvents, conn) = newFixture()
        val sent = slot<String>()
        every { conn.send(capture(sent)) } returns true

        client.connect(
            subscribe = listOf("proj-a", "proj-b"),
            resumeFromSeq = mapOf("proj-a" to 42L, "proj-b" to 0L),
        ).test {
            connEvents.emit(WsEvent.Connected)
            assertEquals(AutoScanWsClient.Frame.Connected, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val cfg = Json.parseToJsonElement(sent.captured).jsonObject
        val subs = cfg["subscribe"]?.jsonArray
        assertNotNull(subs)
        assertEquals(2, subs!!.size)
        assertEquals("proj-a", subs[0].jsonPrimitive.content)
        assertEquals("proj-b", subs[1].jsonPrimitive.content)

        val rfs = cfg["resume_from_seq"]!!.jsonObject
        assertEquals(42L, rfs["proj-a"]!!.jsonPrimitive.longOrNull)
        assertEquals(0L, rfs["proj-b"]!!.jsonPrimitive.longOrNull)
    }

    // ---- Frame parsing: snapshot --------------------------------------------

    @Test
    fun message_snapshot_parsesScans_andNestedEvents() = runTest {
        val (client, connEvents, _) = newFixture()

        client.connect().test {
            connEvents.emit(WsEvent.Connected)
            awaitItem()  // Frame.Connected

            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "snapshot",
                      "data": {
                        "scans": [
                          {
                            "project_name": "proj-a",
                            "status": "running",
                            "started_at": 1712822400.0,
                            "last_activity_at": 1712822460.0
                          }
                        ],
                        "events": {
                          "proj-a": [
                            {
                              "seq": 1,
                              "ts": "2026-04-11T10:00:00Z",
                              "type": "issue",
                              "payload": {
                                "identifier": "ISS-1",
                                "title": "Fix bug",
                                "step": "state_update",
                                "status": "started"
                              }
                            }
                          ]
                        }
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val frame = awaitItem() as AutoScanWsClient.Frame.Snapshot
            assertEquals(1, frame.scans.size)
            assertEquals("proj-a", frame.scans[0].project_name)
            assertEquals("running", frame.scans[0].status)

            val projEvents = frame.events["proj-a"]
            assertNotNull(projEvents)
            assertEquals(1, projEvents!!.size)
            val parsed = projEvents[0]
            assertEquals("proj-a", parsed.projectName)
            assertEquals(1L, parsed.seq)
            assertEquals("2026-04-11T10:00:00Z", parsed.ts)
            assertEquals("issue", parsed.type)
            val issue = parsed.payload as ScanProgressPayload.Issue
            assertEquals("ISS-1", issue.identifier)
            assertEquals("Fix bug", issue.title)
            assertEquals("state_update", issue.step)
            assertEquals("started", issue.status)

            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Frame parsing: event -----------------------------------------------

    @Test
    fun message_event_parsesProjectAndPayload() = runTest {
        val (client, connEvents, _) = newFixture()

        client.connect().test {
            connEvents.emit(WsEvent.Connected)
            awaitItem()

            connEvents.emit(
                WsEvent.Message(
                    """
                    {
                      "type": "event",
                      "project_name": "proj-a",
                      "event": {
                        "seq": 7,
                        "ts": "2026-04-11T11:00:00Z",
                        "type": "worktree",
                        "payload": {
                          "identifier": "ISS-2",
                          "path": "/w/ISS-2"
                        }
                      }
                    }
                    """.trimIndent(),
                ),
            )
            val frame = awaitItem() as AutoScanWsClient.Frame.Event
            assertEquals("proj-a", frame.event.projectName)
            assertEquals(7L, frame.event.seq)
            assertEquals("2026-04-11T11:00:00Z", frame.event.ts)
            assertEquals("worktree", frame.event.type)
            val wt = frame.event.payload as ScanProgressPayload.Worktree
            assertEquals("ISS-2", wt.identifier)
            assertEquals("/w/ISS-2", wt.path)
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Malformed / unknown frames -----------------------------------------

    @Test
    fun message_malformedJson_returnsNull_andDoesNotCrashStream() = runTest {
        val (client, connEvents, _) = newFixture()

        client.connect().test {
            connEvents.emit(WsEvent.Connected)
            awaitItem()

            connEvents.emit(WsEvent.Message("not-json {"))
            expectNoEvents()

            // Still processing afterwards.
            connEvents.emit(WsEvent.Message("""{"type":"caught_up"}"""))
            assertEquals(AutoScanWsClient.Frame.CaughtUp, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Disconnected / Error -----------------------------------------------

    @Test
    fun wsError_emitsDisconnectedFrame_withThrowable() = runTest {
        val (client, connEvents, _) = newFixture()
        val boom = RuntimeException("kaboom")

        client.connect().test {
            connEvents.emit(WsEvent.Connected)
            awaitItem()

            connEvents.emit(WsEvent.Error(boom))
            val frame = awaitItem() as AutoScanWsClient.Frame.Disconnected
            assertEquals(boom, frame.cause)
            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- Teardown -----------------------------------------------------------

    @Test
    fun flowCancellation_disconnectsUnderlyingConnection() = runTest {
        val (client, connEvents, conn) = newFixture()

        client.connect().test {
            connEvents.emit(WsEvent.Connected)
            awaitItem()
            cancelAndIgnoreRemainingEvents()
        }

        // awaitClose { conn.disconnect() } fires on flow cancellation.
        verify(exactly = 1) { conn.disconnect() }
    }
}

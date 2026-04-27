package com.mobaide.app.data.events

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [ScanPayloadParser] — dispatches the autoscan envelope `type`
 * string to the matching [ScanProgressPayload] subtype and fills in defaults
 * for missing / mistyped fields.
 */
class ScanPayloadParserTest {

    private fun json(raw: String): JsonObject =
        Json.parseToJsonElement(raw) as JsonObject

    // ---- phase --------------------------------------------------------------

    @Test
    fun phase_happyPath_allFieldsPresent() {
        val payload = json(
            """
            {
              "phase": "scan_started",
              "cycle_number": 7,
              "next_scan_at": "2026-04-11T10:00:00Z",
              "issues_processed": 3
            }
            """.trimIndent(),
        )

        val result = ScanPayloadParser.parse("phase", payload)
        assertEquals(
            ScanProgressPayload.Phase(
                phase = "scan_started",
                cycleNumber = 7,
                nextScanAt = "2026-04-11T10:00:00Z",
                issuesProcessed = 3,
            ),
            result,
        )
    }

    @Test
    fun phase_missingFields_defaultToEmptyStringsAndZeroAndNull() {
        // empty JSON object — exercise intOr default (0) and strOrNull default (null),
        // and str(key) default (empty string) for "phase".
        val result = ScanPayloadParser.parse("phase", json("{}"))

        val phase = result as ScanProgressPayload.Phase
        assertEquals("", phase.phase)
        assertEquals(0, phase.cycleNumber)
        assertNull(phase.nextScanAt)
        assertEquals(0, phase.issuesProcessed)
    }

    // ---- issue --------------------------------------------------------------

    @Test
    fun issue_happyPath_mapsAllFields() {
        val payload = json(
            """
            {
              "identifier": "ISS-101",
              "title": "Fix flaky test",
              "step": "state_update",
              "status": "started",
              "error": "network timeout"
            }
            """.trimIndent(),
        )

        val result = ScanPayloadParser.parse("issue", payload)
        assertEquals(
            ScanProgressPayload.Issue(
                identifier = "ISS-101",
                title = "Fix flaky test",
                step = "state_update",
                status = "started",
                error = "network timeout",
            ),
            result,
        )
    }

    @Test
    fun issue_missingErrorField_becomesNull() {
        val payload = json(
            """
            {
              "identifier": "ISS-1",
              "title": "t",
              "step": "s",
              "status": "ok"
            }
            """.trimIndent(),
        )
        val result = ScanPayloadParser.parse("issue", payload) as ScanProgressPayload.Issue
        assertNull(result.error)
    }

    // ---- worktree -----------------------------------------------------------

    @Test
    fun worktree_happyPath() {
        val payload = json(
            """{"identifier":"ISS-42","path":"/tmp/wt/ISS-42"}""",
        )
        val result = ScanPayloadParser.parse("worktree", payload)
        assertEquals(
            ScanProgressPayload.Worktree(identifier = "ISS-42", path = "/tmp/wt/ISS-42"),
            result,
        )
    }

    // ---- session ------------------------------------------------------------

    @Test
    fun session_happyPath_withPid() {
        val payload = json(
            """{"identifier":"ISS-9","session_id":"abc-123","pid":4242}""",
        )
        val result = ScanPayloadParser.parse("session", payload)
        assertEquals(
            ScanProgressPayload.Session(
                identifier = "ISS-9",
                sessionId = "abc-123",
                pid = 4242,
            ),
            result,
        )
    }

    @Test
    fun session_missingPid_becomesNull() {
        val payload = json("""{"identifier":"ISS-9","session_id":"abc-123"}""")
        val result = ScanPayloadParser.parse("session", payload) as ScanProgressPayload.Session
        assertNull(result.pid)
    }

    // ---- log ----------------------------------------------------------------

    @Test
    fun log_happyPath() {
        val payload = json(
            """{"line":"hello","level":"warn","ansi":true}""",
        )
        val result = ScanPayloadParser.parse("log", payload)
        assertEquals(
            ScanProgressPayload.Log(line = "hello", level = "warn", ansi = true),
            result,
        )
    }

    @Test
    fun log_missingLevel_defaultsToInfo() {
        val payload = json("""{"line":"just a line"}""")
        val result = ScanPayloadParser.parse("log", payload) as ScanProgressPayload.Log
        assertEquals("just a line", result.line)
        assertEquals("info", result.level)
        assertEquals(false, result.ansi) // boolOr default
    }

    // ---- unknown type -------------------------------------------------------

    @Test
    fun unknownType_routesToUnknownAndPreservesTypeName() {
        val result = ScanPayloadParser.parse("something_new", json("{}"))
        assertTrue(result is ScanProgressPayload.Unknown)
        assertEquals("something_new", (result as ScanProgressPayload.Unknown).type)
    }

    @Test
    fun emptyStringType_isAlsoRoutedToUnknown() {
        val result = ScanPayloadParser.parse("", json("{}"))
        assertTrue(result is ScanProgressPayload.Unknown)
        assertEquals("", (result as ScanProgressPayload.Unknown).type)
    }
}

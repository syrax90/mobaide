package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Deserialization tests for the proto-v4 [SessionResponse.attention] /
 * [SessionResponse.exit_reason] fields (CLA-64). The agent stamps these on
 * every session snapshot; the DTO must parse them when present and tolerate
 * their absence (back-compat with any producer that omits them).
 */
class SessionResponseAttentionTest {

    private val json = Json { ignoreUnknownKeys = true }

    private fun decode(body: String): SessionResponse =
        json.decodeFromString(SessionResponse.serializer(), body)

    @Test
    fun parsesAttentionAndExitReasonWhenPresent() {
        val dto = decode(
            """
            {
              "session_id": "s1",
              "state": "running",
              "created_at": "2026-04-11T10:00:00Z",
              "attention": "awaiting_permission",
              "exit_reason": null
            }
            """.trimIndent(),
        )
        assertEquals("awaiting_permission", dto.attention)
        assertNull(dto.exit_reason)
    }

    @Test
    fun parsesAwaitingOsPermission() {
        // The agent's startup-stall heuristic: a session most likely blocked on
        // a macOS file-access prompt on the host. Must deserialize like any
        // other attention reason so the "needs you" badge lights up.
        val dto = decode(
            """
            {
              "session_id": "s1",
              "state": "running",
              "created_at": "2026-04-11T10:00:00Z",
              "attention": "awaiting_os_permission",
              "exit_reason": null
            }
            """.trimIndent(),
        )
        assertEquals("awaiting_os_permission", dto.attention)
        assertNull(dto.exit_reason)
    }

    @Test
    fun parsesEndedWithExitReason() {
        val dto = decode(
            """
            {
              "session_id": "s1",
              "state": "crashed",
              "created_at": "2026-04-11T10:00:00Z",
              "attention": "ended",
              "exit_reason": "process_exited"
            }
            """.trimIndent(),
        )
        assertEquals("ended", dto.attention)
        assertEquals("process_exited", dto.exit_reason)
    }

    @Test
    fun defaultsToNullWhenFieldsAbsent() {
        // A snapshot from a producer that omits the new fields entirely must
        // still deserialize, with both attention and exit_reason null.
        val dto = decode(
            """
            {
              "session_id": "s1",
              "state": "running",
              "created_at": "2026-04-11T10:00:00Z"
            }
            """.trimIndent(),
        )
        assertNull(dto.attention)
        assertNull(dto.exit_reason)
    }

    @Test
    fun explicitNullsDeserializeToNull() {
        val dto = decode(
            """
            {
              "session_id": "s1",
              "state": "running",
              "created_at": "2026-04-11T10:00:00Z",
              "attention": null,
              "exit_reason": null
            }
            """.trimIndent(),
        )
        assertNull(dto.attention)
        assertNull(dto.exit_reason)
    }
}

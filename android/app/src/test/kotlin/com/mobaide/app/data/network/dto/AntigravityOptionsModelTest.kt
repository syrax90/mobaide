package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wire-contract guard for the Antigravity DTOs: [AntigravityOptionsModel] must
 * serialize the same snake_case keys + defaults as the server Pydantic model
 * (note the `skip_permissions` / `add_dirs` underscores), [SessionKind] must
 * decode the `antigravity` value, and [CreateSessionRequest] must ship `cli` +
 * `antigravity_options` (never the claude/codex blobs). Mirrors
 * CodexOptionsModelTest.
 */
class AntigravityOptionsModelTest {

    // Mirrors the production session-decoding config (RestApi / AgentEventBus).
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val encodeJson = Json { encodeDefaults = true }

    @Test
    fun encodesSnakeCaseWireKeys_matchingServer() {
        val encoded = encodeJson.encodeToString(
            AntigravityOptionsModel.serializer(),
            AntigravityOptionsModel(
                model = "Gemini 3.5 Flash (Medium)",
                skip_permissions = true,
                sandbox = false,
                add_dirs = listOf("src", "docs"),
                worktree = true,
                branch_name = "feature/x",
            ),
        )
        // skip_permissions/add_dirs pin the snake_case wire keys.
        assertTrue(encoded.contains("\"skip_permissions\":true"))
        assertTrue(encoded.contains("\"add_dirs\""))
        assertTrue(encoded.contains("\"branch_name\""))
        assertTrue(encoded.contains("\"Gemini 3.5 Flash (Medium)\""))
    }

    @Test
    fun decodesServerPayload_withDefaultsForOmittedFields() {
        val opts = json.decodeFromString(
            AntigravityOptionsModel.serializer(),
            """{"skip_permissions":true}""",
        )
        assertTrue(opts.skip_permissions)
        assertNull(opts.model)
        assertFalse(opts.sandbox)
        assertTrue(opts.add_dirs.isEmpty())
        assertFalse(opts.worktree)
        assertNull(opts.branch_name)
    }

    @Test
    fun sessionKindAntigravity_decodesFromWireValue() {
        val dto = json.decodeFromString(
            SessionResponse.serializer(),
            """{"session_id":"s1","state":"running","created_at":"t","kind":"antigravity"}""",
        )
        assertEquals(SessionKind.Antigravity, dto.kind)
    }

    @Test
    fun createSessionRequest_antigravity_serializesCliAndAntigravityOptionsKeys() {
        // The wizard's submitAntigravity() ships exactly these request-side
        // keys; the server keys on `cli` + `antigravity_options`. Pin the wire
        // field names so a rename on the request side surfaces here.
        val encoded = encodeJson.encodeToString(
            CreateSessionRequest.serializer(),
            CreateSessionRequest(
                project_name = "alpha",
                cli = "antigravity",
                antigravity_options = AntigravityOptionsModel(skip_permissions = true),
            ),
        )
        assertTrue(encoded.contains("\"cli\":\"antigravity\""))
        assertTrue(encoded.contains("\"antigravity_options\""))
        assertTrue(encoded.contains("\"skip_permissions\":true"))
    }

    @Test
    fun createSessionRequest_antigravity_roundTripsThroughWire() {
        val req = CreateSessionRequest(
            project_name = "alpha",
            cli = "antigravity",
            antigravity_options = AntigravityOptionsModel(
                model = "Gemini 3.1 Pro (High)",
                sandbox = true,
                add_dirs = listOf("/extra/dir"),
                worktree = true,
                branch_name = "feature/x",
            ),
        )
        val decoded = json.decodeFromString(
            CreateSessionRequest.serializer(),
            encodeJson.encodeToString(CreateSessionRequest.serializer(), req),
        )
        assertEquals("antigravity", decoded.cli)
        assertNull("Antigravity requests must not carry claude_options", decoded.claude_options)
        assertNull("Antigravity requests must not carry codex_options", decoded.codex_options)
        val opts = decoded.antigravity_options
        assertEquals("Gemini 3.1 Pro (High)", opts?.model)
        assertFalse(opts?.skip_permissions == true)
        assertTrue(opts?.sandbox == true)
        assertEquals(listOf("/extra/dir"), opts?.add_dirs)
        assertTrue(opts?.worktree == true)
        assertEquals("feature/x", opts?.branch_name)
    }
}

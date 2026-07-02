package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wire-contract guard for the Gemini DTOs: [GeminiOptionsModel] must serialize
 * the same snake_case keys + defaults as the server Pydantic model (note the
 * `approval_mode` underscore), [SessionKind] must decode the `gemini` value,
 * and [CreateSessionRequest] must ship `cli` + `gemini_options` (never the
 * claude/codex blobs). Mirrors CodexOptionsModelTest.
 */
class GeminiOptionsModelTest {

    // Mirrors the production session-decoding config (RestApi / AgentEventBus).
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val encodeJson = Json { encodeDefaults = true }

    @Test
    fun encodesSnakeCaseWireKeys_matchingServer() {
        val encoded = encodeJson.encodeToString(
            GeminiOptionsModel.serializer(),
            GeminiOptionsModel(
                model = "flash",
                approval_mode = "auto_edit",
                include_directories = listOf("src", "docs"),
                sandbox = true,
                worktree = true,
                branch_name = "feature/x",
            ),
        )
        // approval_mode pins both the snake_case key and the underscore value.
        assertTrue(encoded.contains("\"approval_mode\":\"auto_edit\""))
        assertTrue(encoded.contains("\"include_directories\""))
        assertTrue(encoded.contains("\"branch_name\""))
        assertTrue(encoded.contains("\"flash\""))
    }

    @Test
    fun decodesServerPayload_withDefaultsForOmittedFields() {
        val opts = json.decodeFromString(
            GeminiOptionsModel.serializer(),
            """{"approval_mode":"yolo"}""",
        )
        assertEquals("yolo", opts.approval_mode)
        assertNull(opts.model)
        assertTrue(opts.include_directories.isEmpty())
        assertFalse(opts.sandbox)
        assertFalse(opts.worktree)
        assertNull(opts.branch_name)
    }

    @Test
    fun sessionKindGemini_decodesFromWireValue() {
        val dto = json.decodeFromString(
            SessionResponse.serializer(),
            """{"session_id":"s1","state":"running","created_at":"t","kind":"gemini"}""",
        )
        assertEquals(SessionKind.Gemini, dto.kind)
    }

    @Test
    fun createSessionRequest_gemini_serializesCliAndGeminiOptionsKeys() {
        // The wizard's submitGemini() ships exactly these request-side keys; the
        // server keys on `cli` + `gemini_options`. Pin the wire field names so a
        // rename on the request side surfaces here.
        val encoded = encodeJson.encodeToString(
            CreateSessionRequest.serializer(),
            CreateSessionRequest(
                project_name = "alpha",
                cli = "gemini",
                gemini_options = GeminiOptionsModel(approval_mode = "plan"),
            ),
        )
        assertTrue(encoded.contains("\"cli\":\"gemini\""))
        assertTrue(encoded.contains("\"gemini_options\""))
        assertTrue(encoded.contains("\"approval_mode\":\"plan\""))
    }

    @Test
    fun createSessionRequest_gemini_roundTripsThroughWire() {
        val req = CreateSessionRequest(
            project_name = "alpha",
            cli = "gemini",
            gemini_options = GeminiOptionsModel(
                model = "pro",
                approval_mode = "auto_edit",
                worktree = true,
                branch_name = "feature/x",
            ),
        )
        val decoded = json.decodeFromString(
            CreateSessionRequest.serializer(),
            encodeJson.encodeToString(CreateSessionRequest.serializer(), req),
        )
        assertEquals("gemini", decoded.cli)
        assertNull("Gemini requests must not carry claude_options", decoded.claude_options)
        assertNull("Gemini requests must not carry codex_options", decoded.codex_options)
        val opts = decoded.gemini_options
        assertEquals("pro", opts?.model)
        assertEquals("auto_edit", opts?.approval_mode)
        assertTrue(opts?.worktree == true)
        assertEquals("feature/x", opts?.branch_name)
    }
}

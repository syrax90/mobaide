package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wire-contract guard for the Codex DTOs: [CodexOptionsModel] must serialize the
 * same snake_case keys + defaults as the server Pydantic model, [SessionKind]
 * must round-trip the new `codex` value, and an unknown future kind must coerce
 * to the default rather than throwing (the coerceInputValues forward-compat path).
 */
class CodexOptionsModelTest {

    // Mirrors the production session-decoding config (RestApi / AgentEventBus).
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val encodeJson = Json { encodeDefaults = true }

    @Test
    fun encodesSnakeCaseWireKeys_matchingServer() {
        val encoded = encodeJson.encodeToString(
            CodexOptionsModel.serializer(),
            CodexOptionsModel(
                model = "gpt-5.5",
                sandbox = "read-only",
                approval = "on-request",
                reasoning_effort = "high",
                full_auto = false,
                worktree = true,
                branch_name = "feature/x",
            ),
        )
        assertTrue(encoded.contains("\"reasoning_effort\""))
        assertTrue(encoded.contains("\"full_auto\""))
        assertTrue(encoded.contains("\"branch_name\""))
        assertTrue(encoded.contains("\"gpt-5.5\""))
    }

    @Test
    fun decodesServerPayload_withDefaultsForOmittedFields() {
        val opts = json.decodeFromString(
            CodexOptionsModel.serializer(),
            """{"sandbox":"workspace-write","approval":"never"}""",
        )
        assertEquals("workspace-write", opts.sandbox)
        assertEquals("never", opts.approval)
        assertEquals("high", opts.reasoning_effort)
        assertFalse(opts.full_auto)
        assertFalse(opts.worktree)
        assertNull(opts.model)
        assertNull(opts.branch_name)
    }

    @Test
    fun sessionKindCodex_decodesFromWireValue() {
        val dto = json.decodeFromString(
            SessionResponse.serializer(),
            """{"session_id":"s1","state":"running","created_at":"t","kind":"codex"}""",
        )
        assertEquals(SessionKind.Codex, dto.kind)
    }

    @Test
    fun unknownSessionKind_coercesToDefault_insteadOfThrowing() {
        // Forward-compat: a kind added by a newer server (without a major proto
        // bump) must not break decoding of the whole session snapshot.
        val dto = json.decodeFromString(
            SessionResponse.serializer(),
            """{"session_id":"s1","state":"running","created_at":"t","kind":"gemini"}""",
        )
        assertEquals(SessionKind.Claude, dto.kind)
    }

    @Test
    fun createSessionRequest_codex_serializesCliAndCodexOptionsKeys() {
        // The wizard's submitCodex() ships exactly these request-side keys; the
        // server keys on `cli` + `codex_options` (not `claude_options`). Pin the
        // wire field names so a rename on the request side surfaces here.
        val encoded = encodeJson.encodeToString(
            CreateSessionRequest.serializer(),
            CreateSessionRequest(
                project_name = "alpha",
                cli = "codex",
                codex_options = CodexOptionsModel(sandbox = "read-only"),
            ),
        )
        assertTrue(encoded.contains("\"cli\":\"codex\""))
        assertTrue(encoded.contains("\"codex_options\""))
        assertTrue(encoded.contains("\"sandbox\":\"read-only\""))
    }

    @Test
    fun createSessionRequest_codex_roundTripsThroughWire() {
        val req = CreateSessionRequest(
            project_name = "alpha",
            cli = "codex",
            codex_options = CodexOptionsModel(
                sandbox = "workspace-write",
                approval = "on-request",
                worktree = true,
                branch_name = "feature/x",
            ),
        )
        val decoded = json.decodeFromString(
            CreateSessionRequest.serializer(),
            encodeJson.encodeToString(CreateSessionRequest.serializer(), req),
        )
        assertEquals("codex", decoded.cli)
        assertNull("Codex requests must not carry claude_options", decoded.claude_options)
        val opts = decoded.codex_options
        assertEquals("workspace-write", opts?.sandbox)
        assertEquals("on-request", opts?.approval)
        assertTrue(opts?.worktree == true)
        assertEquals("feature/x", opts?.branch_name)
    }
}

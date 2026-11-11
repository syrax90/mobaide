package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wire-contract guard for the Codex DTOs: [CodexOptionsModel] must serialize the
 * same snake_case keys + defaults as the server Pydantic model, [SessionKind]
 * must round-trip the new `codex` value, and an unknown future kind must be
 * preserved verbatim as [SessionKind.Custom] rather than throwing or coercing
 * to Claude (proto-v7; see [SessionKindSerializerTest]).
 */
class CodexOptionsModelTest {

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

}

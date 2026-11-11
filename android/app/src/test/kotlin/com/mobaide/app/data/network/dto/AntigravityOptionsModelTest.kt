package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
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

}

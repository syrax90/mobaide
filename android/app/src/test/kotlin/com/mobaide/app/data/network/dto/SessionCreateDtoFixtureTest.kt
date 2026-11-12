package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the session-create golden fixtures: every case of the
 * CLI options blocks and the create request decodes, and the `full`
 * cases carry `base_branch` both ways.
 */
class SessionCreateDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    // RestApi's decoder and encoder configuration.
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val allKeysJson = Json { encodeDefaults = true }

    private fun fixture(type: String): Fixture {
        val loaded: Fixture = ProtocolFixtures.load("dto/agent/$type.json")
        assertEquals(type, loaded.type)
        assertTrue("$type has no cases", loaded.cases.isNotEmpty())
        return loaded
    }

    private fun <T> Fixture.decodeAll(serializer: KSerializer<T>): Map<String, T> =
        cases.associate { it.name to json.decodeFromJsonElement(serializer, it.wire) }

    private fun <T> assertBaseBranchEncodes(serializer: KSerializer<T>, value: T, expected: String) {
        val encoded = json.encodeToJsonElement(serializer, value) as JsonObject
        assertEquals(expected, encoded["base_branch"]?.jsonPrimitive?.content)
    }

    @Test
    fun claudeOptions_fullCarriesBaseBranch() {
        val decoded = fixture("ClaudeOptionsModel").decodeAll(ClaudeOptionsModel.serializer())
        val full = decoded.getValue("full")
        assertEquals("origin/develop", full.base_branch)
        assertEquals("feature/mobaide/x", full.branch_name)
        assertBaseBranchEncodes(ClaudeOptionsModel.serializer(), full, "origin/develop")
        decoded.filterKeys { it != "full" }.values.forEach { assertNull(it.base_branch) }
    }

    @Test
    fun codexOptions_fullCarriesBaseBranch() {
        val decoded = fixture("CodexOptionsModel").decodeAll(CodexOptionsModel.serializer())
        val full = decoded.getValue("full")
        assertEquals("origin/main", full.base_branch)
        assertBaseBranchEncodes(CodexOptionsModel.serializer(), full, "origin/main")
        decoded.filterKeys { it != "full" }.values.forEach { assertNull(it.base_branch) }
    }

    @Test
    fun antigravityOptions_fullCarriesBaseBranch() {
        val decoded = fixture("AntigravityOptionsModel").decodeAll(AntigravityOptionsModel.serializer())
        val full = decoded.getValue("full")
        assertEquals("origin/main", full.base_branch)
        assertBaseBranchEncodes(AntigravityOptionsModel.serializer(), full, "origin/main")
        decoded.filterKeys { it != "full" }.values.forEach { assertNull(it.base_branch) }
    }

    @Test
    fun opencodeOptions_fullCarriesEveryFieldBothWays() {
        val fixture = fixture("OpenCodeOptionsModel")
        val decoded = fixture.decodeAll(OpenCodeOptionsModel.serializer())
        val full = decoded.getValue("full")
        assertEquals(
            OpenCodeOptionsModel(
                auto_approve = true,
                connect = true,
                worktree = true,
                branch_name = "b",
                base_branch = "origin/main",
            ),
            full,
        )
        assertEquals(
            fixture.cases.first { it.name == "full" }.wire,
            json.encodeToJsonElement(OpenCodeOptionsModel.serializer(), full),
        )
        // An empty block is a session that asks before it acts.
        assertEquals(OpenCodeOptionsModel(), decoded.getValue("empty_defaults_materialize"))
        assertFalse(decoded.getValue("empty_defaults_materialize").auto_approve)
    }

    @Test
    fun createSessionRequest_opencodeLaunchCarriesItsOptionsBlockBothWays() {
        val decoded = fixture("CreateSessionRequest").decodeAll(CreateSessionRequest.serializer())
        val launch = decoded.getValue("opencode_launch")
        assertEquals("opencode", launch.cli)
        assertEquals("chat", launch.ui)
        assertEquals(
            OpenCodeOptionsModel(
                auto_approve = true,
                worktree = true,
                branch_name = "feature/x",
                base_branch = "origin/main",
            ),
            launch.opencode_options,
        )
        assertNull(launch.claude_options)
        assertNull(launch.codex_options)
        assertNull(launch.antigravity_options)

        val encoded = json.encodeToJsonElement(CreateSessionRequest.serializer(), launch) as JsonObject
        assertEquals("opencode", encoded["cli"]?.jsonPrimitive?.content)
        assertEquals(
            json.encodeToJsonElement(OpenCodeOptionsModel.serializer(), launch.opencode_options!!),
            encoded["opencode_options"],
        )
        assertTrue("claude_options" !in encoded)
    }

    @Test
    fun copilotOptions_fullCarriesBaseBranch_andEncodesTheServerKeys() {
        val decoded = fixture("CopilotOptionsModel").decodeAll(CopilotOptionsModel.serializer())
        val full = decoded.getValue("full")
        assertTrue(full.allow_all)
        assertTrue(full.worktree)
        assertEquals("b", full.branch_name)
        assertEquals("origin/main", full.base_branch)
        assertBaseBranchEncodes(CopilotOptionsModel.serializer(), full, "origin/main")
        val encoded = allKeysJson.encodeToJsonElement(CopilotOptionsModel.serializer(), full) as JsonObject
        assertEquals(setOf("allow_all", "worktree", "branch_name", "base_branch"), encoded.keys)
        val defaults = decoded.getValue("empty_defaults_materialize")
        // `allow_all` must default off.
        assertEquals(CopilotOptionsModel(allow_all = false, worktree = false), defaults)
    }

    @Test
    fun createSessionRequest_copilotLaunchCarriesTheCopilotBlock() {
        val decoded = fixture("CreateSessionRequest").decodeAll(CreateSessionRequest.serializer())
        val launch = decoded.getValue("copilot_launch")
        assertEquals("copilot", launch.cli)
        assertEquals(SessionResponse.UI_CHAT, launch.ui)
        assertEquals(
            CopilotOptionsModel(
                allow_all = true,
                worktree = true,
                branch_name = "feature/x",
                base_branch = "origin/main",
            ),
            launch.copilot_options,
        )
        assertNull(launch.claude_options)
        val encoded = json.encodeToJsonElement(CreateSessionRequest.serializer(), launch) as JsonObject
        assertEquals("copilot", encoded["cli"]?.jsonPrimitive?.content)
        assertTrue("copilot_options" in encoded)
        assertTrue("antigravity_options" !in encoded)
    }

    @Test
    fun createSessionRequest_fullCarriesTopLevelBaseBranch() {
        val decoded = fixture("CreateSessionRequest").decodeAll(CreateSessionRequest.serializer())
        val full = decoded.getValue("full")
        assertTrue(full.worktree)
        assertEquals("feature/x", full.branch_name)
        assertEquals("origin/main", full.base_branch)
        assertNull(full.claude_options?.base_branch)
        assertBaseBranchEncodes(CreateSessionRequest.serializer(), full, "origin/main")
        decoded.filterKeys { it != "full" }.values.forEach { assertNull(it.base_branch) }
    }

    @Test
    fun nullBaseBranch_isLeftOffTheWire() {
        val encoded = json.encodeToJsonElement(
            CreateSessionRequest.serializer(),
            CreateSessionRequest(project_name = "p", worktree = true),
        ) as JsonObject
        assertTrue("base_branch" !in encoded)
    }
}

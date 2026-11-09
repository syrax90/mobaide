package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the session-create golden fixtures: every case of the
 * three CLI options blocks and the create request decodes, and the `full`
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

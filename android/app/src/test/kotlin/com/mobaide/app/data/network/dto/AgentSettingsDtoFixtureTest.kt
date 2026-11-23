package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the agent settings golden fixtures: every case
 * decodes from `wire` and `decoded_canonical` to the same value, and the
 * request encodes to the canonical form.
 */
class AgentSettingsDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    @Serializable
    private data class VersionCase(val name: String, val wire: VersionInfoResponse)

    @Serializable
    private data class VersionFixture(val cases: List<VersionCase>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private fun fixture(type: String): Fixture {
        val loaded: Fixture = ProtocolFixtures.load("dto/agent/$type.json")
        assertEquals(type, loaded.type)
        assertTrue("$type has no cases", loaded.cases.isNotEmpty())
        return loaded
    }

    private fun <T> Fixture.assertCanonicalDecodesLikeWire(serializer: KSerializer<T>) {
        for (case in cases) {
            val fromWire = json.decodeFromJsonElement(serializer, case.wire)
            val fromCanonical =
                json.decodeFromJsonElement(serializer, case.decoded_canonical ?: case.wire)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun everyFixture_decodesFromWireAndCanonicalAlike() {
        fixture("AgentSettingsResponse").assertCanonicalDecodesLikeWire(AgentSettingsResponse.serializer())
        fixture("KeepAwakeSettingsRequest").assertCanonicalDecodesLikeWire(KeepAwakeSettingsRequest.serializer())
    }

    @Test
    fun keepAwakeRequest_encodesToTheCanonicalWire() {
        for (case in fixture("KeepAwakeSettingsRequest").cases) {
            val decoded = json.decodeFromJsonElement(KeepAwakeSettingsRequest.serializer(), case.wire)
            assertEquals(
                case.name,
                case.decoded_canonical ?: case.wire,
                json.encodeToJsonElement(KeepAwakeSettingsRequest.serializer(), decoded),
            )
        }
    }

    @Test
    fun versionFixture_keepAwakeCapability_isOfferedByTheAgentSettingsCase() {
        val cases = ProtocolFixtures.load<VersionFixture>("dto/agent/VersionInfoResponse.json")
            .cases.associateBy { it.name }

        assertTrue(cases.getValue("agent_settings_capability").wire.keepAwakeCapability())
        assertFalse(cases.getValue("voice_capability").wire.keepAwakeCapability())
        assertFalse(cases.getValue("missing_capabilities_defaults_to_empty_object").wire.keepAwakeCapability())
    }

    @Test
    fun keepAwakeCapability_isOnlyAnExplicitTrue() {
        fun version(keepAwake: JsonElement) = VersionInfoResponse(
            name = "mobaide-agent",
            server_version = "1.0.0",
            protocol_versions = listOf(7),
            min_client_version = 1,
            capabilities = buildJsonObject {
                put("agent_settings", buildJsonObject { put("keep_awake", keepAwake) })
            },
        )

        assertTrue(version(JsonPrimitive(true)).keepAwakeCapability())
        assertFalse(version(JsonPrimitive(false)).keepAwakeCapability())
        assertFalse(version(JsonPrimitive("true")).keepAwakeCapability())
        assertFalse(version(JsonPrimitive(1)).keepAwakeCapability())
        assertFalse(version(buildJsonObject { put("enabled", true) }).keepAwakeCapability())
    }
}

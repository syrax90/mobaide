package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the `FsDirsResponse` golden fixture under
 * `tests/protocol_fixtures/dto/agent/`, decoded under the `RestApi` decoder
 * configuration, plus the `capabilities.fs` gate that decides whether the path
 * fields ask for it at all.
 */
class FsDtoFixtureTest {

    @Serializable
    private data class Case(val name: String, val wire: JsonObject)

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private fun decode(name: String): FsDirsResponse {
        val fixture: Fixture = ProtocolFixtures.load("dto/agent/FsDirsResponse.json")
        assertEquals("FsDirsResponse", fixture.type)
        return json.decodeFromJsonElement(FsDirsResponse.serializer(), fixture.cases.first { it.name == name }.wire)
    }

    @Test
    fun fsDirsResponse_decodesEveryCase() {
        assertEquals(
            FsDirsResponse("/home/me", listOf(".config", "code", "Documents", "projects"), truncated = false),
            decode("complete_listing_with_dot_dirs"),
        )
        assertEquals(FsDirsResponse("/var/big", listOf("a0001", "a0002"), truncated = true), decode("truncated_listing"))
        assertEquals(FsDirsResponse("C:\\", emptyList(), truncated = false), decode("empty_windows_drive_root"))
    }

    private fun info(capabilities: String) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities) as JsonObject,
    )

    @Test
    fun fsCapability_readsAnExplicitJsonTrueAndNothingElse() {
        assertTrue(info("""{"fs":{"dirs":true}}""").fsCapability("dirs"))
        assertFalse(info("""{"fs":{"dirs":"true"}}""").fsCapability("dirs"))
        assertFalse(info("""{"fs":{"dirs":false}}""").fsCapability("dirs"))
        assertFalse(info("""{"fs":"yes"}""").fsCapability("dirs"))
        assertFalse(info("""{"fs":{"dirs":{"nested":true}}}""").fsCapability("dirs"))
        assertFalse(info("""{"git":{"changes":true}}""").fsCapability("dirs"))
        assertFalse(info("{}").fsCapability("dirs"))
    }
}

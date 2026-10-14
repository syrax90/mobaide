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
 * The Android arm of `tests/protocol_fixtures/dto/central/AgentSummaryResponse.json`:
 * `supports_uninstall` decodes when the relay sends it and defaults to false
 * for a relay (or an agent build) that predates it. Every case decodes from
 * `wire` and, where it carries one, from `decoded_canonical` to the same value.
 */
class AgentSummaryDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private fun fixture(): Fixture {
        val fixture: Fixture = ProtocolFixtures.load("dto/central/AgentSummaryResponse.json")
        assertEquals("AgentSummaryResponse", fixture.type)
        assertTrue("AgentSummaryResponse has no cases", fixture.cases.isNotEmpty())
        return fixture
    }

    private fun decode(name: String): AgentSummaryResponse =
        json.decodeFromJsonElement(
            AgentSummaryResponse.serializer(),
            fixture().cases.first { it.name == name }.wire,
        )

    @Test
    fun everyCase_decodesFromWireAndCanonicalAlike() {
        for (case in fixture().cases) {
            val fromWire = json.decodeFromJsonElement(AgentSummaryResponse.serializer(), case.wire)
            val fromCanonical = json.decodeFromJsonElement(
                AgentSummaryResponse.serializer(),
                case.decoded_canonical ?: case.wire,
            )
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun full_decodesSupportsUninstall() {
        assertTrue(decode("full").supports_uninstall)
    }

    @Test
    fun neverSeen_defaultsSupportsUninstallToFalse() {
        assertFalse(decode("never_seen").supports_uninstall)
    }
}

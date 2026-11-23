package com.mobaide.app.data.network.dto

import com.mobaide.app.data.voice.VoiceRoute
import com.mobaide.app.data.voice.VoiceRouting
import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/dto/central/CloudVoiceStatus.json`
 * and `CloudVoiceChangedPayload.json`: both decode from `wire` and from
 * `decoded_canonical` alike, and a state or an engine from a newer server reads
 * as "not offered" and "no choice yet".
 */
class CloudVoiceDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true }

    private fun fixture(type: String): Fixture {
        val loaded: Fixture = ProtocolFixtures.load("dto/central/$type.json")
        assertEquals(type, loaded.type)
        assertTrue("$type has no cases", loaded.cases.isNotEmpty())
        return loaded
    }

    private fun <T> Fixture.decode(serializer: KSerializer<T>, name: String): T =
        json.decodeFromJsonElement(serializer, cases.first { it.name == name }.wire)

    private fun <T> Fixture.assertCanonicalDecodesLikeWire(serializer: KSerializer<T>) {
        for (case in cases) {
            val fromWire = json.decodeFromJsonElement(serializer, case.wire)
            val fromCanonical = json.decodeFromJsonElement(serializer, case.decoded_canonical ?: case.wire)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun everyFixture_decodesFromWireAndCanonicalAlike() {
        fixture("CloudVoiceStatus").assertCanonicalDecodesLikeWire(CloudVoiceStatus.serializer())
        fixture("CloudVoiceChangedPayload").assertCanonicalDecodesLikeWire(CloudVoiceChangedPayload.serializer())
    }

    @Test
    fun status_carriesTheLimitsTheUseAndTheChoices() {
        val ready = fixture("CloudVoiceStatus").decode(CloudVoiceStatus.serializer(), "ready_with_choices")

        assertEquals(CloudVoiceStatus.STATE_READY, ready.state)
        assertEquals(CloudVoiceLimits(sample_rate = 16000, max_seconds = 120, max_chunk_bytes = 65536), ready.limits)
        assertEquals(
            CloudVoiceUsage(used_seconds = 742, limit_seconds = 1800, resets_at = "2026-11-23T00:00:00Z"),
            ready.usage,
        )
        assertEquals(
            mapOf(
                "a1b2c3d4e5f6" to CloudVoiceStatus.ENGINE_CLOUD,
                "0f9e8d7c6b5a" to CloudVoiceStatus.ENGINE_LOCAL,
            ),
            ready.engines,
        )
        assertEquals(
            ready.engines,
            fixture("CloudVoiceChangedPayload").decode(CloudVoiceChangedPayload.serializer(), "two_agents").engines,
        )
    }

    @Test
    fun aStateOrAnEngineFromANewerServer_readsAsNotOfferedAndNoChoice() {
        val statuses = fixture("CloudVoiceStatus")
        val newer = statuses.decode(CloudVoiceStatus.serializer(), "state_from_a_newer_server")
        val ready = statuses.decode(CloudVoiceStatus.serializer(), "ready_with_choices")
        val unknownEngine = ready.copy(engines = newer.engines)

        assertFalse(VoiceRouting.cloudOffered(newer))
        assertFalse(VoiceRouting.cloudOffered(statuses.decode(CloudVoiceStatus.serializer(), "off")))
        assertTrue(VoiceRouting.cloudOffered(statuses.decode(CloudVoiceStatus.serializer(), "limit_reached")))
        assertEquals(VoiceRoute.Local, VoiceRouting.route(newer, "a1b2c3d4e5f6", null))
        assertEquals(VoiceRoute.Choose, VoiceRouting.route(unknownEngine, "a1b2c3d4e5f6", null))
    }
}

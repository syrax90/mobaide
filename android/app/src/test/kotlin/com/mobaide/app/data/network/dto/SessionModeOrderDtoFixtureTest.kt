package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/dto/central/SessionModeOrderDto.json`
 * and `UpdateSessionModeOrderRequest.json`, under the `CentralApi` /
 * `UserEventBus` decoder configuration: the order decodes from `wire` and from
 * `decoded_canonical` alike (an order never saved carries a null `updated_at`),
 * and the PUT body this client sends encodes to the canonical wire.
 */
class SessionModeOrderDtoFixtureTest {

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

    @Test
    fun order_decodesFromWireAndCanonicalAlike() {
        for (case in fixture("SessionModeOrderDto").cases) {
            val fromWire = json.decodeFromJsonElement(SessionModeOrderDto.serializer(), case.wire)
            val fromCanonical = json.decodeFromJsonElement(
                SessionModeOrderDto.serializer(),
                case.decoded_canonical ?: case.wire,
            )
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun order_saved_keepsIdsInOrderWithVersion() {
        val saved = fixture("SessionModeOrderDto").decode(SessionModeOrderDto.serializer(), "saved")

        assertEquals(
            listOf("shell", "custom_ab12cd34ef56", "claude", "codex", "antigravity", "auto_scan"),
            saved.order,
        )
        assertEquals(3, saved.version)
        assertEquals("2026-11-04T10:00:00Z", saved.updated_at)
    }

    @Test
    fun order_neverSaved_isTheEmptyDefault() {
        val never = fixture("SessionModeOrderDto").decode(SessionModeOrderDto.serializer(), "never_saved")

        assertEquals(SessionModeOrderDto(), never)
        assertNull(never.updated_at)
    }

    @Test
    fun updateRequest_encodesToTheCanonicalWire() {
        for (case in fixture("UpdateSessionModeOrderRequest").cases) {
            val decoded = json.decodeFromJsonElement(UpdateSessionModeOrderRequest.serializer(), case.wire)
            assertEquals(
                case.name,
                case.decoded_canonical ?: case.wire,
                json.encodeToJsonElement(UpdateSessionModeOrderRequest.serializer(), decoded),
            )
        }
    }
}

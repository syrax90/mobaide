package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the Herdr golden fixtures under
 * `tests/protocol_fixtures/dto/agent/Herdr*.json`: every case decodes from
 * `wire` and from `decoded_canonical` (nulls dropped, defaults filled) to the
 * same value under the `RestApi` decoder configuration, and that value encodes
 * back to the canonical form. `capabilities.herdr` is what hides or shows the
 * whole Herdr UI.
 */
class HerdrDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    /** The canonical form: defaults written, nulls left out. */
    private val canonical = Json { encodeDefaults = true; explicitNulls = false }

    private fun fixture(type: String): Fixture {
        val loaded: Fixture = ProtocolFixtures.load("dto/agent/$type.json")
        assertEquals(type, loaded.type)
        assertTrue("$type has no cases", loaded.cases.isNotEmpty())
        return loaded
    }

    private fun <T> Fixture.assertRoundTrips(serializer: KSerializer<T>) {
        for (case in cases) {
            val fromWire = json.decodeFromJsonElement(serializer, case.wire)
            val expected = case.decoded_canonical ?: case.wire
            assertEquals(case.name, fromWire, json.decodeFromJsonElement(serializer, expected))
            assertEquals(case.name, expected, canonical.encodeToJsonElement(serializer, fromWire))
        }
    }

    private fun <T> Fixture.decode(serializer: KSerializer<T>, name: String): T =
        json.decodeFromJsonElement(serializer, cases.first { it.name == name }.wire)

    @Test
    fun snapshot_everyCaseRoundTripsToTheCanonicalForm() {
        fixture("HerdrSnapshot").assertRoundTrips(HerdrSnapshot.serializer())
    }

    @Test
    fun attention_everyCaseRoundTripsToTheCanonicalForm() {
        fixture("HerdrAttention").assertRoundTrips(HerdrAttention.serializer())
    }

    @Test
    fun snapshot_full_carriesThePaneFieldsTheDashboardReads() {
        val server = fixture("HerdrSnapshot").decode(HerdrSnapshot.serializer(), "full").servers.single()
        val (agent, shell) = server.workspaces.single().tabs.single().panes

        assertTrue(server.isReady)
        assertEquals(listOf("claude", "codex"), server.features.agents.map { it.id })
        assertEquals("done", agent.status)
        assertEquals(12L, agent.turn)
        assertEquals(HerdrRect(x = 0, y = 0, width = 87, height = 49), agent.rect)
        assertNull(shell.agent)
        assertNull(shell.status)
    }

    @Test
    fun snapshot_minimalWire_offersNoAction() {
        // A flag the agent did not send is off: an action is offered only on
        // an explicit `true`.
        val server = fixture("HerdrSnapshot")
            .decode(HerdrSnapshot.serializer(), "defaults_materialize").servers.single()

        assertEquals(HerdrFeatures(), server.features)
        assertFalse(server.features.close_pane)
        assertFalse(server.features.control)
    }

    @Test
    fun snapshot_outdatedServer_isNotReady() {
        val servers = fixture("HerdrSnapshot")
            .decode(HerdrSnapshot.serializer(), "outdated_server_and_explicit_nulls").servers

        assertFalse(servers.first { it.server_id == "tui:work" }.isReady)
        assertTrue(servers.first { it.server_id == "core:default" }.isReady)
    }

    private fun info(capabilities: String) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities) as JsonObject,
    )

    @Test
    fun herdrCapability_readsAnExplicitJsonTrueAndNothingElse() {
        assertTrue(info("""{"herdr":{"dashboard":true}}""").herdrCapability())
        assertFalse(info("""{"herdr":{"dashboard":"true"}}""").herdrCapability())
        assertFalse(info("""{"herdr":{"dashboard":false}}""").herdrCapability())
        assertFalse(info("""{"herdr":true}""").herdrCapability())
        assertFalse(info("""{"herdr":{}}""").herdrCapability())
        // An agent older than the dashboard sends no key at all.
        assertFalse(info("""{"voice":{"dictation":true}}""").herdrCapability())
        assertFalse(info("{}").herdrCapability())
    }
}

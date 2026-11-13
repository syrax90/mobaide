package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the voice golden fixtures under
 * `tests/protocol_fixtures/dto/agent/Voice*.json`: every shape decodes from
 * `wire` and from `decoded_canonical` (the wire minus its nulls) to the same
 * value under the `RestApi` decoder configuration, and the request bodies this
 * client sends encode to the canonical form. `capabilities.voice` of the
 * version golden is what hides or shows the mic.
 */
class VoiceDtoFixtureTest {

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

    private fun <T> Fixture.decode(serializer: KSerializer<T>, name: String): T =
        json.decodeFromJsonElement(serializer, cases.first { it.name == name }.wire)

    private fun <T> Fixture.assertCanonicalDecodesLikeWire(serializer: KSerializer<T>) {
        for (case in cases) {
            val fromWire = json.decodeFromJsonElement(serializer, case.wire)
            val fromCanonical =
                json.decodeFromJsonElement(serializer, case.decoded_canonical ?: case.wire)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    private fun <T> Fixture.assertEncodesToCanonical(serializer: KSerializer<T>) {
        for (case in cases) {
            val decoded = json.decodeFromJsonElement(serializer, case.wire)
            assertEquals(
                case.name,
                case.decoded_canonical ?: case.wire,
                json.encodeToJsonElement(serializer, decoded),
            )
        }
    }

    @Test
    fun everyFixture_decodesFromWireAndCanonicalAlike() {
        fixture("VoiceStatusResponse").assertCanonicalDecodesLikeWire(VoiceStatusResponse.serializer())
        fixture("VoiceInstallRequest").assertCanonicalDecodesLikeWire(VoiceInstallRequest.serializer())
        fixture("VoiceDictationCreateRequest")
            .assertCanonicalDecodesLikeWire(VoiceDictationCreateRequest.serializer())
        fixture("VoiceDictationResponse").assertCanonicalDecodesLikeWire(VoiceDictationResponse.serializer())
        fixture("VoiceDictationAudioRequest")
            .assertCanonicalDecodesLikeWire(VoiceDictationAudioRequest.serializer())
        fixture("VoiceDictationAudioResponse")
            .assertCanonicalDecodesLikeWire(VoiceDictationAudioResponse.serializer())
        fixture("VoiceDictationFinishRequest")
            .assertCanonicalDecodesLikeWire(VoiceDictationFinishRequest.serializer())
        fixture("VoiceDictationResultResponse")
            .assertCanonicalDecodesLikeWire(VoiceDictationResultResponse.serializer())
        fixture("VoiceCleanupRequest").assertCanonicalDecodesLikeWire(VoiceCleanupRequest.serializer())
        fixture("VoiceCleanupResponse").assertCanonicalDecodesLikeWire(VoiceCleanupResponse.serializer())
        fixture("VoiceCleanupSettingsRequest")
            .assertCanonicalDecodesLikeWire(VoiceCleanupSettingsRequest.serializer())
    }

    @Test
    fun requests_encodeToTheCanonicalWire() {
        fixture("VoiceInstallRequest").assertEncodesToCanonical(VoiceInstallRequest.serializer())
        fixture("VoiceDictationCreateRequest").assertEncodesToCanonical(VoiceDictationCreateRequest.serializer())
        fixture("VoiceDictationAudioRequest").assertEncodesToCanonical(VoiceDictationAudioRequest.serializer())
        fixture("VoiceDictationFinishRequest").assertEncodesToCanonical(VoiceDictationFinishRequest.serializer())
        fixture("VoiceCleanupRequest").assertEncodesToCanonical(VoiceCleanupRequest.serializer())
        fixture("VoiceCleanupSettingsRequest").assertEncodesToCanonical(VoiceCleanupSettingsRequest.serializer())
    }

    @Test
    fun cleanupSettings_carryTheAgentsChoice() {
        val requests = fixture("VoiceCleanupSettingsRequest")
        val statuses = fixture("VoiceStatusResponse")

        assertTrue(requests.decode(VoiceCleanupSettingsRequest.serializer(), "on").enabled)
        assertFalse(requests.decode(VoiceCleanupSettingsRequest.serializer(), "off").enabled)
        assertTrue(statuses.decode(VoiceStatusResponse.serializer(), "ready").cleanup_enabled)
        assertFalse(statuses.decode(VoiceStatusResponse.serializer(), "not_installed").cleanup_enabled)
    }

    @Test
    fun status_fromAnAgentBeforeTheCleanupSetting_readsAsCleanupOff() {
        val wire = fixture("VoiceStatusResponse").cases.first { it.name == "ready" }.wire
        val older = JsonObject(wire - "cleanup_enabled")

        assertFalse(json.decodeFromJsonElement(VoiceStatusResponse.serializer(), older).cleanup_enabled)
    }

    @Test
    fun status_repairFlagAndNotice_areRead() {
        val fixture = fixture("VoiceStatusResponse")

        val repairing = fixture.decode(VoiceStatusResponse.serializer(), "repairing_downloading")
        val cancelled = fixture.decode(VoiceStatusResponse.serializer(), "repairing_cancelled")
        val ready = fixture.decode(VoiceStatusResponse.serializer(), "ready")

        assertTrue(repairing.repairing)
        assertEquals(VoiceStatusResponse.STATE_DOWNLOADING, repairing.state)
        assertTrue(cancelled.repairing)
        assertEquals(VoiceStatusResponse.STATE_FAILED, cancelled.state)
        assertTrue(cancelled.enabled)
        assertFalse(ready.repairing)
        assertTrue(ready.model.notice.orEmpty().isNotBlank())
    }

    @Test
    fun status_fromAnAgentBeforeTheRepairFlag_readsAsNotRepairing() {
        val wire = fixture("VoiceStatusResponse").cases.first { it.name == "ready" }.wire
        val older = JsonObject(wire - "repairing")

        assertFalse(json.decodeFromJsonElement(VoiceStatusResponse.serializer(), older).repairing)
    }

    @Test
    fun versionFixture_voiceCapability_carriesTheDictationLimitsAndCleanup() {
        val cases = ProtocolFixtures.load<VersionFixture>("dto/agent/VersionInfoResponse.json")
            .cases.associateBy { it.name }

        assertEquals(
            VoiceCapability(
                sampleRate = 16000,
                maxSeconds = 120,
                maxChunkBytes = 65536,
                cleanup = true,
                cleanupKinds = listOf("claude", "codex", "antigravity", "opencode", "copilot", "cursor"),
            ),
            cases.getValue("voice_capability").wire.voiceCapability(),
        )
        assertNull(cases.getValue("missing_capabilities_defaults_to_empty_object").wire.voiceCapability())
    }

    @Test
    fun voiceCapability_isNullForAnythingButExplicitPositiveLimits() {
        fun version(voice: JsonObject) = VersionInfoResponse(
            name = "mobaide-agent",
            server_version = "1.0.0",
            protocol_versions = listOf(7),
            min_client_version = 1,
            capabilities = buildJsonObject { put("voice", voice) },
        )
        fun voice(
            dictation: Any = true,
            sampleRate: Any = 16000,
            maxSeconds: Any = 120,
            maxChunkBytes: Any = 65536,
        ) = buildJsonObject {
            when (dictation) {
                is Boolean -> put("dictation", dictation)
                else -> put("dictation", dictation.toString())
            }
            listOf("sample_rate" to sampleRate, "max_seconds" to maxSeconds, "max_chunk_bytes" to maxChunkBytes)
                .forEach { (key, value) ->
                    when (value) {
                        is Number -> put(key, value)
                        else -> put(key, value.toString())
                    }
                }
        }

        assertEquals(VoiceCapability(16000, 120, 65536), version(voice()).voiceCapability())
        assertNull(version(voice(dictation = false)).voiceCapability())
        assertNull(version(voice(dictation = "true")).voiceCapability())
        assertNull(version(voice(maxSeconds = 0)).voiceCapability())
        assertNull(version(voice(sampleRate = 16000.5)).voiceCapability())
        assertNull(version(voice(maxChunkBytes = "65536")).voiceCapability())
    }

    private fun capability(cleanup: JsonElement?, kinds: JsonElement? = null) = VersionInfoResponse(
        name = "mobaide-agent",
        server_version = "1.0.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = buildJsonObject {
            put(
                "voice",
                buildJsonObject {
                    put("dictation", true)
                    put("sample_rate", 16000)
                    put("max_seconds", 120)
                    put("max_chunk_bytes", 65536)
                    if (cleanup != null) put("cleanup", cleanup)
                    if (kinds != null) put("cleanup_kinds", kinds)
                },
            )
        },
    ).voiceCapability()

    @Test
    fun voiceCapability_cleanupIsOnlyAnExplicitTrue_andAMalformedKeyKeepsDictation() {
        assertEquals(
            VoiceCapability(16000, 120, 65536, cleanup = true, cleanupKinds = listOf("claude")),
            capability(JsonPrimitive(true)),
        )
        assertEquals(VoiceCapability(16000, 120, 65536, cleanup = false), capability(null))
        assertEquals(VoiceCapability(16000, 120, 65536, cleanup = false), capability(JsonPrimitive(false)))
        assertEquals(VoiceCapability(16000, 120, 65536, cleanup = false), capability(JsonPrimitive("true")))
        assertEquals(VoiceCapability(16000, 120, 65536, cleanup = false), capability(JsonPrimitive(1)))
        assertEquals(VoiceCapability(16000, 120, 65536, cleanup = false), capability(JsonNull))
        assertEquals(
            VoiceCapability(16000, 120, 65536, cleanup = false),
            capability(buildJsonObject { put("enabled", true) }),
        )
    }

    @Test
    fun voiceCapability_cleanupKinds_defaultToClaude_andAMalformedListKeepsCleanup() {
        fun kinds(cleanup: JsonElement, kinds: JsonElement?) = capability(cleanup, kinds)?.cleanupKinds

        val on = JsonPrimitive(true)
        assertEquals(listOf("codex"), kinds(on, buildJsonArray { add("codex") }))
        assertEquals(listOf("claude"), kinds(on, null))
        assertEquals(listOf("claude"), kinds(on, JsonPrimitive("codex")))
        assertEquals(listOf("claude"), kinds(on, buildJsonObject { put("codex", true) }))
        assertEquals(listOf("claude"), kinds(on, JsonNull))
        assertEquals(
            listOf("claude", "antigravity"),
            kinds(
                on,
                buildJsonArray {
                    add("claude")
                    add(1)
                    add(true)
                    add(JsonNull)
                    add(buildJsonObject { put("kind", "codex") })
                    add("antigravity")
                },
            ),
        )
        assertEquals(emptyList<String>(), kinds(on, buildJsonArray {}))
        assertEquals(emptyList<String>(), kinds(JsonPrimitive(false), buildJsonArray { add("codex") }))
        assertEquals(emptyList<String>(), kinds(JsonPrimitive("true"), buildJsonArray { add("codex") }))
    }
}

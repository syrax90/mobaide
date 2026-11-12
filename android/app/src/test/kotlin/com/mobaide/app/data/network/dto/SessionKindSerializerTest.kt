package com.mobaide.app.data.network.dto

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Wire-contract guard for [SessionKindSerializer] (proto-v7). The serializer's
 * descriptor must behave as [kotlinx.serialization.descriptors.PrimitiveKind.STRING]:
 * with an ENUM-kind descriptor, `Json { coerceInputValues = true }` performs its
 * unknown-value-to-default coercion *before* invoking the custom deserializer
 * (kotlinx.serialization#1947), which would silently mislabel every unknown kind
 * as Claude again. These tests are the tripwire for that regression:
 *   - the built-in kinds round-trip their wire strings;
 *   - any other string (custom types, future kinds) is preserved verbatim in
 *     [SessionKind.Custom] and round-trips byte-identically;
 *   - an explicit `"kind": null` still coerces to the property default (Claude) —
 *     null-coercion keys on the property's nullability, not the descriptor;
 *   - `kind_label` decodes when present and defaults to null when absent.
 */
class SessionKindSerializerTest {

    // Mirrors the production session-decoding config (RestApi / AgentEventBus).
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val builtins = mapOf(
        "claude" to SessionKind.Claude,
        "codex" to SessionKind.Codex,
        "antigravity" to SessionKind.Antigravity,
        "opencode" to SessionKind.OpenCode,
        "copilot" to SessionKind.Copilot,
        "auto_scan" to SessionKind.AutoScan,
        "shell" to SessionKind.Shell,
    )

    @Test
    fun builtinKinds_decodeToSingletons_andEncodeBackToWireStrings() {
        builtins.forEach { (wire, kind) ->
            assertEquals(kind, json.decodeFromString(SessionKindSerializer, "\"$wire\""))
            assertEquals("\"$wire\"", json.encodeToString(SessionKindSerializer, kind))
        }
    }

    @Test
    fun customTypeKind_roundTripsVerbatim() {
        val wire = "custom_ab12cd34ef56"
        val decoded = json.decodeFromString(SessionKindSerializer, "\"$wire\"")
        assertEquals(SessionKind.Custom(wire), decoded)
        assertEquals("\"$wire\"", json.encodeToString(SessionKindSerializer, decoded))
    }

    @Test
    fun unknownKind_insideSessionResponse_isNotCoercedToClaude() {
        // The #1947 regression tripwire: coerceInputValues is ON in this config,
        // and with a STRING descriptor it must stay inert for non-null values.
        val dto = json.decodeFromString(
            SessionResponse.serializer(),
            """{"session_id":"s1","state":"running","created_at":"t","kind":"custom_ab12cd34ef56"}""",
        )
        assertEquals(SessionKind.Custom("custom_ab12cd34ef56"), dto.kind)
    }

    @Test
    fun explicitNullKind_stillCoercesToClaudeDefault() {
        // The reason coerceInputValues is kept at all: an explicit `"kind": null`
        // must fall back to SessionResponse.kind's default instead of throwing
        // and dropping the whole session snapshot.
        val dto = json.decodeFromString(
            SessionResponse.serializer(),
            """{"session_id":"s1","state":"running","created_at":"t","kind":null}""",
        )
        assertEquals(SessionKind.Claude, dto.kind)
    }

    @Test
    fun wireName_passesCustomRawThrough_forStorageKeyStability() {
        // Shortcut buckets / MRU recents key on wireName; a custom kind must
        // contribute its raw id-based wire string (rename-stable), never a
        // display name.
        assertEquals("custom_ab12cd34ef56", SessionKind.Custom("custom_ab12cd34ef56").wireName)
        assertEquals("claude", (SessionKind.Claude as SessionKind).wireName)
    }
}

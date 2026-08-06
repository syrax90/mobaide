package com.mobaide.app.ui.terminal

import com.mobaide.app.testing.ProtocolFixtures
import com.mobaide.app.testing.toFixtureHex
import kotlinx.serialization.Serializable
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/keys/key_chords.json` — the shared
 * chord contract Android, iOS and the web client all encode against. Kotlin
 * remains the reference implementation, so a disagreement between this suite
 * and the fixture means the fixture is wrong.
 */
class KeyChordFixtureTest {

    @Serializable
    private data class ChordSpec(
        val ctrl: Boolean,
        val alt: Boolean,
        val shift: Boolean,
        val key: String,
    ) {
        fun toChord(): KeyChord = KeyChord(ctrl = ctrl, alt = alt, shift = shift, key = key)
    }

    @Serializable
    private data class EncodeCase(
        val name: String,
        val chord: ChordSpec,
        val token: String,
        val display: String,
        val bytes_hex: String,
        val summary: String,
        val uses_csi_u: Boolean,
        val same_as_plain_key: String? = null,
    )

    @Serializable
    private data class RejectedEncodeCase(val name: String, val chord: ChordSpec)

    @Serializable
    private data class ParseCase(
        val name: String,
        val token: String,
        val expect: ChordSpec,
        val canonical_token: String,
    )

    @Serializable
    private data class RejectedParseCase(val name: String, val token: String)

    @Serializable
    private data class KeyChordFixture(
        val encode: List<EncodeCase>,
        val encode_rejected: List<RejectedEncodeCase>,
        val parse_cases: List<ParseCase>,
        val parse_rejected: List<RejectedParseCase>,
    )

    private val fixture: KeyChordFixture =
        ProtocolFixtures.load("keys/key_chords.json")

    @Test
    fun fixture_hasCasesToRun() {
        assertTrue("encode cases", fixture.encode.isNotEmpty())
        assertTrue("encode_rejected cases", fixture.encode_rejected.isNotEmpty())
        assertTrue("parse cases", fixture.parse_cases.isNotEmpty())
        assertTrue("parse_rejected cases", fixture.parse_rejected.isNotEmpty())
    }

    @Test
    fun encode_matchesTheGoldenBytes() {
        for (case in fixture.encode) {
            val chord = case.chord.toChord()
            val encoding = KeyChordEncoder.encode(chord)
            assertNotNull("${case.name} must encode", encoding)
            encoding!!
            assertEquals("${case.name} bytes", case.bytes_hex, encoding.bytes.toFixtureHex())
            assertEquals("${case.name} summary", case.summary, encoding.summary)
            assertEquals("${case.name} usesCsiU", case.uses_csi_u, encoding.usesCsiU)
            assertEquals(
                "${case.name} sameAsPlainKey",
                case.same_as_plain_key,
                encoding.sameAsPlainKey,
            )
        }
    }

    @Test
    fun token_andDisplay_matchTheGoldenStrings() {
        for (case in fixture.encode) {
            val chord = case.chord.toChord()
            assertEquals("${case.name} token", case.token, chord.token())
            assertEquals("${case.name} display", case.display, chord.display())
        }
    }

    /** The token is what the server stores, so it must reach the same bytes. */
    @Test
    fun encodingAPersistedToken_reachesTheSameBytes() {
        for (case in fixture.encode) {
            val parsed = KeyChord.parse(case.token)
            assertNotNull("${case.name} token must parse", parsed)
            val encoding = KeyChordEncoder.encode(parsed!!)
            assertNotNull("${case.name} must encode from its token", encoding)
            assertEquals("${case.name} bytes", case.bytes_hex, encoding!!.bytes.toFixtureHex())
        }
    }

    @Test
    fun encode_rejectsTheGoldenRejections() {
        for (case in fixture.encode_rejected) {
            assertNull("${case.name} must not encode", KeyChordEncoder.encode(case.chord.toChord()))
        }
    }

    @Test
    fun parse_matchesTheGoldenChordsAndRenormalizes() {
        for (case in fixture.parse_cases) {
            val parsed = KeyChord.parse(case.token)
            assertEquals("${case.name} parse", case.expect.toChord(), parsed)
            assertEquals("${case.name} canonical token", case.canonical_token, parsed?.token())
        }
    }

    @Test
    fun parse_rejectsTheGoldenRejections() {
        for (case in fixture.parse_rejected) {
            assertNull("${case.name} must not parse", KeyChord.parse(case.token))
        }
    }
}

package com.mobaide.app.ui.terminal

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Structural properties of [KeyChordEncoder] that a case table cannot express.
 * The wire matrix itself lives in `tests/protocol_fixtures/keys/key_chords.json`
 * and is asserted by [KeyChordFixtureTest], the iOS `KeyChordFixtureTests` and
 * the web `keyChord.test.ts`.
 */
class KeyChordEncoderTest {

    private fun chord(
        key: String,
        ctrl: Boolean = false,
        alt: Boolean = false,
        shift: Boolean = false,
    ) = KeyChord(ctrl = ctrl, alt = alt, shift = shift, key = key)

    private val legalModifierSubsets = listOf(
        Triple(false, false, false),
        Triple(true, false, false),
        Triple(false, true, false),
        Triple(false, false, true),
        Triple(true, true, false),
        Triple(true, false, true),
        Triple(false, true, true),
    )

    @Test
    fun ctrlShiftI_isDistinctFromCtrlI() {
        val ctrlShiftI = KeyChordEncoder.encode(chord("i", ctrl = true, shift = true))!!
        val ctrlI = KeyChordEncoder.encode(chord("i", ctrl = true))!!
        assertFalse(
            "Ctrl+Shift+I must not collapse onto the Ctrl+I (= Tab) byte",
            ctrlShiftI.bytes.contentEquals(ctrlI.bytes),
        )
    }

    /**
     * Every chord the picker can compose has to encode, or a user could select a
     * combination the Send button then silently drops.
     */
    @Test
    fun everyChordInTheDomain_encodes() {
        for (key in KeyChordKeys.all) {
            for ((ctrl, alt, shift) in legalModifierSubsets) {
                val composed = chord(key, ctrl = ctrl, alt = alt, shift = shift)
                val encoding = KeyChordEncoder.encode(composed)
                assertNotNull("${composed.token()} must encode", encoding)
                assertTrue("${composed.token()} bytes", encoding!!.bytes.isNotEmpty())
            }
        }
    }

    /**
     * A CSI-u sequence carries the modifier set explicitly, so unlike the legacy
     * forms (where Ctrl+I and Tab share a byte) two distinct chords can never
     * collapse onto one sequence.
     */
    @Test
    fun csiUSequences_areUniquePerChord() {
        val seen = mutableMapOf<String, String>()
        for (key in KeyChordKeys.all) {
            for ((ctrl, alt, shift) in legalModifierSubsets) {
                val composed = chord(key, ctrl = ctrl, alt = alt, shift = shift)
                val encoding = KeyChordEncoder.encode(composed) ?: continue
                if (!encoding.usesCsiU) continue
                val hex = encoding.bytes.joinToString("") { "%02x".format(it.toInt() and 0xFF) }
                assertNull(
                    "$hex is emitted by both ${seen[hex]} and ${composed.token()}",
                    seen[hex],
                )
                seen[hex] = composed.token()
            }
        }
    }
}

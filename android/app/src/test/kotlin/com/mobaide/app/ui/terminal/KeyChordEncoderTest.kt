package com.mobaide.app.ui.terminal

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Pins the [KeyChordEncoder] wire matrix: legacy-first byte forms, the exact
 * preview [ChordEncoding.summary] strings, the [ChordEncoding.usesCsiU] risky
 * flag, and the [ChordEncoding.sameAsPlainKey] disclosure (with its
 * plain-special self-suppression). Also covers the [KeyChord] token round-trip
 * and [KeyChord.parse] rejection rules the recents pipeline relies on to drop
 * stale persisted tokens silently.
 */
class KeyChordEncoderTest {

    private fun chord(
        key: String,
        ctrl: Boolean = false,
        alt: Boolean = false,
        shift: Boolean = false,
    ) = KeyChord(ctrl = ctrl, alt = alt, shift = shift, key = key)

    private fun bytes(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }

    private fun assertEncodes(
        chord: KeyChord,
        expectedBytes: ByteArray,
        expectedSummary: String,
        usesCsiU: Boolean = false,
        sameAsPlainKey: String? = null,
    ) {
        val encoding = KeyChordEncoder.encode(chord)
        assertNotNull("${chord.display()} must encode", encoding)
        encoding!!
        assertArrayEquals("${chord.display()} bytes", expectedBytes, encoding.bytes)
        assertEquals("${chord.display()} summary", expectedSummary, encoding.summary)
        assertEquals("${chord.display()} usesCsiU", usesCsiU, encoding.usesCsiU)
        assertEquals("${chord.display()} sameAsPlainKey", sameAsPlainKey, encoding.sameAsPlainKey)
    }

    @Test
    fun plainAndShiftedKeys_sendLiteralBytes() {
        assertEncodes(chord("a"), bytes(0x61), "0x61")
        assertEncodes(chord("i", shift = true), bytes(0x49), "0x49")
        assertEncodes(chord("2"), bytes(0x32), "0x32")
    }

    @Test
    fun ctrlChords_sendLegacyC0Bytes() {
        assertEncodes(chord("a", ctrl = true), bytes(0x01), "0x01")
        assertEncodes(chord("space", ctrl = true), bytes(0x00), "0x00")
        assertEncodes(chord("backspace", ctrl = true), bytes(0x08), "0x08")
    }

    @Test
    fun ctrlI_andCtrlM_sendLegacyBytes_withPlainKeyDisclosure() {
        assertEncodes(chord("i", ctrl = true), bytes(0x09), "0x09", sameAsPlainKey = "Tab")
        assertEncodes(chord("m", ctrl = true), bytes(0x0D), "0x0D", sameAsPlainKey = "Enter")
    }

    @Test
    fun plainSpecials_suppressTheirOwnDisclosure() {
        assertEncodes(chord("tab"), bytes(0x09), "0x09")
        assertEncodes(chord("enter"), bytes(0x0D), "0x0D")
        assertEncodes(chord("space"), bytes(0x20), "0x20")
        assertEncodes(chord("backspace"), bytes(0x7F), "0x7F")
    }

    @Test
    fun altChords_prefixEscOverSingleByteCores() {
        assertEncodes(chord("x", alt = true), bytes(0x1B, 0x78), "ESC 0x78")
        assertEncodes(chord("a", alt = true, shift = true), bytes(0x1B, 0x41), "ESC 0x41")
        assertEncodes(chord("a", ctrl = true, alt = true), bytes(0x1B, 0x01), "ESC 0x01")
        assertEncodes(chord("5", alt = true), bytes(0x1B, 0x35), "ESC 0x35")
        assertEncodes(chord("backspace", alt = true), bytes(0x1B, 0x7F), "ESC 0x7F")
    }

    @Test
    fun shiftTab_sendsCsiZ() {
        assertEncodes(chord("tab", shift = true), bytes(0x1B, 0x5B, 0x5A), "ESC [ Z")
    }

    @Test
    fun plainFunctionKeys_useXtermSs3AndTildeForms() {
        assertEncodes(chord("f1"), bytes(0x1B, 0x4F, 0x50), "ESC O P")
        assertEncodes(chord("f5"), bytes(0x1B, 0x5B, 0x31, 0x35, 0x7E), "ESC [ 15 ~")
        assertEncodes(chord("f12"), bytes(0x1B, 0x5B, 0x32, 0x34, 0x7E), "ESC [ 24 ~")
    }

    @Test
    fun modifiedFunctionKeys_useXtermModifierForms_neverCsiU() {
        assertEncodes(
            chord("f1", ctrl = true),
            bytes(0x1B, 0x5B, 0x31, 0x3B, 0x35, 0x50),
            "ESC [ 1 ; 5 P",
        )
        assertEncodes(
            chord("f5", shift = true),
            bytes(0x1B, 0x5B, 0x31, 0x35, 0x3B, 0x32, 0x7E),
            "ESC [ 15 ; 2 ~",
        )
    }

    @Test
    fun inexpressibleChords_fallBackToCsiU_andAreFlaggedRisky() {
        assertEncodes(
            chord("i", ctrl = true, shift = true),
            bytes(0x1B, 0x5B, 0x31, 0x30, 0x35, 0x3B, 0x36, 0x75),
            "ESC [ 105 ; 6 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("2", ctrl = true),
            bytes(0x1B, 0x5B, 0x35, 0x30, 0x3B, 0x35, 0x75),
            "ESC [ 50 ; 5 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("2", shift = true),
            bytes(0x1B, 0x5B, 0x35, 0x30, 0x3B, 0x32, 0x75),
            "ESC [ 50 ; 2 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("enter", shift = true),
            bytes(0x1B, 0x5B, 0x31, 0x33, 0x3B, 0x32, 0x75),
            "ESC [ 13 ; 2 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("enter", ctrl = true),
            bytes(0x1B, 0x5B, 0x31, 0x33, 0x3B, 0x35, 0x75),
            "ESC [ 13 ; 5 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("tab", ctrl = true),
            bytes(0x1B, 0x5B, 0x39, 0x3B, 0x35, 0x75),
            "ESC [ 9 ; 5 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("tab", alt = true, shift = true),
            bytes(0x1B, 0x5B, 0x39, 0x3B, 0x34, 0x75),
            "ESC [ 9 ; 4 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("space", ctrl = true, shift = true),
            bytes(0x1B, 0x5B, 0x33, 0x32, 0x3B, 0x36, 0x75),
            "ESC [ 32 ; 6 u",
            usesCsiU = true,
        )
        assertEncodes(
            chord("backspace", shift = true),
            bytes(0x1B, 0x5B, 0x31, 0x32, 0x37, 0x3B, 0x32, 0x75),
            "ESC [ 127 ; 2 u",
            usesCsiU = true,
        )
    }

    @Test
    fun ctrlShiftI_isDistinctFromCtrlI() {
        val ctrlShiftI = KeyChordEncoder.encode(chord("i", ctrl = true, shift = true))!!
        val ctrlI = KeyChordEncoder.encode(chord("i", ctrl = true))!!
        assertFalse(
            "Ctrl+Shift+I must not collapse onto the Ctrl+I (= Tab) byte",
            ctrlShiftI.bytes.contentEquals(ctrlI.bytes),
        )
    }

    @Test
    fun encode_returnsNull_forUnknownKeyOrAllThreeModifiers() {
        assertNull(KeyChordEncoder.encode(chord("esc", ctrl = true)))
        assertNull(KeyChordEncoder.encode(chord("a", ctrl = true, alt = true, shift = true)))
    }

    @Test
    fun token_roundTripsThroughParse() {
        val chords = listOf(
            chord("a"),
            chord("i", ctrl = true, shift = true),
            chord("f5", alt = true),
            chord("backspace", ctrl = true),
        )
        for (original in chords) {
            assertEquals(original, KeyChord.parse(original.token()))
        }
        assertEquals("ctrl+shift+i", chord("i", ctrl = true, shift = true).token())
    }

    @Test
    fun parse_rejectsInvalidTokens() {
        listOf("", "ctrl+", "ctrl+alt+shift+a", "meta+a", "ctrl+esc", "ctrl+ctrl+a")
            .forEach { token ->
                assertNull("\"$token\" must not parse", KeyChord.parse(token))
            }
    }

    @Test
    fun display_usesConventionalModifierOrderAndKeyNames() {
        assertEquals("Ctrl+Shift+I", chord("i", ctrl = true, shift = true).display())
        assertEquals("Alt+F4", chord("f4", alt = true).display())
        assertEquals("Ctrl+Space", chord("space", ctrl = true).display())
        assertEquals("Backspace", chord("backspace").display())
    }
}

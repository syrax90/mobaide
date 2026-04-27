package com.mobaide.app.ui.autoscan

import androidx.compose.ui.text.font.FontWeight
import com.mobaide.app.ui.theme.AnsiColors
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [AnsiParser] — a minimal SGR parser that turns ANSI-coloured
 * strings into a Compose [androidx.compose.ui.text.AnnotatedString].
 *
 * Assertions focus on:
 *   1. the plain-text projection (`.text`) — ESC sequences must be stripped.
 *   2. the span styles attached by SGR codes (bold, standard fg, bright fg,
 *      256-colour fg within 0..15).
 *   3. graceful handling of non-'m' CSI sequences and malformed escapes.
 */
class AnsiParserTest {

    private val ESC = "\u001B"

    // ---- plain text (no escapes) -------------------------------------------

    @Test
    fun plainText_passesThroughUnchanged() {
        val result = AnsiParser.parse("hello world")
        assertEquals("hello world", result.text)
    }

    @Test
    fun emptyString_producesEmptyAnnotatedString() {
        val result = AnsiParser.parse("")
        assertEquals("", result.text)
        assertTrue(result.spanStyles.isEmpty())
    }

    // ---- reset (0) ----------------------------------------------------------

    @Test
    fun resetCode_clearsColourAndBold_noSpanCarriesStyling() {
        // Apply red+bold, then reset, then append plain text. The plain text
        // after the reset must have no colour and no bold weight.
        val input = "${ESC}[1;31mERR${ESC}[0m ok"
        val result = AnsiParser.parse(input)

        assertEquals("ERR ok", result.text)

        // Find span that covers the " ok" portion (start index 3).
        val okRange = result.spanStyles.firstOrNull { it.start == 3 }
        assertNotNull("expected a span starting at index 3 for \" ok\"", okRange)
        val okStyle = okRange!!.item
        assertEquals(
            "colour must be Unspecified after reset",
            androidx.compose.ui.graphics.Color.Unspecified,
            okStyle.color,
        )
        assertNull("bold must be cleared after reset", okStyle.fontWeight)
    }

    // ---- bold (1) -----------------------------------------------------------

    @Test
    fun boldCode_setsFontWeightBoldOnFollowingText() {
        val result = AnsiParser.parse("${ESC}[1mBOLD")
        assertEquals("BOLD", result.text)

        val span = result.spanStyles.firstOrNull { it.start == 0 && it.end == 4 }
        assertNotNull(span)
        assertEquals(FontWeight.Bold, span!!.item.fontWeight)
    }

    // ---- foreground colours 30..37 -----------------------------------------

    @Test
    fun fgStandard_31_mapsToAnsiColorsIndex1_red() {
        val result = AnsiParser.parse("${ESC}[31mred")
        assertEquals("red", result.text)

        val span = result.spanStyles.first { it.end > it.start }
        assertEquals(AnsiColors[1], span.item.color) // 31-30 = 1
    }

    @Test
    fun fgStandard_37_mapsToAnsiColorsIndex7_white() {
        val result = AnsiParser.parse("${ESC}[37mW")
        val span = result.spanStyles.first { it.end > it.start }
        assertEquals(AnsiColors[7], span.item.color) // 37-30 = 7
    }

    // ---- bright foreground 90..97 ------------------------------------------

    @Test
    fun fgBright_90_mapsToAnsiColorsIndex8_brightBlack() {
        val result = AnsiParser.parse("${ESC}[90mdim")
        val span = result.spanStyles.first { it.end > it.start }
        assertEquals(AnsiColors[8], span.item.color) // 8 + (90-90) = 8
    }

    @Test
    fun fgBright_97_mapsToAnsiColorsIndex15_brightWhite() {
        val result = AnsiParser.parse("${ESC}[97mX")
        val span = result.spanStyles.first { it.end > it.start }
        assertEquals(AnsiColors[15], span.item.color) // 8 + (97-90) = 15
    }

    // ---- 256-colour foreground (38;5;n) in 0..15 ---------------------------

    @Test
    fun fg256_withinAnsiTable_mapsToAnsiColorsByIndex() {
        // 38;5;4 -> AnsiColors[4] (blue)
        val result = AnsiParser.parse("${ESC}[38;5;4mblue")
        assertEquals("blue", result.text)
        val span = result.spanStyles.first { it.end > it.start }
        assertEquals(AnsiColors[4], span.item.color)
    }

    @Test
    fun fg256_outsideAnsiTable_isIgnored_fgRemainsUnspecified() {
        // 38;5;200 is outside 0..15 — parser has no palette for that, so it
        // should leave `fg` unchanged (null → Unspecified). Text must still
        // come through cleanly.
        val result = AnsiParser.parse("${ESC}[38;5;200mhi")
        assertEquals("hi", result.text)

        val span = result.spanStyles.firstOrNull { it.end > it.start }
        // No colour applied — span either absent or Unspecified.
        if (span != null) {
            assertEquals(
                androidx.compose.ui.graphics.Color.Unspecified,
                span.item.color,
            )
        }
    }

    // ---- non-'m' CSI sequences are silently stripped -----------------------

    @Test
    fun cursorMoveCsi_H_isStripped_fromOutputText() {
        // ESC [ 2;10 H moves cursor — must be stripped, surrounding text kept.
        val result = AnsiParser.parse("before${ESC}[2;10Hafter")
        assertEquals("beforeafter", result.text)
    }

    @Test
    fun eraseLineCsi_K_isStripped() {
        val result = AnsiParser.parse("a${ESC}[Kb")
        assertEquals("ab", result.text)
    }

    @Test
    fun eraseDisplayCsi_J_isStripped() {
        val result = AnsiParser.parse("x${ESC}[2Jy")
        assertEquals("xy", result.text)
    }

    @Test
    fun nonMCsi_doesNotDisturbActiveStyling() {
        // Red text, then cursor move, then more red text — styling must survive.
        val result = AnsiParser.parse("${ESC}[31mA${ESC}[HB")
        assertEquals("AB", result.text)
        val redSpans = result.spanStyles.filter { it.item.color == AnsiColors[1] }
        assertFalse("expected at least one red-coloured span", redSpans.isEmpty())
    }

    // ---- malformed input ----------------------------------------------------

    @Test
    fun unterminatedCsi_atEndOfInput_isToleratedWithoutThrowing() {
        // ESC [ with no terminator at end of string — parser must break the
        // loop cleanly and return whatever it has so far.
        val result = AnsiParser.parse("hello${ESC}[31")
        // Pre-escape text must be present.
        assertTrue("expected parser to preserve text up to the escape", result.text.startsWith("hello"))
    }

    @Test
    fun emptySgr_isEquivalentToReset() {
        // ESC[m with empty params is documented as "reset" (params split of
        // empty string yields listOf(0) per the parser).
        val result = AnsiParser.parse("${ESC}[1;31mRED${ESC}[mplain")
        assertEquals("REDplain", result.text)

        // The "plain" section must not carry bold or red.
        val plainSpan = result.spanStyles.firstOrNull { it.start == 3 }
        assertNotNull(plainSpan)
        assertNull(plainSpan!!.item.fontWeight)
        assertEquals(
            androidx.compose.ui.graphics.Color.Unspecified,
            plainSpan.item.color,
        )
    }
}

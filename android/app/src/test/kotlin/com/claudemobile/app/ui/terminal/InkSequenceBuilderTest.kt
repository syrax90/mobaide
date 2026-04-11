package com.claudemobile.app.ui.terminal

import org.junit.Assert.assertArrayEquals
import org.junit.Test

/**
 * Unit tests for [InkSequenceBuilder] — translates semantic UI actions into
 * the raw byte sequences that Claude Code's Ink TUI reads from stdin.
 */
class InkSequenceBuilderTest {

    // ---- textInput ----------------------------------------------------------

    @Test
    fun textInput_appendsCarriageReturn() {
        assertArrayEquals(
            "hello\r".toByteArray(Charsets.UTF_8),
            InkSequenceBuilder.textInput("hello"),
        )
    }

    @Test
    fun textInput_emptyString_justCarriageReturn() {
        assertArrayEquals(
            byteArrayOf('\r'.code.toByte()),
            InkSequenceBuilder.textInput(""),
        )
    }

    @Test
    fun textInput_utf8MultiByteCharactersAreEncodedCorrectly() {
        // "héllo" — 'é' is 2 bytes in UTF-8 (0xC3 0xA9). The Ink TUI receives
        // the raw UTF-8 stream, so we must not accidentally use Latin-1 or
        // UTF-16 here.
        val expected = byteArrayOf(
            'h'.code.toByte(),
            0xC3.toByte(),
            0xA9.toByte(),
            'l'.code.toByte(),
            'l'.code.toByte(),
            'o'.code.toByte(),
            '\r'.code.toByte(),
        )
        assertArrayEquals(expected, InkSequenceBuilder.textInput("héllo"))
    }

    // ---- checkboxToggle / checkboxSubmit ------------------------------------

    @Test
    fun checkboxToggle_isSingleSpaceByte() {
        assertArrayEquals(
            byteArrayOf(' '.code.toByte()),
            InkSequenceBuilder.checkboxToggle(),
        )
    }

    @Test
    fun checkboxSubmit_isCarriageReturn0x0d() {
        assertArrayEquals(
            byteArrayOf(0x0d),
            InkSequenceBuilder.checkboxSubmit(),
        )
    }

    // ---- arrow keys ---------------------------------------------------------

    @Test
    fun arrowUp_isEscBracketA() {
        assertArrayEquals(
            byteArrayOf(0x1b, '['.code.toByte(), 'A'.code.toByte()),
            InkSequenceBuilder.arrowUp(),
        )
    }

    @Test
    fun arrowDown_isEscBracketB() {
        assertArrayEquals(
            byteArrayOf(0x1b, '['.code.toByte(), 'B'.code.toByte()),
            InkSequenceBuilder.arrowDown(),
        )
    }
}

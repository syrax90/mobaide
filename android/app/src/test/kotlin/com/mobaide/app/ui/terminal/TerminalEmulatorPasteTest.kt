package com.mobaide.app.ui.terminal

import com.termux.terminal.TerminalEmulator
import com.termux.terminal.TerminalOutput
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * Unit tests for the sanitization contract of [TerminalEmulator.paste] — the single
 * pipeline the selection-toolbar "Paste" sink routes clipboard text through
 * (`TerminalSessionCallback.onPasteFromClipboard` → `session.emulator.paste(text)`).
 *
 * The contract that makes an attacker-controlled clipboard safe to paste:
 *
 * 1. ESC (0x1B) and C1 controls [0x80,0x9F] are stripped first, so no clipboard
 *    content can smuggle live escape sequences to the PTY.
 * 2. LF and CRLF become CR — terminal line discipline expects carriage returns.
 * 3. When (and only when) the PTY app enabled bracketed paste (DECSET 2004), the
 *    body is wrapped in a single live `ESC[200~` / `ESC[201~` pair, emitted as
 *    three separate write calls (open marker, body, close marker).
 *
 * Runs on the plain JVM against the real vendored emulator; a [RecordingOutput]
 * captures everything paste() writes toward the session. Bracketed-paste mode is
 * enabled the same way production does — by feeding the DECSET sequence through
 * [TerminalEmulator.append].
 */
class TerminalEmulatorPasteTest {

    /** Records every string [TerminalEmulator.paste] writes toward the session. */
    private class RecordingOutput : TerminalOutput() {
        val writes = mutableListOf<String>()
        val concatenated: String get() = writes.joinToString("")

        override fun write(data: ByteArray, offset: Int, count: Int) {
            writes += String(data, offset, count, Charsets.UTF_8)
        }
        override fun titleChanged(oldTitle: String?, newTitle: String?) {}
        override fun onCopyTextToClipboard(text: String?) {}
        override fun onPasteTextFromClipboard() {}
        override fun onBell() {}
        override fun onColorsChanged() {}
    }

    private val output = RecordingOutput()
    private val emulator =
        TerminalEmulator(output, 80, 24, 12, 24, TRANSCRIPT_ROWS, TerminalSessionCallback())

    private fun feed(sequence: String) {
        val bytes = sequence.toByteArray(Charsets.UTF_8)
        emulator.append(bytes, bytes.size)
    }

    private fun enableBracketedPaste() = feed(BRACKETED_PASTE_ON)

    private fun occurrencesOf(needle: String, haystack: String): Int =
        haystack.split(needle).size - 1

    @Test
    fun paste_singleLine_bracketedOff_writesTextVerbatim_withoutMarkers() {
        emulator.paste("hello world")

        assertEquals(
            "with DECSET 2004 off the body must pass through unwrapped — markers " +
                "would surface as literal text on non-bracketed shells",
            "hello world",
            output.concatenated,
        )
    }

    @Test
    fun paste_lfAndCrlf_convertToCr() {
        emulator.paste("a\nb\r\nc")

        assertEquals(
            "every LF and CRLF must become a single CR — terminal line discipline " +
                "expects carriage returns, and CRLF must not produce a double return",
            "a\rb\rc",
            output.concatenated,
        )
    }

    @Test
    fun paste_bracketedModeOn_wrapsInExactlyThreeWrites() {
        enableBracketedPaste()

        emulator.paste("a\nb")

        assertEquals(
            "bracketed paste is exactly three ordered writes: open marker, sanitized " +
                "body, close marker — the FIFO send path reassembles them downstream",
            listOf("[200~", "a\rb", "[201~"),
            output.writes,
        )
    }

    @Test
    fun paste_escAndC1Controls_stripped() {
        emulator.paste("a[31mred\u0090b")

        assertEquals(
            "ESC must be stripped (defanging the SGR to literal '[31m' text) and C1 " +
                "controls [0x80,0x9F] removed outright",
            "a[31mredb",
            output.concatenated,
        )
    }

    @Test
    fun paste_embeddedEndMarker_cannotTerminateBracketedPasteEarly() {
        enableBracketedPaste()

        emulator.paste("evil[201~; rm -rf ~\n")

        // The ESC strip runs before wrapping, so a clipboard that embeds a live
        // ESC[201~ cannot close the bracket early and have the tail execute as
        // direct input — it degrades to the literal text "[201~".
        assertEquals(
            listOf("[200~", "evil[201~; rm -rf ~\r", "[201~"),
            output.writes,
        )
        assertEquals(
            "exactly one live end marker may reach the PTY — the wrap's own",
            1,
            occurrencesOf("[201~", output.concatenated),
        )
    }

    @Test
    fun paste_preWrappedClipboard_producesNoNestedLiveMarkers() {
        enableBracketedPaste()

        // Clipboard already carrying a bracketed-paste block (e.g. copied from a
        // terminal transcript): the embedded markers must be defanged, leaving the
        // emulator's own wrap as the only live pair.
        emulator.paste("[200~body[201~")

        assertEquals(1, occurrencesOf("[200~", output.concatenated))
        assertEquals(1, occurrencesOf("[201~", output.concatenated))
        assertEquals("[200~[200~body[201~[201~", output.concatenated)
    }

    @Test
    fun paste_bracketedOff_strippedMultiline_neverEmitsEsc() {
        emulator.paste("line1\nline2[H")

        assertFalse(
            "with 2004 off and ESC stripped, no byte of emulator output may be ESC",
            output.concatenated.contains(''),
        )
        assertEquals("line1\rline2[H", output.concatenated)
    }

    private companion object {
        const val TRANSCRIPT_ROWS = 200
        const val BRACKETED_PASTE_ON = "[?2004h"
    }
}

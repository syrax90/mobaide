package com.mobaide.app.ui.autoscan

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import com.mobaide.app.ui.theme.AnsiColors

/**
 * Minimal hand-rolled ANSI SGR parser.
 *
 * Supports:
 *   - reset (0)
 *   - bold (1)
 *   - foreground colors 30-37, bright 90-97
 *   - 256-color foreground (38;5;n)
 *
 * Strips other CSI sequences (cursor moves, erase line, etc.).
 */
object AnsiParser {
    private const val ESC = '\u001B'

    fun parse(input: String): AnnotatedString = buildAnnotatedString {
        var fg: androidx.compose.ui.graphics.Color? = null
        var bold = false
        var i = 0

        fun applySpan(text: String) {
            if (text.isEmpty()) return
            val style = SpanStyle(
                color = fg ?: androidx.compose.ui.graphics.Color.Unspecified,
                fontWeight = if (bold) FontWeight.Bold else null,
            )
            withStyle(style) { append(text) }
        }

        val sb = StringBuilder()
        while (i < input.length) {
            val c = input[i]
            if (c == ESC && i + 1 < input.length && input[i + 1] == '[') {
                applySpan(sb.toString()); sb.clear()
                // Scan for CSI final byte (0x40–0x7E)
                val end = (i + 2 until input.length).firstOrNull { idx ->
                    val ch = input[idx]
                    ch in '\u0040'..'\u007E'
                } ?: break
                val params = input.substring(i + 2, end)
                val terminator = input[end]
                if (terminator == 'm') {
                    val codes = if (params.isEmpty()) listOf(0) else
                        params.split(';').mapNotNull { it.toIntOrNull() }
                    var j = 0
                    while (j < codes.size) {
                        val code = codes[j]
                        when {
                            code == 0 -> { fg = null; bold = false }
                            code == 1 -> bold = true
                            code == 22 -> bold = false
                            code == 39 -> fg = null
                            code in 30..37 -> fg = AnsiColors[code - 30]
                            code in 90..97 -> fg = AnsiColors[8 + (code - 90)]
                            code == 38 && j + 2 < codes.size && codes[j + 1] == 5 -> {
                                val n = codes[j + 2]
                                if (n in 0..15) fg = AnsiColors[n]
                                j += 2
                            }
                        }
                        j++
                    }
                }
                // Other terminators (K, J, H, etc.) are silently dropped.
                i = end + 1
            } else {
                sb.append(c)
                i++
            }
        }
        applySpan(sb.toString())
    }
}

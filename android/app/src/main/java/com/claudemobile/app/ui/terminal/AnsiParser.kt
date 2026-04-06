package com.claudemobile.app.ui.terminal

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import com.claudemobile.app.ui.theme.AnsiColors
import com.claudemobile.app.ui.theme.TerminalForeground

object AnsiParser {

    fun parse(raw: String): AnnotatedString = buildAnnotatedString {
        var fg: Color = TerminalForeground
        var bg: Color = Color.Transparent
        var bold = false
        var i = 0

        while (i < raw.length) {
            if (raw[i] == '\u001B' && i + 1 < raw.length && raw[i + 1] == '[') {
                // Parse CSI sequence
                i += 2
                val paramStart = i
                while (i < raw.length && raw[i] in '0'..'9' || (i < raw.length && raw[i] == ';')) {
                    i++
                }
                if (i < raw.length) {
                    val command = raw[i]
                    i++
                    if (command == 'm') {
                        // SGR sequence
                        val params = raw.substring(paramStart, i - 1)
                        val codes = if (params.isEmpty()) {
                            listOf(0)
                        } else {
                            params.split(';').mapNotNull { it.toIntOrNull() }
                        }
                        var ci = 0
                        while (ci < codes.size) {
                            when (val code = codes[ci]) {
                                0 -> { fg = TerminalForeground; bg = Color.Transparent; bold = false }
                                1 -> bold = true
                                22 -> bold = false
                                in 30..37 -> {
                                    fg = if (bold) AnsiColors[code - 30 + 8] else AnsiColors[code - 30]
                                }
                                39 -> fg = TerminalForeground
                                in 40..47 -> bg = AnsiColors[code - 40]
                                49 -> bg = Color.Transparent
                                in 90..97 -> fg = AnsiColors[code - 90 + 8]
                                in 100..107 -> bg = AnsiColors[code - 100 + 8]
                                38 -> {
                                    // Extended foreground: 38;5;N
                                    if (ci + 2 < codes.size && codes[ci + 1] == 5) {
                                        fg = color256(codes[ci + 2])
                                        ci += 2
                                    }
                                }
                                48 -> {
                                    // Extended background: 48;5;N
                                    if (ci + 2 < codes.size && codes[ci + 1] == 5) {
                                        bg = color256(codes[ci + 2])
                                        ci += 2
                                    }
                                }
                            }
                            ci++
                        }
                    }
                    // All other CSI commands (cursor movement, erase, etc.) are stripped
                }
            } else {
                // Regular character — emit with current style
                pushStyle(
                    SpanStyle(
                        color = fg,
                        background = bg,
                        fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal,
                    )
                )
                append(raw[i])
                pop()
                i++
            }
        }
    }

    private fun color256(n: Int): Color {
        if (n < 16) return AnsiColors[n]
        if (n in 16..231) {
            val idx = n - 16
            val r = (idx / 36) * 51
            val g = ((idx / 6) % 6) * 51
            val b = (idx % 6) * 51
            return Color(r, g, b)
        }
        // Grayscale 232-255
        val gray = (n - 232) * 10 + 8
        return Color(gray, gray, gray)
    }
}

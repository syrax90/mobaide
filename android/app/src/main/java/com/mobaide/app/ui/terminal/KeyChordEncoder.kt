package com.mobaide.app.ui.terminal

/**
 * A keyboard chord: at most two of Ctrl/Alt/Shift over a single base key from
 * [KeyChordKeys]. Persisted as a compact token ("ctrl+shift+i") so DataStore
 * never depends on this type — see [token] / [parse].
 */
data class KeyChord(
    val ctrl: Boolean = false,
    val alt: Boolean = false,
    val shift: Boolean = false,
    val key: String,
) {
    /** Canonical storage token: modifiers in ctrl, alt, shift order, key last, lowercase. */
    fun token(): String = buildString {
        if (ctrl) append("ctrl+")
        if (alt) append("alt+")
        if (shift) append("shift+")
        append(key)
    }

    /** Human label in conventional order, e.g. "Ctrl+Shift+I", "Alt+F4". */
    fun display(): String = buildString {
        if (ctrl) append("Ctrl+")
        if (alt) append("Alt+")
        if (shift) append("Shift+")
        append(KeyChordKeys.displayName(key))
    }

    companion object {
        /**
         * Inverse of [token]. Returns null for anything invalid — unknown key or
         * modifier, duplicate modifier, all three modifiers, blank — so callers can
         * silently drop stale or forward-incompatible persisted tokens.
         */
        fun parse(token: String): KeyChord? {
            val parts = token.split('+')
            val key = parts.last()
            if (key !in KeyChordKeys.all) return null
            var ctrl = false
            var alt = false
            var shift = false
            for (mod in parts.dropLast(1)) {
                when (mod) {
                    "ctrl" -> if (ctrl) return null else ctrl = true
                    "alt" -> if (alt) return null else alt = true
                    "shift" -> if (shift) return null else shift = true
                    else -> return null
                }
            }
            if (ctrl && alt && shift) return null
            return KeyChord(ctrl = ctrl, alt = alt, shift = shift, key = key)
        }
    }
}

/** The base-key domain of the shortcut picker, as canonical lowercase tokens. */
object KeyChordKeys {
    val letters: List<String> = ('a'..'z').map { it.toString() }
    val digits: List<String> = ('0'..'9').map { it.toString() }
    val functionKeys: List<String> = (1..12).map { "f$it" }

    // Esc is deliberately absent: it is already a control-row key, and Ctrl/Alt+Esc
    // are window-manager chords with no terminal meaning.
    val specials: List<String> = listOf("tab", "enter", "space", "backspace")

    val all: Set<String> = (letters + digits + functionKeys + specials).toSet()

    fun displayName(key: String): String = when (key) {
        in specials -> key.replaceFirstChar { it.uppercase() }
        else -> key.uppercase()
    }
}

/**
 * The exact wire form of an encoded chord. Plain class on purpose: a data class
 * would compare [bytes] by reference (the ControlKey pitfall) — compare fields
 * explicitly instead.
 *
 * [summary] renders the bytes for the picker preview ("0x09", "ESC 0x01",
 * "ESC [ 105 ; 6 u"), [usesCsiU] is the single source of truth for the
 * "may not work in every CLI" hint, and [sameAsPlainKey] discloses when a chord
 * collapses onto an unmodified special's byte (Ctrl+I → Tab, Ctrl+M → Enter).
 */
class ChordEncoding(
    val bytes: ByteArray,
    val summary: String,
    val usesCsiU: Boolean,
    val sameAsPlainKey: String?,
)

/**
 * Legacy-first chord-to-bytes encoder.
 *
 * Decision procedure, in order:
 *  1. F-keys always use their xterm forms — plain F1–F4 = SS3 `ESC O P..S`,
 *     plain F5–F12 = `CSI 15/17/18/19/20/21/23/24 ~`, and any modifier combo
 *     switches to `CSI 1;m P..S` / `CSI n;m ~` with m = 1+shift(1)+alt(2)+ctrl(4).
 *     Never CSI-u, never marked risky.
 *  2. Other keys compute a legacy core from (ctrl, shift, key): Ctrl+letter = C0
 *     (cp AND 0x1F), Shift+letter = uppercase, Shift+Tab = `CSI Z`,
 *     Ctrl+Space = NUL, Ctrl+Backspace = BS. The core FAILS whenever legacy
 *     encoding would silently drop a requested modifier (Ctrl+Shift+letter,
 *     any modified digit, Ctrl+Tab, modified Enter, Shift+Space/Backspace).
 *  3. Alt is an ESC prefix, composable only over a single-byte core
 *     (Alt+X = `ESC x`, Ctrl+Alt+A = `ESC 0x01`); over the multi-byte `CSI Z`
 *     core it cannot compose.
 *  4. Anything rule 2 or 3 rejects falls back to kitty CSI-u
 *     `ESC [ <codepoint> ; <m> u` (lowercase-letter codepoint) and is flagged
 *     [ChordEncoding.usesCsiU] — CLIs opt into that encoding, so it may echo as
 *     literal text in legacy-mode terminals.
 */
object KeyChordEncoder {

    fun encode(chord: KeyChord): ChordEncoding? {
        if (chord.key !in KeyChordKeys.all) return null
        if (chord.ctrl && chord.alt && chord.shift) return null
        val mods = 1 +
            (if (chord.shift) 1 else 0) +
            (if (chord.alt) 2 else 0) +
            (if (chord.ctrl) 4 else 0)
        val raw = if (chord.key in KeyChordKeys.functionKeys) {
            functionKeyForm(chord, mods)
        } else {
            legacyOrCsiU(chord, mods)
        }
        return ChordEncoding(
            bytes = raw.bytes,
            summary = raw.summary,
            usesCsiU = raw.usesCsiU,
            sameAsPlainKey = plainKeyCollision(chord, raw.bytes),
        )
    }

    private class Raw(val bytes: ByteArray, val summary: String, val usesCsiU: Boolean)

    private val SS3_FINALS = mapOf("f1" to 'P', "f2" to 'Q', "f3" to 'R', "f4" to 'S')
    private val TILDE_NUMBERS = mapOf(
        "f5" to 15, "f6" to 17, "f7" to 18, "f8" to 19,
        "f9" to 20, "f10" to 21, "f11" to 23, "f12" to 24,
    )

    private fun functionKeyForm(chord: KeyChord, mods: Int): Raw {
        val modified = chord.ctrl || chord.alt || chord.shift
        val ss3Final = SS3_FINALS[chord.key]
        return when {
            ss3Final != null && !modified -> ascii("O$ss3Final", "ESC O $ss3Final")
            ss3Final != null -> ascii("[1;$mods$ss3Final", "ESC [ 1 ; $mods $ss3Final")
            else -> {
                val n = TILDE_NUMBERS.getValue(chord.key)
                if (!modified) {
                    ascii("[$n~", "ESC [ $n ~")
                } else {
                    ascii("[$n;$mods~", "ESC [ $n ; $mods ~")
                }
            }
        }
    }

    private fun legacyOrCsiU(chord: KeyChord, mods: Int): Raw {
        val core = legacyCore(chord)
        return when {
            core == null -> csiU(chord.key, mods)
            !chord.alt -> core
            core.bytes.size == 1 ->
                Raw(byteArrayOf(0x1b) + core.bytes, "ESC ${core.summary}", usesCsiU = false)
            else -> csiU(chord.key, mods)
        }
    }

    private fun legacyCore(chord: KeyChord): Raw? = when {
        chord.key in KeyChordKeys.letters -> {
            val c = chord.key[0]
            when {
                chord.ctrl && chord.shift -> null
                chord.ctrl -> singleByte((c.code and 0x1F).toByte())
                chord.shift -> singleByte(c.uppercaseChar().code.toByte())
                else -> singleByte(c.code.toByte())
            }
        }
        chord.key in KeyChordKeys.digits ->
            if (chord.ctrl || chord.shift) null else singleByte(chord.key[0].code.toByte())
        chord.key == "tab" -> when {
            chord.ctrl -> null
            chord.shift -> ascii("[Z", "ESC [ Z")
            else -> singleByte(0x09)
        }
        chord.key == "enter" ->
            if (chord.ctrl || chord.shift) null else singleByte(0x0d)
        chord.key == "space" -> when {
            chord.shift -> null
            chord.ctrl -> singleByte(0x00)
            else -> singleByte(0x20)
        }
        chord.key == "backspace" -> when {
            chord.shift -> null
            chord.ctrl -> singleByte(0x08)
            else -> singleByte(0x7f)
        }
        else -> null
    }

    private fun csiU(key: String, mods: Int): Raw {
        val cp = when (key) {
            "tab" -> 9
            "enter" -> 13
            "space" -> 32
            "backspace" -> 127
            else -> key[0].code
        }
        return Raw(
            bytes = byteArrayOf(0x1b) + "[$cp;${mods}u".toByteArray(Charsets.US_ASCII),
            summary = "ESC [ $cp ; $mods u",
            usesCsiU = true,
        )
    }

    private fun plainKeyCollision(chord: KeyChord, bytes: ByteArray): String? {
        if (bytes.size != 1) return null
        val name = when (bytes[0].toInt()) {
            0x09 -> "Tab"
            0x0d -> "Enter"
            0x20 -> "Space"
            0x7f -> "Backspace"
            else -> return null
        }
        val isThatPlainSpecial = !chord.ctrl && !chord.alt && !chord.shift &&
            KeyChordKeys.displayName(chord.key) == name
        return if (isThatPlainSpecial) null else name
    }

    private fun singleByte(b: Byte): Raw =
        Raw(byteArrayOf(b), "0x%02X".format(b.toInt() and 0xFF), usesCsiU = false)

    /** [afterEsc] is the sequence body following the leading ESC byte. */
    private fun ascii(afterEsc: String, summary: String): Raw =
        Raw(byteArrayOf(0x1b) + afterEsc.toByteArray(Charsets.US_ASCII), summary, usesCsiU = false)
}

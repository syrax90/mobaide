package com.mobaide.app.data.network

import okio.ByteString
import okio.ByteString.Companion.toByteString

/**
 * Define the binary WebSocket frame protocol for terminal I/O.
 *
 * Every frame is a binary message whose first byte identifies the frame
 * type and whose remaining bytes carry the payload.
 *
 * | Byte | Char | Direction | Payload                                                |
 * |------|------|-----------|--------------------------------------------------------|
 * | 0x6f | 'o'  | S->C      | raw PTY output bytes (replay chunk or live)            |
 * | 0x69 | 'i'  | C->S      | raw input bytes (CR / Ctrl / Esc / UTF-8)              |
 * | 0x78 | 'x'  | C->S      | execute: server Ctrl+C clears, types payload, then CR  |
 * | 0x6d | 'm'  | C->S      | submit: server writes payload, pauses, then writes CR  |
 * | 0x72 | 'r'  | C->S      | resize: u16 rows + u16 cols (big-endian)               |
 * | 0x73 | 's'  | S->C      | state: UTF-8 JSON {status, pid?, exit_code?}           |
 * | 0x62 | 'b'  | S->C      | replay-buffer start marker (empty payload)             |
 * | 0x65 | 'e'  | S->C      | replay-buffer end marker — live frames follow (empty)  |
 * | 0x77 | 'w'  | S->C      | winsize: u16 rows + u16 cols + u16 attached clients    |
 * |      |      |           | (big-endian, proto-v8); truncated payload decodes null |
 *
 * Reconnect handshake (protocol v3): the server emits a fixed sequence of
 * ``state → 'b' → 'o'(chunk)…'o'(chunk) → 'e'`` after every WS open. The
 * client clears its local emulator on ``'b'``, appends every ``'o'``
 * payload, and transitions out of *ReplayingBuffer* only on ``'e'``.
 */
object TerminalProtocol {

    const val TYPE_OUTPUT: Byte = 'o'.code.toByte()
    const val TYPE_INPUT: Byte = 'i'.code.toByte()
    const val TYPE_EXECUTE: Byte = 'x'.code.toByte()
    const val TYPE_SUBMIT: Byte = 'm'.code.toByte()
    const val TYPE_RESIZE: Byte = 'r'.code.toByte()
    const val TYPE_STATE: Byte = 's'.code.toByte()
    const val TYPE_REPLAY: Byte = 'b'.code.toByte()
    const val TYPE_REPLAY_END: Byte = 'e'.code.toByte()
    const val TYPE_WINSIZE: Byte = 'w'.code.toByte()

    /** Wrap raw input bytes in an `'i'` frame. */
    fun encodeInput(bytes: ByteArray): ByteString {
        val out = ByteArray(bytes.size + 1)
        out[0] = TYPE_INPUT
        System.arraycopy(bytes, 0, out, 1, bytes.size)
        return out.toByteString()
    }

    /** Wrap a complete command in an `'x'` (execute) frame; the server clears stale PTY
     *  input with Ctrl+C, writes the payload, then submits it with a delayed CR. */
    fun encodeExecute(bytes: ByteArray): ByteString {
        val out = ByteArray(bytes.size + 1)
        out[0] = TYPE_EXECUTE
        System.arraycopy(bytes, 0, out, 1, bytes.size)
        return out.toByteString()
    }

    /** Wrap composer text in an `'m'` (submit) frame; the server writes the payload, pauses,
     *  then writes CR as a separate PTY write so Ink TUIs parse it as a discrete Enter
     *  instead of pasted content. */
    fun encodeSubmit(bytes: ByteArray): ByteString {
        val out = ByteArray(bytes.size + 1)
        out[0] = TYPE_SUBMIT
        System.arraycopy(bytes, 0, out, 1, bytes.size)
        return out.toByteString()
    }

    /** Build an `'r'` frame: type byte + uint16 rows + uint16 cols (big-endian). */
    fun encodeResize(rows: Int, cols: Int): ByteString {
        val out = ByteArray(5)
        out[0] = TYPE_RESIZE
        out[1] = ((rows ushr 8) and 0xff).toByte()
        out[2] = (rows and 0xff).toByte()
        out[3] = ((cols ushr 8) and 0xff).toByte()
        out[4] = (cols and 0xff).toByte()
        return out.toByteString()
    }

    fun decode(frame: ByteString): TerminalFrame? {
        if (frame.size == 0) return null
        return when (frame[0]) {
            TYPE_OUTPUT -> TerminalFrame.Output(frame.substring(1).toByteArray())
            TYPE_STATE -> TerminalFrame.State(frame.substring(1).utf8())
            TYPE_REPLAY -> TerminalFrame.ReplayMarker
            TYPE_REPLAY_END -> TerminalFrame.ReplayEndMarker
            TYPE_WINSIZE -> {
                if (frame.size < 7) return null
                TerminalFrame.Winsize(
                    rows = readU16(frame, 1),
                    cols = readU16(frame, 3),
                    attachedClients = readU16(frame, 5),
                )
            }
            else -> null
        }
    }

    private fun readU16(frame: ByteString, offset: Int): Int =
        ((frame[offset].toInt() and 0xff) shl 8) or (frame[offset + 1].toInt() and 0xff)
}

sealed class TerminalFrame {
    data class Output(val bytes: ByteArray) : TerminalFrame() {
        override fun equals(other: Any?): Boolean =
            other is Output && bytes.contentEquals(other.bytes)
        override fun hashCode(): Int = bytes.contentHashCode()
    }
    data class State(val json: String) : TerminalFrame()
    data object ReplayMarker : TerminalFrame()
    data object ReplayEndMarker : TerminalFrame()
    data class Winsize(val rows: Int, val cols: Int, val attachedClients: Int) : TerminalFrame()
}

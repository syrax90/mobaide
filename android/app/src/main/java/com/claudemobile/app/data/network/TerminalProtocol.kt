package com.claudemobile.app.data.network

import okio.ByteString
import okio.ByteString.Companion.toByteString

/**
 * Binary WebSocket frame protocol for terminal I/O (CLA-13).
 *
 * Every frame is a binary message whose first byte identifies the frame
 * type and whose remaining bytes carry the payload. This replaces the old
 * JSON+base64 wire format and lets raw PTY bytes round-trip end-to-end.
 *
 * | Byte | Char | Direction | Payload                                       |
 * |------|------|-----------|-----------------------------------------------|
 * | 0x6f | 'o'  | S->C      | raw PTY output bytes                          |
 * | 0x69 | 'i'  | C->S      | raw input bytes (CR / Ctrl / Esc / UTF-8)     |
 * | 0x78 | 'x'  | C->S      | execute: server Ctrl+C clears, then writes     |
 * | 0x72 | 'r'  | C->S      | resize: u16 rows + u16 cols (big-endian)      |
 * | 0x73 | 's'  | S->C      | state: UTF-8 JSON {status, pid?, exit_code?}  |
 * | 0x62 | 'b'  | S->C      | replay-buffer marker (empty payload)          |
 */
object TerminalProtocol {

    const val TYPE_OUTPUT: Byte = 'o'.code.toByte()
    const val TYPE_INPUT: Byte = 'i'.code.toByte()
    const val TYPE_EXECUTE: Byte = 'x'.code.toByte()
    const val TYPE_RESIZE: Byte = 'r'.code.toByte()
    const val TYPE_STATE: Byte = 's'.code.toByte()
    const val TYPE_REPLAY: Byte = 'b'.code.toByte()

    /** Wrap raw input bytes in an `'i'` frame. */
    fun encodeInput(bytes: ByteArray): ByteString {
        val out = ByteArray(bytes.size + 1)
        out[0] = TYPE_INPUT
        System.arraycopy(bytes, 0, out, 1, bytes.size)
        return out.toByteString()
    }

    /** Wrap a complete command in an `'x'` (execute) frame.
     *  The server sends Ctrl+C to cancel stale PTY input, pauses to let
     *  the TUI show a fresh prompt, then writes the payload. */
    fun encodeExecute(bytes: ByteArray): ByteString {
        val out = ByteArray(bytes.size + 1)
        out[0] = TYPE_EXECUTE
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
            else -> null
        }
    }
}

sealed class TerminalFrame {
    data class Output(val bytes: ByteArray) : TerminalFrame() {
        override fun equals(other: Any?): Boolean =
            other is Output && bytes.contentEquals(other.bytes)
        override fun hashCode(): Int = bytes.contentHashCode()
    }
    data class State(val json: String) : TerminalFrame()
    data object ReplayMarker : TerminalFrame()
}

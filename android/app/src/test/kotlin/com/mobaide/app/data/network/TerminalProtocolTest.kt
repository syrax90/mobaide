package com.mobaide.app.data.network

import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [TerminalProtocol] — byte-level framing for the terminal
 * WebSocket protocol. Every frame must begin with a single type byte; resize
 * frames additionally pack two big-endian u16 fields.
 */
class TerminalProtocolTest {

    // ---- encodeInput --------------------------------------------------------

    @Test
    fun encodeInput_prependsTypeByte_andPreservesPayload() {
        val payload = byteArrayOf(0x68, 0x69, 0x0d) // "hi\r"
        val frame = TerminalProtocol.encodeInput(payload).toByteArray()

        assertEquals("frame length must be payload + 1 type byte", 4, frame.size)
        assertEquals("first byte must be 'i'", 'i'.code.toByte(), frame[0])
        assertArrayEquals(
            "remaining bytes must match payload verbatim",
            payload,
            frame.copyOfRange(1, frame.size),
        )
    }

    @Test
    fun encodeInput_preservesHighBitBytes() {
        // 0xFF must not be sign-extended or clamped — raw PTY output may carry
        // any byte value including UTF-8 continuation bytes.
        val payload = byteArrayOf(0xFF.toByte(), 0x80.toByte(), 0x00)
        val frame = TerminalProtocol.encodeInput(payload).toByteArray()

        assertEquals('i'.code.toByte(), frame[0])
        assertEquals(0xFF.toByte(), frame[1])
        assertEquals(0x80.toByte(), frame[2])
        // Explicit Byte cast — JUnit 4's assertEquals(Object, Object) auto-boxes
        // `Int` and `Byte` into distinct wrapper types that compare unequal.
        assertEquals(0x00.toByte(), frame[3])
    }

    // ---- encodeExecute -----------------------------------------------------

    @Test
    fun encodeExecute_prependsTypeByte_andPreservesPayload() {
        val payload = byteArrayOf(0x68, 0x69, 0x0d) // "hi\r"
        val frame = TerminalProtocol.encodeExecute(payload).toByteArray()

        assertEquals("frame length must be payload + 1 type byte", 4, frame.size)
        assertEquals("first byte must be 'x'", 'x'.code.toByte(), frame[0])
        assertArrayEquals(
            "remaining bytes must match payload verbatim",
            payload,
            frame.copyOfRange(1, frame.size),
        )
    }

    @Test
    fun encodeSubmit_prependsTypeByte_andPreservesPayload() {
        val payload = byteArrayOf(0x68, 0x69) // "hi" — no CR; the server appends it
        val frame = TerminalProtocol.encodeSubmit(payload).toByteArray()

        assertEquals("frame length must be payload + 1 type byte", 3, frame.size)
        assertEquals("first byte must be 'm'", 'm'.code.toByte(), frame[0])
        assertArrayEquals(
            "remaining bytes must match payload verbatim",
            payload,
            frame.copyOfRange(1, frame.size),
        )
    }

    // ---- encodeResize -------------------------------------------------------

    @Test
    fun encodeResize_packsRowsAndColsAsBigEndianU16() {
        // rows = 300 = 0x012C  -> hi=0x01, lo=0x2C
        // cols = 500 = 0x01F4  -> hi=0x01, lo=0xF4
        val frame = TerminalProtocol.encodeResize(rows = 300, cols = 500).toByteArray()

        assertEquals(5, frame.size)
        assertEquals('r'.code.toByte(), frame[0])
        assertEquals(0x01.toByte(), frame[1])
        assertEquals(0x2C.toByte(), frame[2])
        assertEquals(0x01.toByte(), frame[3])
        assertEquals(0xF4.toByte(), frame[4])
    }

    @Test
    fun encodeResize_maxU16Boundary() {
        // 0xFFFF in both fields must serialize as 0xFF,0xFF,0xFF,0xFF.
        val frame = TerminalProtocol.encodeResize(rows = 0xFFFF, cols = 0xFFFF).toByteArray()

        assertEquals('r'.code.toByte(), frame[0])
        assertEquals(0xFF.toByte(), frame[1])
        assertEquals(0xFF.toByte(), frame[2])
        assertEquals(0xFF.toByte(), frame[3])
        assertEquals(0xFF.toByte(), frame[4])
    }

    // ---- decode -------------------------------------------------------------

    @Test
    fun decode_emptyFrame_returnsNull() {
        val result = TerminalProtocol.decode(ByteArray(0).toByteString())
        assertNull(result)
    }

    @Test
    fun decode_output_wrapsPayloadBytes() {
        val bytes = byteArrayOf('h'.code.toByte(), 'i'.code.toByte())
        val wire = (byteArrayOf('o'.code.toByte()) + bytes).toByteString()

        val decoded = TerminalProtocol.decode(wire)
        assertTrue(decoded is TerminalFrame.Output)
        val out = decoded as TerminalFrame.Output
        assertArrayEquals(bytes, out.bytes)
    }

    @Test
    fun decode_state_decodesJsonAsUtf8() {
        val json = """{"status":"running","pid":42}"""
        val wire = (byteArrayOf('s'.code.toByte()) + json.toByteArray(Charsets.UTF_8)).toByteString()

        val decoded = TerminalProtocol.decode(wire)
        assertTrue(decoded is TerminalFrame.State)
        assertEquals(json, (decoded as TerminalFrame.State).json)
    }

    @Test
    fun decode_replayMarker_returnsSingletonObject() {
        val wire = byteArrayOf('b'.code.toByte()).toByteString()
        val decoded = TerminalProtocol.decode(wire)
        assertEquals(TerminalFrame.ReplayMarker, decoded)
    }

    @Test
    fun decode_replayEndMarker_returnsSingletonObject() {
        // Proto-v3 terminator for the chunked-replay handshake. Must decode
        // distinctly from ReplayMarker; the ViewModel uses it to flip out of
        // ReplayingBuffer back to Live.
        val wire = byteArrayOf('e'.code.toByte()).toByteString()
        val decoded = TerminalProtocol.decode(wire)
        assertEquals(TerminalFrame.ReplayEndMarker, decoded)
    }

    @Test
    fun decode_inputFrame_returnsNull_becauseDecodeIsServerToClientOnly() {
        // 'i' is a client->server type; server never sends it, decode must not
        // accept it. Same for 'r' (resize).
        val inputWire = byteArrayOf('i'.code.toByte(), 0x61).toByteString()
        val resizeWire = byteArrayOf('r'.code.toByte(), 0, 24, 0, 80).toByteString()

        assertNull(TerminalProtocol.decode(inputWire))
        assertNull(TerminalProtocol.decode(resizeWire))
    }

    @Test
    fun decode_unknownType_returnsNull() {
        val wire = byteArrayOf(0x7A, 0x01, 0x02).toByteString() // 'z' + junk
        assertNull(TerminalProtocol.decode(wire))
    }
}

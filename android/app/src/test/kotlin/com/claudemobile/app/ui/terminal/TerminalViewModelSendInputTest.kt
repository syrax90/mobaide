package com.claudemobile.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.repository.SessionRepository
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import okio.ByteString
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.sendInput] — verifies the layered PTY-clearing
 * byte sequence (Ctrl+U, Ctrl+E, DEL x N) prepended before the user's text + CR.
 *
 * The [WebSocketConnection] is mocked so no real network I/O occurs; the drain
 * coroutine running on Dispatchers.IO picks up the buffered channel entry and
 * forwards it to `connection.sendBinary`, where we capture the raw bytes.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelSendInputTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
    }
    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("test-session") } returns connection
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
        )

    /**
     * Extract the raw PTY payload from the captured 'i'-framed [ByteString].
     * [com.claudemobile.app.data.network.TerminalProtocol.encodeInput] prepends
     * a single 'i' type byte, so we strip it to get the bytes that `sendInput` built.
     */
    private fun payloadOf(frame: ByteString): ByteArray =
        frame.toByteArray().let { it.copyOfRange(1, it.size) }

    // ---- Structure of the clearing prefix --------------------------------------

    @Test
    fun sendInput_prependsCtrlU_thenCtrlE_thenDelBytes_beforeTextAndCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("hello")

        // The drain coroutine runs on Dispatchers.IO — give it time to process.
        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)

        // First byte: Ctrl+U (0x15)
        assertEquals("first byte must be Ctrl+U", 0x15.toByte(), payload[0])
        // Second byte: Ctrl+E (0x05)
        assertEquals("second byte must be Ctrl+E", 0x05.toByte(), payload[1])

        // clearLength = maxOf("hello".length, 200) = 200
        val expectedClearLen = maxOf("hello".length, 200)
        // Bytes [2 .. 2+expectedClearLen) must all be DEL (0x7F)
        val delRegion = payload.copyOfRange(2, 2 + expectedClearLen)
        assertTrue(
            "all bytes in the DEL region must be 0x7F",
            delRegion.all { it == 0x7F.toByte() },
        )

        // After the clear prefix: the text + CR
        val textRegion = payload.copyOfRange(2 + expectedClearLen, payload.size)
        assertArrayEquals(
            "text + CR must follow the clearing sequence",
            "hello\r".toByteArray(Charsets.UTF_8),
            textRegion,
        )
    }

    // ---- clearLength uses maxOf(text.length, 200) ------------------------------

    @Test
    fun sendInput_shortText_clearLengthIsAtLeast200() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("x")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        // "x".length = 1, so clearLength = maxOf(1, 200) = 200
        // Total payload: 1 (Ctrl+U) + 1 (Ctrl+E) + 200 (DELs) + 1 ("x") + 1 (\r) = 204
        assertEquals("payload length for short text", 204, payload.size)
    }

    @Test
    fun sendInput_longText_clearLengthMatchesTextLength() {
        val longText = "a".repeat(300)
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput(longText)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        // clearLength = maxOf(300, 200) = 300
        // Total payload: 1 + 1 + 300 + 300 + 1 = 603
        assertEquals("payload length for long text", 603, payload.size)

        // Verify the DEL region has exactly 300 bytes
        val delRegion = payload.copyOfRange(2, 2 + 300)
        assertTrue(
            "all 300 DEL bytes must be 0x7F",
            delRegion.all { it == 0x7F.toByte() },
        )
    }

    // ---- Empty input -----------------------------------------------------------

    @Test
    fun sendInput_emptyString_sendsMinimumClear_plusBareCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        // clearLength = maxOf(0, 200) = 200
        // Total: 1 + 1 + 200 + 0 + 1 = 203
        assertEquals("payload length for empty input", 203, payload.size)

        // Trailing byte is CR
        assertEquals("last byte must be CR", '\r'.code.toByte(), payload[payload.size - 1])
    }

    // ---- UTF-8 multi-byte text -------------------------------------------------

    @Test
    fun sendInput_utf8Text_encodesCorrectlyAfterClearPrefix() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        // "cafe" in French: "cafe\u0301" — but let's use a simpler multi-byte: "hello"
        // Use actual multi-byte: "\u00e9" (e-acute) is 2 bytes in UTF-8
        vm.sendInput("\u00e9")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        // clearLength = maxOf(1 /* single char */, 200) = 200
        val textRegion = payload.copyOfRange(2 + 200, payload.size)
        // "\u00e9" in UTF-8 = 0xC3 0xA9, plus CR = 3 bytes
        assertArrayEquals(
            "UTF-8 multi-byte char + CR must be preserved",
            "\u00e9\r".toByteArray(Charsets.UTF_8),
            textRegion,
        )
    }

    // ---- Frame type byte -------------------------------------------------------

    @Test
    fun sendInput_wrapsPayloadInTerminalProtocolInputFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("test")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val frame = captured.captured.toByteArray()
        assertEquals(
            "first byte of the WebSocket frame must be 'i' (input type)",
            'i'.code.toByte(),
            frame[0],
        )
    }
}

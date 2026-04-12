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
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.sendInput] — verifies that the method
 * sends the user's text followed by CR, wrapped in a terminal-protocol 'i' frame.
 *
 * When `clearFirst = true` (text came from tapping the current input line),
 * clearing bytes are prepended atomically in the same frame.
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

    /** Strip the 'i' frame-type prefix to get the raw PTY payload. */
    private fun payloadOf(frame: ByteString): ByteArray =
        frame.toByteArray().let { it.copyOfRange(1, it.size) }

    @Test
    fun sendInput_sendsTextFollowedByCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("hello")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "hello\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_emptyString_sendsBareCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_utf8Text_encodesCorrectly() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        // "\u00e9" (e-acute) is 2 bytes in UTF-8 (0xC3 0xA9)
        vm.sendInput("\u00e9")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "\u00e9\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_wrapsPayloadInTerminalProtocolInputFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("test")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "first byte of the WebSocket frame must be 'i' (input type)",
            'i'.code.toByte(),
            captured.captured.toByteArray()[0],
        )
    }

    // ── clearFirst tests ────────────────────────────────────────────────────────

    @Test
    fun sendInput_clearFirst_prependsClearingBytesBeforeTextAndCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("/stats", clearFirst = true)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        val clearPrefix = byteArrayOf(0x15, 0x05) +
            ByteArray(maxOf("/stats".length, 200)) { 0x7F.toByte() }
        val expected = clearPrefix + "/stats\r".toByteArray(Charsets.UTF_8)

        assertArrayEquals(expected, payload)
    }

    @Test
    fun sendInput_clearFirst_false_doesNotPrependClearingBytes() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("/stats", clearFirst = false)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "/stats\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_clearFirst_sendsAsSingleFrame() {
        val vm = createViewModel()
        val captured = mutableListOf<ByteString>()

        vm.sendInput("/stats", clearFirst = true)

        verify(timeout = 2000, exactly = 1) { connection.sendBinary(capture(captured)) }
        assertEquals(1, captured.size)
        assertEquals(
            "frame must start with 'i' type byte",
            'i'.code.toByte(),
            captured[0].toByteArray()[0],
        )
    }

    @Test
    fun sendInput_clearFirst_longText_delCountMatchesTextLength() {
        val longText = "a".repeat(300)
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput(longText, clearFirst = true)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        // maxOf(300, 200) = 300 — DEL count must scale with text length
        val expectedDelCount = 300
        val clearPrefix = byteArrayOf(0x15, 0x05) +
            ByteArray(expectedDelCount) { 0x7F.toByte() }
        val expected = clearPrefix + (longText + "\r").toByteArray(Charsets.UTF_8)

        assertArrayEquals(expected, payload)
    }

    @Test
    fun sendInput_clearFirst_emptyText_uses200Dels_plusBareCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("", clearFirst = true)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        val payload = payloadOf(captured.captured)
        // maxOf(0, 200) = 200 — minimum DEL count even for empty input
        val clearPrefix = byteArrayOf(0x15, 0x05) +
            ByteArray(200) { 0x7F.toByte() }
        val expected = clearPrefix + "\r".toByteArray(Charsets.UTF_8)

        assertArrayEquals(expected, payload)
    }
}

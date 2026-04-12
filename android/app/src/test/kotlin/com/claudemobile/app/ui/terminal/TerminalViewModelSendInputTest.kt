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
 * sends the user's text followed by CR, wrapped in a terminal-protocol 'x'
 * (execute) frame so the server clears stale PTY input before writing.
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

    /** Strip the frame-type prefix byte to get the raw PTY payload. */
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
    fun sendInput_wrapsPayloadInExecuteFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("test")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "first byte of the WebSocket frame must be 'x' (execute type)",
            'x'.code.toByte(),
            captured.captured.toByteArray()[0],
        )
    }
}

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
 * Unit tests for [TerminalViewModel.sendInput] — verifies the three send paths:
 *
 * 1. **Unchanged tap**: text matches [TerminalViewModel.onRowTapped] → bare CR via 'i' frame
 * 2. **Edited tap**: text differs from tapped original → 'x' (execute) frame
 * 3. **Fresh text**: no prior [onRowTapped] call → text + CR via 'i' frame
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

    private fun frameTypeOf(frame: ByteString): Byte = frame.toByteArray()[0]

    // ── Path 1: Fresh text (no tap) ────────────────────────────────────────────

    @Test
    fun sendInput_freshText_sendsTextPlusCR() {
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
    fun sendInput_freshText_usesInputFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("hello")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "fresh text must use 'i' (input) frame",
            'i'.code.toByte(),
            frameTypeOf(captured.captured),
        )
    }

    @Test
    fun sendInput_freshText_emptyString_sendsBareCR() {
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
    fun sendInput_freshText_utf8_encodesCorrectly() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("\u00e9")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "\u00e9\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    // ── Path 2: Unchanged tap → bare CR ────────────────────────────────────────

    @Test
    fun sendInput_unchangedTap_sendsBareCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.onRowTapped("/stats")
        vm.sendInput("/stats")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "unchanged tap on current input must send bare CR",
            "\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_unchangedTap_usesInputFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.onRowTapped("/stats")
        vm.sendInput("/stats")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "unchanged tap must use 'i' (input) frame",
            'i'.code.toByte(),
            frameTypeOf(captured.captured),
        )
    }

    // ── Path 3: Edited tap → 'x' (execute) frame ──────────────────────────────

    @Test
    fun sendInput_editedTap_sendsEditedTextPlusCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.onRowTapped("/stats")
        vm.sendInput("/help")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "edited tap must send the new text + CR",
            "/help\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_editedTap_usesExecuteFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.onRowTapped("/stats")
        vm.sendInput("/help")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "edited tap must use 'x' (execute) frame for server-side Ctrl+C clear",
            'x'.code.toByte(),
            frameTypeOf(captured.captured),
        )
    }

    // ── tappedOriginal reset ───────────────────────────────────────────────────

    @Test
    fun sendInput_consumesTappedOriginal_subsequentSendIsFresh() {
        val vm = createViewModel()
        val captured = mutableListOf<ByteString>()

        vm.onRowTapped("/stats")
        vm.sendInput("/stats")  // consumes tappedOriginal

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }

        vm.sendInput("/stats")  // should be fresh (no tappedOriginal)

        verify(timeout = 2000, atLeast = 2) { connection.sendBinary(capture(captured)) }
        val second = captured.last()
        assertArrayEquals(
            "second send after tap consumed should be fresh text + CR",
            "/stats\r".toByteArray(Charsets.UTF_8),
            payloadOf(second),
        )
    }

    @Test
    fun sendBytes_resetsTappedOriginal() {
        val vm = createViewModel()
        val captured = mutableListOf<ByteString>()

        vm.onRowTapped("/stats")
        vm.sendBytes(byteArrayOf(0x03))  // Ctrl+C invalidates tap state

        vm.sendInput("/stats")  // should be fresh now

        verify(timeout = 2000, atLeast = 2) { connection.sendBinary(capture(captured)) }
        val sendInputFrame = captured.last()
        assertEquals(
            "sendBytes must reset tappedOriginal — subsequent sendInput uses 'i' frame",
            'i'.code.toByte(),
            frameTypeOf(sendInputFrame),
        )
    }
}

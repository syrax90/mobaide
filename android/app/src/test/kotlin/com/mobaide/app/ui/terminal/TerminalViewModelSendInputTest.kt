package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okio.ByteString
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel]'s send paths.
 *
 * [TerminalViewModel.sendInput] always encodes the fresh typed text + CR as a single
 * 'i' (input) frame. [TerminalViewModel.sendTappedRow] is the "tap `❯` row = Enter"
 * entrypoint; it dispatches on its `isCurrentInput` flag:
 *
 * - `isCurrentInput = true`  → bare CR via 'i' frame (PTY already holds the text).
 * - `isCurrentInput = false` → `text + "\r"` via 'x' (execute) frame so the server
 *   Ctrl+C-clears stale input before writing.
 *
 * [TerminalViewModel.sendTappedRow] also no-ops when the socket is not Live, to avoid
 * queueing frames during reconnect / replay-buffer playback.
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
     * Drive the VM's `_connectionState` to [ConnectionState.Live] by emitting a
     * [WsEvent.Connected] and yielding to the test dispatcher so the VM's collector
     * can consume it.
     */
    private suspend fun TestScope.goLive() {
        eventsFlow.emit(WsEvent.Connected)
        advanceUntilIdle()
    }

    /** Strip the frame-type prefix byte to get the raw PTY payload. */
    private fun payloadOf(frame: ByteString): ByteArray =
        frame.toByteArray().let { it.copyOfRange(1, it.size) }

    private fun frameTypeOf(frame: ByteString): Byte = frame.toByteArray()[0]

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

        vm.sendInput("é")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "é\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendTappedRow_currentInput_sendsBareCR() = runTest {
        val vm = createViewModel()
        goLive()
        val captured = slot<ByteString>()

        vm.sendTappedRow("/stats", isCurrentInput = true)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "tap on the current input row must submit with a bare CR",
            "\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendTappedRow_currentInput_usesInputFrame() = runTest {
        val vm = createViewModel()
        goLive()
        val captured = slot<ByteString>()

        vm.sendTappedRow("/stats", isCurrentInput = true)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "current-input tap must use 'i' (input) frame",
            'i'.code.toByte(),
            frameTypeOf(captured.captured),
        )
    }

    @Test
    fun sendTappedRow_historicalInput_sendsTextPlusCR() = runTest {
        val vm = createViewModel()
        goLive()
        val captured = slot<ByteString>()

        vm.sendTappedRow("/help", isCurrentInput = false)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "historical-row tap must send the tapped text + CR",
            "/help\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendTappedRow_historicalInput_usesExecuteFrame() = runTest {
        val vm = createViewModel()
        goLive()
        val captured = slot<ByteString>()

        vm.sendTappedRow("/help", isCurrentInput = false)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "historical-row tap must use 'x' (execute) frame so server Ctrl+C-clears stale input",
            'x'.code.toByte(),
            frameTypeOf(captured.captured),
        )
    }

    @Test
    fun sendTappedRow_whenNotLive_doesNotSend() {
        val vm = createViewModel()

        vm.sendTappedRow("/stats", isCurrentInput = true)
        vm.sendTappedRow("/help", isCurrentInput = false)

        verify(exactly = 0) { connection.sendBinary(any()) }
    }
}

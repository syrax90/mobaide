package com.mobaide.app.ui.terminal

import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
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
 * [TerminalViewModel.sendInput] prepends a Ctrl+A + Ctrl+K line clear (0x01, 0x0B) to
 * the fresh typed text + CR, then encodes the whole buffer as a single 'i' (input)
 * frame — the clear wipes stale prompt text so the message replaces (never concatenates
 * onto) it, with no SIGINT so a running task is never interrupted.
 *
 * [TerminalViewModel.sendTappedRow] is the "tap `❯` row = Enter"
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
        every { observeSessionList() } returns MutableStateFlow(emptyList())
    }

    private val templatesRepository = mockk<TemplatesRepository>(relaxed = true)
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            templatesRepository = templatesRepository,
            preferencesStore = preferencesStore,
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

    /** The Ctrl+A (0x01) + Ctrl+K (0x0B) line-clear bytes [TerminalViewModel.sendInput]
     *  prepends to every composer send (see CLEAR_INPUT_LINE). */
    private val clearPrefix = byteArrayOf(0x01, 0x0B)

    @Test
    fun sendInput_freshText_sendsTextPlusCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("hello")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            clearPrefix + "hello\r".toByteArray(Charsets.UTF_8),
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
    fun sendInput_freshText_emptyString_sendsClearThenCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "empty composer send still clears the line, then submits a bare CR",
            clearPrefix + "\r".toByteArray(Charsets.UTF_8),
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
            clearPrefix + "é\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_multilineText_wrapsInBracketedPasteMarkers() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("line1\nline2")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "multi-line input must be wrapped with xterm bracketed-paste markers",
            clearPrefix + "[200~line1\nline2[201~\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_multilineText_usesInputFrame() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("line1\nline2")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "multi-line input must still use 'i' (input) frame, not a new frame type",
            'i'.code.toByte(),
            frameTypeOf(captured.captured),
        )
    }

    @Test
    fun sendInput_multilineText_markerBytes() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("a\nb")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        val payload = payloadOf(captured.captured)

        // Typo guard: assert the literal escape-sequence bytes for the markers,
        // not the Kotlin string literal that produced them. If someone replaces
        // "[200~" with "[200~" the bytes will differ even if the source
        // still "looks right".
        val startMarker = byteArrayOf(0x1B, 0x5B, 0x32, 0x30, 0x30, 0x7E)
        val endMarker = byteArrayOf(0x1B, 0x5B, 0x32, 0x30, 0x31, 0x7E)

        assertArrayEquals(
            "start marker must be ESC [ 2 0 0 ~  (0x1B 0x5B 0x32 0x30 0x30 0x7E)",
            startMarker,
            // Offset 2, not 0: the 2-byte Ctrl+A/Ctrl+K line clear precedes the marker.
            payload.copyOfRange(2, 8),
        )
        assertArrayEquals(
            "end marker must be ESC [ 2 0 1 ~  (0x1B 0x5B 0x32 0x30 0x31 0x7E)",
            endMarker,
            payload.copyOfRange(payload.size - 7, payload.size - 1),
        )
        assertEquals(
            "payload must end with CR after the close marker",
            '\r'.code.toByte(),
            payload[payload.size - 1],
        )
    }

    @Test
    fun sendInput_singleLineText_doesNotWrap() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("single line")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "single-line input (no LF) must NOT be wrapped — markers would surface on non-Claude shells",
            clearPrefix + "single line\r".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun sendInput_prependsLineClear_ctrlA_thenCtrlK() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("ls")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        val payload = payloadOf(captured.captured)
        // Typo guard: assert the literal clear bytes regardless of how CLEAR_INPUT_LINE
        // is spelled in source. Ctrl+A = 0x01 (move to start), Ctrl+K = 0x0B (kill to end).
        assertEquals(
            "composer send must start with Ctrl+A (0x01) to clear the line",
            0x01.toByte(),
            payload[0],
        )
        assertEquals(
            "Ctrl+A must be immediately followed by Ctrl+K (0x0B)",
            0x0B.toByte(),
            payload[1],
        )
    }

    @Test
    fun sendInput_clearsComposerAfterSend() = runTest {
        val vm = createViewModel()
        vm.updateInputText(TextFieldValue("stale composer text"))

        vm.sendInput("hello")
        advanceUntilIdle()

        // The composer must be emptied once the message is queued: the line clear
        // wipes the PTY's prompt line, and leaving the sent text in the input bar
        // would invite an accidental duplicate send of the same message.
        assertEquals(
            "sendInput must reset the composer to empty after dispatching the frame",
            "",
            vm.inputText.value.text,
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

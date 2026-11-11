package com.mobaide.app.ui.terminal

import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
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
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Unit tests for [TerminalViewModel]'s send paths.
 *
 * [TerminalViewModel.sendInput] prepends a Ctrl+A + Ctrl+K line clear (0x01, 0x0B) to
 * the fresh typed text, then encodes the buffer as a single 'm' (submit) frame — the
 * clear wipes stale prompt text so the message replaces (never concatenates onto) it,
 * with no SIGINT so a running task is never interrupted. No CR travels with the text:
 * the server submits with a delayed CR as its own PTY write, because Ink TUIs treat a
 * CR inside the same stdin chunk as pasted content rather than the Enter key.
 *
 * [TerminalViewModel.sendTappedRow] is the "tap `❯` row = Enter"
 * entrypoint; it dispatches on its `isCurrentInput` flag:
 *
 * - `isCurrentInput = true`  → bare CR via 'i' frame (PTY already holds the text).
 * - `isCurrentInput = false` → `text` via 'x' (execute) frame so the server
 *   Ctrl+C-clears stale input, writes the text, then submits with a delayed CR.
 *
 * [TerminalViewModel.sendTappedRow] also no-ops when the socket is not Live, to avoid
 * queueing frames during reconnect / replay-buffer playback.
 *
 * Uses Robolectric so [TerminalViewModel]'s `parseStatus` — which parses the `'s'`
 * (state) frame JSON via `org.json.JSONObject` — resolves through Robolectric's shadow
 * instead of the stub `android.jar` (which returns defaults under the project's
 * `unitTests.isReturnDefaultValues = true` config; see `TerminalViewModelConnectTest`).
 * Needed for [markSessionRunning] to actually drive `sessionState` to `"running"`.
 * SDK pinned to 33 to match the rest of the JVM/Robolectric suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
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
        every { observeSessionsSynced() } returns MutableStateFlow(false)
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
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = templatesRepository,
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = mockk(relaxed = true),
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

    /** Drive `sessionState` to "running" via the binary state frame the server sends. */
    private suspend fun TestScope.markSessionRunning() {
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE)
                    .plus("""{"status":"running","pid":1234}""".toByteArray(Charsets.UTF_8))
                    .toByteString(),
            ),
        )
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
    fun sendInput_freshText_sendsTextWithoutCR() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("hello")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "no CR may travel with the text — the server appends it as a delayed write",
            clearPrefix + "hello".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
        assertEquals('m'.code.toByte(), frameTypeOf(captured.captured))
    }

    @Test
    fun sendInput_multilineText_wrapsInBracketedPasteMarkers() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("line1\nline2")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "multi-line input must be wrapped with xterm bracketed-paste markers",
            clearPrefix + "[200~line1\nline2[201~".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
        assertEquals('m'.code.toByte(), frameTypeOf(captured.captured))
    }

    @Test
    fun sendInput_singleLineText_doesNotWrap() {
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendInput("single line")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "single-line input (no LF) must NOT be wrapped — markers would surface on non-Claude shells",
            clearPrefix + "single line".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
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
        assertEquals('i'.code.toByte(), frameTypeOf(captured.captured))
    }

    @Test
    fun sendTappedRow_historicalInput_sendsTextWithoutCR() = runTest {
        val vm = createViewModel()
        goLive()
        val captured = slot<ByteString>()

        vm.sendTappedRow("/help", isCurrentInput = false)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "historical-row tap must send the tapped text without CR — the server appends it",
            "/help".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
        assertEquals(
            "historical-row tap must use 'x' (execute): server Ctrl+C-clears, types, submits",
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

    @Test
    fun terminalOriginatedBytes_whenNotLive_areDropped() {
        val vm = createViewModel()

        // The vendored TerminalView/emulator path (direct IME typing, auto-replies)
        // funnels through TerminalSession.write → the VM's input listener. Its Live
        // gate mirrors sendTappedRow: a keyboard raised while the session was running
        // must not keep feeding a stopped/replaying PTY.
        vm.session.write("x".toByteArray(Charsets.UTF_8), 0, 1)

        verify(exactly = 0) { connection.sendBinary(any()) }
    }

    @Test
    fun terminalOriginatedBytes_whenLiveAndRunning_flowAsInputFrames() = runTest {
        val vm = createViewModel()
        goLive()
        markSessionRunning()
        val captured = slot<ByteString>()

        vm.session.write("x".toByteArray(Charsets.UTF_8), 0, 1)

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals('i'.code.toByte(), frameTypeOf(captured.captured))
        assertArrayEquals("x".toByteArray(Charsets.UTF_8), payloadOf(captured.captured))
    }

    @Test
    fun terminalOriginatedBytes_whenLiveButSessionNotRunning_areDropped() = runTest {
        val vm = createViewModel()
        goLive() // Live WS, but sessionState still "connecting" — PTY not running

        vm.session.write("x".toByteArray(Charsets.UTF_8), 0, 1)

        // A session that stopped or crashed while the WS stayed Live must not keep
        // receiving keystrokes from an already-raised terminal IME.
        verify(exactly = 0) { connection.sendBinary(any()) }
    }
}

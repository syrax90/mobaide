package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.ForegroundSessionTracker
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
 * Unit tests for the selection-toolbar Paste path through [TerminalViewModel]'s
 * input listener.
 *
 * The ActionMode "Paste" sink calls `session.emulator.paste(text)`
 * (`TerminalSessionCallback.onPasteFromClipboard` wired in `TerminalSurfaceView`),
 * whose sanitized output lands in `TerminalSession.write` → the VM's
 * `setOnInputListener` → `'i'` (input) frames over `connection.sendBinary`. That
 * listener drops bytes unless the socket is [ConnectionState.Live] AND
 * `sessionState == "running"` — so pasted bytes inherit the same gate as direct
 * IME typing, with no paste-specific gating code.
 *
 * Tests drive [com.termux.terminal.TerminalEmulator.paste] on the VM's real
 * JVM-hosted emulator, exactly as the production sink does. Bracketed-paste mode
 * is enabled by feeding DECSET 2004 through `emulator.append`, mirroring a TUI
 * (e.g. Claude Code) switching the mode on.
 *
 * Scaffolding cloned from [TerminalViewModelSendInputTest]; see there for why
 * Robolectric (sdk 33) is required for [markSessionRunning]'s `'s'`-frame JSON
 * parsing.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelPasteTest {

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
            templatesRepository = templatesRepository,
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = preferencesStore,
        )

    /** Drive the VM's `_connectionState` to [ConnectionState.Live]. */
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

    /** What the production Paste sink does with the clipboard text it read. */
    private fun paste(vm: TerminalViewModel, text: String) {
        requireNotNull(vm.session.emulator).paste(text)
    }

    /** Feed DECSET 2004 (bracketed paste on) to the emulator, as a TUI would. */
    private fun enableBracketedPaste(vm: TerminalViewModel) {
        val bytes = BRACKETED_PASTE_ON.toByteArray(Charsets.UTF_8)
        requireNotNull(vm.session.emulator).append(bytes, bytes.size)
    }

    /** Strip the frame-type prefix byte to get the raw PTY payload. */
    private fun payloadOf(frame: ByteString): ByteArray =
        frame.toByteArray().let { it.copyOfRange(1, it.size) }

    private fun frameTypeOf(frame: ByteString): Byte = frame.toByteArray()[0]

    @Test
    fun paste_whenLiveAndRunning_sendsInputFrameWithPayload() = runTest {
        val vm = createViewModel()
        goLive()
        markSessionRunning()
        val captured = slot<ByteString>()

        paste(vm, "hello")

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "pasted bytes must travel as 'i' (input) frames — never 'm' (submit)",
            'i'.code.toByte(),
            frameTypeOf(captured.captured),
        )
        assertArrayEquals("hello".toByteArray(Charsets.UTF_8), payloadOf(captured.captured))
    }

    @Test
    fun paste_multiline_bracketedOff_singleFrameWithLfConvertedToCr() = runTest {
        val vm = createViewModel()
        goLive()
        markSessionRunning()
        val captured = slot<ByteString>()

        paste(vm, "a\nb")

        verify(timeout = 2000, exactly = 1) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "without DECSET 2004 the emulator must send the LF→CR body unwrapped",
            "a\rb".toByteArray(Charsets.UTF_8),
            payloadOf(captured.captured),
        )
    }

    @Test
    fun paste_multiline_bracketedOn_exactlyThreeInputFrames_wrappingBody() = runTest {
        val vm = createViewModel()
        goLive()
        markSessionRunning()
        enableBracketedPaste(vm)
        val frames = mutableListOf<ByteString>()

        paste(vm, "a\nb")

        // Three separate emulator writes → three ordered 'i' frames over the FIFO
        // send channel (the PTY reassembles them); they must never be merged and
        // must never double-wrap (paste bytes bypass sendInput's 'm'-frame encoder).
        verify(timeout = 2000, exactly = 3) { connection.sendBinary(capture(frames)) }
        frames.forEach { frame ->
            assertEquals('i'.code.toByte(), frameTypeOf(frame))
        }
        assertArrayEquals(
            "frames must concatenate to exactly one bracketed-paste wrap around the body",
            "[200~a\rb[201~".toByteArray(Charsets.UTF_8),
            frames.flatMap { payloadOf(it).toList() }.toByteArray(),
        )
    }

    @Test
    fun paste_whenNotLive_isDropped() {
        val vm = createViewModel()

        paste(vm, "hello")

        // Reconnect/replay must not queue stale clipboard bytes for the PTY.
        verify(exactly = 0) { connection.sendBinary(any()) }
    }

    @Test
    fun paste_whenLiveButSessionNotRunning_isDropped() = runTest {
        val vm = createViewModel()
        goLive() // Live WS, but sessionState still "connecting" — PTY not running

        paste(vm, "hello")

        // A stale Paste tap after the session stopped or crashed (menu built while
        // it was still running) must be a silent no-op.
        verify(exactly = 0) { connection.sendBinary(any()) }
    }

    private companion object {
        const val BRACKETED_PASTE_ON = "[?2004h"
    }
}

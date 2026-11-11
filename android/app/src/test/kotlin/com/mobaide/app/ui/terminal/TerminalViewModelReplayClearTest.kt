package com.mobaide.app.ui.terminal

import android.os.Looper
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
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * End-to-end byte-level guard for the clear-before-replay path in
 * `TerminalViewModel.handleBinary`.
 *
 * The agent server re-sends `state + REPLAY marker + full ring buffer`
 * on every mobile reconnect (`mobaide_agent/.../session_io.py:139-146`).
 * Without a screen clear between the marker and the ring playback, the
 * recent history is appended on top of itself — visible to the user as
 * duplicated output after the lifecycle nudge in `TerminalScreen` forces
 * the WS through a fresh handshake.  The fix emits
 * `ESC[H ESC[2J ESC[3J` from inside the marker branch.
 *
 * The byte-level effect cannot be observed in a plain JVM unit test
 * because [com.termux.terminal.TerminalSession.emitOutput] posts through
 * `mMainThreadHandler` (an Android [android.os.Handler] tied to
 * `Looper.getMainLooper()`) — those classes are stubbed to no-ops in
 * non-Android JVM tests.  This file therefore uses Robolectric to spin
 * a real main `Looper` and idles it explicitly before assertions.
 *
 * SDK pinned to 33 to match the rest of the JVM/Robolectric suite (see
 * `TerminalViewMeasureTest`).
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelReplayClearTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    private fun newEventsFlow(): MutableSharedFlow<WsEvent> =
        MutableSharedFlow(replay = 1, extraBufferCapacity = 100)

    private fun newConnection(eventsFlow: MutableSharedFlow<WsEvent>): WebSocketConnection =
        mockk(relaxed = true) {
            every { events } returns eventsFlow
        }

    private fun newRepository(conn: WebSocketConnection): SessionRepository =
        mockk {
            every { createSessionIoConnection("test-session") } returns conn
            every { observeSessionList() } returns MutableStateFlow(emptyList())
            every { observeSessionsSynced() } returns MutableStateFlow(false)
        }

    private val templatesRepository =
        mockk<com.mobaide.app.data.repository.TemplatesRepository>(relaxed = true)

    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun newViewModel(repo: SessionRepository): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repo,
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = templatesRepository,
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = mockk(relaxed = true),
        )

    private fun outputFrame(payload: ByteArray): ByteString =
        byteArrayOf(TerminalProtocol.TYPE_OUTPUT).plus(payload).toByteString()

    private val replayMarkerFrame: ByteString =
        byteArrayOf(TerminalProtocol.TYPE_REPLAY).toByteString()

    /** Drain everything queued on the main `Looper` — `TerminalSession.emitOutput`
     *  posts the append+notify Runnable here. */
    private fun idleMainLooper() {
        shadowOf(Looper.getMainLooper()).idle()
    }

    @Test
    fun replayMarker_followedByOutput_writesAtOrigin_notAppendedAfterStaleContent() =
        runTest(testDispatcher) {
            // Whole-pipeline guard: marker → output should leave the screen
            // showing only the replayed content, not the pre-existing
            // content followed by the replay.  We can read the first row
            // back via `getSelectedText(x1, y1, x2, y2)` and confirm.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
                eventsFlow.tryEmit(WsEvent.Binary(outputFrame("stale-content".toByteArray())))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            idleMainLooper()

            eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
            eventsFlow.tryEmit(WsEvent.Binary(outputFrame("REPLAY".toByteArray())))
            runCurrent()
            idleMainLooper()

            val emulator = vm.session.emulator
                ?: error("emulator must exist after replay handshake")
            // After marker + replay output, the visible row 0 must read as
            // "REPLAY" only — not "stale-contentREPLAY" or any prefix of
            // the stale buffer.  Use the first 6 columns to compare.
            val row0 = emulator.screen.getSelectedText(0, 0, 5, 0)
            assertEquals("REPLAY", row0)
            // Cursor must have advanced past the replay output (col 6).
            assertEquals(6, emulator.cursorCol)
        }
}

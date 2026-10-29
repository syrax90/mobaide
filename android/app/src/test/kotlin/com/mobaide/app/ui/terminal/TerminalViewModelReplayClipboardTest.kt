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
import com.mobaide.app.data.repository.TemplatesRepository
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
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
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
import java.util.Base64

/**
 * Guards that an OSC 52 clipboard write the agent *replayed* never reaches the system
 * clipboard, while one that arrives live still does.
 *
 * The agent replays its 4 MiB PTY ring verbatim on every attach, so a single vim/tmux
 * yank left anywhere in that history re-copied on every session open — the user saw the
 * OS "Copied" chip and lost their clipboard for merely opening a session.
 *
 * Robolectric is mandatory, not stylistic, and so is the frame path. `emitOutput` posts
 * the append to `Looper.getMainLooper()` while the collector drains the buffered frame
 * burst without suspending, so `inReplay` is already false by the time a replayed byte is
 * parsed — the flag has to travel *with* the bytes. Every case here therefore emits the
 * whole `'b' → 'o' → 'e'` burst before idling the Looper once, which is the ordering
 * production sees; a test calling `emitOutput` or `emulator.append` directly would pass
 * against unfixed code. `android.util.Base64` (the emulator's OSC 52 decoder) is stubbed
 * on a plain JVM, so the SDK is pinned to 33 like the rest of the Robolectric suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelReplayClipboardTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

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

    /** Everything the View layer's clipboard sink was asked to copy, in order. */
    private val copied = mutableListOf<String>()

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
        ).also { it.sessionCallback.onCopyToClipboard = { text -> copied += text } }

    private fun outputFrame(payload: ByteArray): ByteString =
        byteArrayOf(TerminalProtocol.TYPE_OUTPUT).plus(payload).toByteString()

    private fun markerFrame(type: Byte): ByteString = byteArrayOf(type).toByteString()

    /** `ESC]52;c;<base64>BEL` — the write form, targeting the system clipboard. */
    private fun osc52Write(text: String): ByteArray {
        val encoded = Base64.getEncoder().encodeToString(text.toByteArray(Charsets.UTF_8))
        return "\u001B]52;c;$encoded\u0007".toByteArray(Charsets.US_ASCII)
    }

    private suspend fun TestScope.goLive() {
        eventsFlow.emit(WsEvent.Connected)
        advanceUntilIdle()
    }

    /** Run every append `emitOutput` has posted to the main `Looper`. */
    private fun idleMainLooper() {
        shadowOf(Looper.getMainLooper()).idle()
    }

    /** The visible first row, which pins that a frame really was parsed. */
    private fun firstRow(vm: TerminalViewModel, width: Int): String =
        requireNotNull(vm.session.emulator).screen.getSelectedText(0, 0, width - 1, 0)

    @Test
    fun replayedOsc52_neverReachesTheClipboard() =
        runTest(testDispatcher) {
            val vm = createViewModel()
            goLive()

            eventsFlow.emit(WsEvent.Binary(markerFrame(TerminalProtocol.TYPE_REPLAY)))
            eventsFlow.emit(
                WsEvent.Binary(
                    outputFrame(osc52Write("replayed").plus("HISTORY".toByteArray(Charsets.US_ASCII))),
                ),
            )
            eventsFlow.emit(WsEvent.Binary(markerFrame(TerminalProtocol.TYPE_REPLAY_END)))
            advanceUntilIdle()
            idleMainLooper()

            assertEquals(
                "the replayed screen content still has to be painted — only the clipboard write is dropped",
                "HISTORY",
                firstRow(vm, 7),
            )
            assertEquals(emptyList<String>(), copied)
        }

    @Test
    fun liveOsc52_stillReachesTheClipboard() =
        runTest(testDispatcher) {
            createViewModel()
            goLive()

            eventsFlow.emit(WsEvent.Binary(outputFrame(osc52Write("yanked"))))
            advanceUntilIdle()
            idleMainLooper()

            assertEquals(
                "a yank the user just performed is the half of OSC 52 this app keeps",
                listOf("yanked"),
                copied,
            )
        }

    @Test
    fun textSelectionCopy_rightAfterAReplaySpan_stillReachesTheClipboard() =
        runTest(testDispatcher) {
            // The sink is shared with the ActionMode "Copy" item, which
            // `TextSelectionCursorController` routes through this exact call from its own
            // main-thread message. A gate latched for the span rather than for the append
            // would eat the user's own copy.
            val vm = createViewModel()
            goLive()
            eventsFlow.emit(WsEvent.Binary(markerFrame(TerminalProtocol.TYPE_REPLAY)))
            eventsFlow.emit(WsEvent.Binary(outputFrame(osc52Write("replayed"))))
            eventsFlow.emit(WsEvent.Binary(markerFrame(TerminalProtocol.TYPE_REPLAY_END)))
            advanceUntilIdle()
            idleMainLooper()

            vm.session.onCopyTextToClipboard("selected text")

            assertEquals(listOf("selected text"), copied)
        }
}

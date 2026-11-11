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
 * Byte-accumulation guard for the protocol-v3 chunked-replay path in
 * [TerminalViewModel.handleBinary].
 *
 * The agent server emits `state → REPLAY → N × OUTPUT(chunk) → REPLAY_END`
 * on every reconnect; chunking exists so each `emitOutput` finishes well
 * under a UI frame. The state-machine half is covered by
 * `TerminalViewModelConnectTest` (replay-end is the *only* transition out
 * of `ReplayingBuffer`). This file pins the byte-level half: when N > 1,
 * every chunk must be written to the emulator in order, not just the last
 * one.
 *
 * Uses Robolectric for the same reason as `TerminalViewModelReplayClearTest`
 * — `com.termux.terminal.TerminalSession.emitOutput` posts the append+notify
 * Runnable through `mMainThreadHandler`, which is a no-op outside a real
 * Looper. SDK pinned to 33 to match the rest of the JVM/Robolectric suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelChunkedReplayTest {

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

    private val replayEndFrame: ByteString =
        byteArrayOf(TerminalProtocol.TYPE_REPLAY_END).toByteString()

    private fun idleMainLooper() {
        shadowOf(Looper.getMainLooper()).idle()
    }

    @Test
    fun chunkedReplay_keepsReplayingBufferAcrossAllOutputFrames() =
        runTest(testDispatcher) {
            // The ReplayingBuffer overlay must persist across every chunk in
            // the burst — not flip back to Live after the first output frame
            // (the protocol-v2 behaviour the v3 bump explicitly removed).
            // Drives the chunks one event at a time and asserts the state
            // between each so a "first output ends replay" regression would
            // fail loudly on the second assertion, not just at the end.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
                eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            idleMainLooper()
            assertEquals(
                "pre-condition: replay marker puts VM in ReplayingBuffer",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            eventsFlow.tryEmit(WsEvent.Binary(outputFrame("a".toByteArray())))
            runCurrent()
            idleMainLooper()
            assertEquals(
                "first chunk must NOT end replay",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            eventsFlow.tryEmit(WsEvent.Binary(outputFrame("b".toByteArray())))
            runCurrent()
            idleMainLooper()
            assertEquals(
                "second chunk must NOT end replay either",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            eventsFlow.tryEmit(WsEvent.Binary(outputFrame("c".toByteArray())))
            runCurrent()
            idleMainLooper()
            assertEquals(
                "third chunk must NOT end replay either",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            eventsFlow.tryEmit(WsEvent.Binary(replayEndFrame))
            runCurrent()
            idleMainLooper()
            assertEquals(
                "only REPLAY_END flips the overlay back to Live",
                ConnectionState.Live,
                vm.connectionState.value,
            )

            val emulator = vm.session.emulator
                ?: error("emulator must exist after the replay handshake")
            val row0 = emulator.screen.getSelectedText(0, 0, 2, 0)
            assertEquals("abc", row0)
        }

    @Test
    fun chunkedReplay_emptyOutputChunks_areNoOps_butStillRespectTerminator() =
        runTest(testDispatcher) {
            // The server emits an empty FRAME_OUTPUT when the ring is empty
            // (preserves the "always one output frame" invariant). An empty
            // 'o' between two non-empty chunks must not interrupt the
            // sequence — handleBinary skips emitOutput when bytes.isEmpty
            // but the state remains ReplayingBuffer until REPLAY_END.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
                eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
                eventsFlow.tryEmit(WsEvent.Binary(outputFrame("xx".toByteArray())))
                eventsFlow.tryEmit(WsEvent.Binary(outputFrame(ByteArray(0))))
                eventsFlow.tryEmit(WsEvent.Binary(outputFrame("yy".toByteArray())))
                eventsFlow.tryEmit(WsEvent.Binary(replayEndFrame))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            idleMainLooper()

            val emulator = vm.session.emulator
                ?: error("emulator must exist after the replay handshake")
            val row0 = emulator.screen.getSelectedText(0, 0, 3, 0)
            assertEquals("xxyy", row0)
            assertEquals(ConnectionState.Live, vm.connectionState.value)
        }
}

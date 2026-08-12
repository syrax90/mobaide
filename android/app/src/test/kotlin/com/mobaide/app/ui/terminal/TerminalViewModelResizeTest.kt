package com.mobaide.app.ui.terminal

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
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.resize] — verifies the debounce and
 * deduplication logic added to prevent rapid-fire PTY resize events from
 * rotation, composer show/hide, and display-mode changes (the keyboard no
 * longer resizes the terminal — it pans over it).
 *
 * Key behaviors:
 * 1. Same dimensions are deduplicated (no frame sent at all).
 * 2. Rapid calls within the 100ms debounce window cancel previous sends;
 *    only the final dimensions are transmitted.
 * 3. After the debounce delay, a single 'r' (resize) frame is sent via
 *    the sendChannel → connection.sendBinary path.
 * 4. On every entry into ConnectionState.Live, the cached size is force
 *    re-asserted (bypassing the debounce and dedupe) so a PTY left at its
 *    24x80 spawn default is corrected even when the measuring layout pass
 *    settled before the socket connected. Mirrors iOS resendCachedSize().
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelResizeTest {

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
    private val templatesRepository = mockk<com.mobaide.app.data.repository.TemplatesRepository>(relaxed = true)
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
        )

    // ── Deduplication ─────────────────────────────────────────────────────────

    @Test
    fun resize_sameDimensionsTwice_sendsOnlyOneFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent() // let init coroutines start

        vm.resize(24, 80)
        vm.resize(24, 80) // identical — should be a no-op

        advanceTimeBy(200) // well past the 100ms debounce
        runCurrent()

        // Exactly one resize frame should have been sent.
        val expected = TerminalProtocol.encodeResize(24, 80)
        verify(exactly = 1) { connection.sendBinary(expected) }
    }

    // ── Debounce: rapid calls coalesce ────────────────────────────────────────

    @Test
    fun resize_rapidCalls_onlyLastDimensionsSent() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // Simulate keyboard animation: size changes rapidly.
        vm.resize(30, 80)
        advanceTimeBy(20) // 20ms — still within debounce window
        runCurrent()

        vm.resize(28, 80)
        advanceTimeBy(20) // 40ms total
        runCurrent()

        vm.resize(24, 80) // final size after animation settles
        advanceTimeBy(200) // 200ms — well past debounce for the last call
        runCurrent()

        // Only the final dimensions should have been sent to the server.
        val finalFrame = TerminalProtocol.encodeResize(24, 80)
        verify(timeout = 2000) { connection.sendBinary(finalFrame) }

        // Intermediate sizes should NOT have been sent.
        val intermediateFrame1 = TerminalProtocol.encodeResize(30, 80)
        val intermediateFrame2 = TerminalProtocol.encodeResize(28, 80)
        verify(exactly = 0) { connection.sendBinary(intermediateFrame1) }
        verify(exactly = 0) { connection.sendBinary(intermediateFrame2) }
    }

    // ── Frame format ──────────────────────────────────────────────────────────

    @Test
    fun resize_sendsCorrectResizeFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        vm.resize(50, 120)
        advanceTimeBy(200)
        runCurrent()

        val expected = TerminalProtocol.encodeResize(50, 120)
        verify(timeout = 2000) { connection.sendBinary(expected) }
    }

    // ── Debounce resets on new dimensions ─────────────────────────────────────

    @Test
    fun resize_newDimensionsAfterDebounce_sendsSecondFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // First resize settles.
        vm.resize(24, 80)
        advanceTimeBy(200)
        runCurrent()

        // Second resize with different dimensions.
        vm.resize(40, 100)
        advanceTimeBy(200)
        runCurrent()

        val first = TerminalProtocol.encodeResize(24, 80)
        val second = TerminalProtocol.encodeResize(40, 100)
        verify(exactly = 1) { connection.sendBinary(first) }
        verify(exactly = 1) { connection.sendBinary(second) }
    }

    // ── Zero/edge dimensions ──────────────────────────────────────────────────

    @Test
    fun resize_changingOnlyRows_sendsNewFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        vm.resize(24, 80)
        advanceTimeBy(200)
        runCurrent()

        vm.resize(30, 80) // only rows changed
        advanceTimeBy(200)
        runCurrent()

        val second = TerminalProtocol.encodeResize(30, 80)
        verify(exactly = 1) { connection.sendBinary(second) }
    }

    @Test
    fun resize_changingOnlyCols_sendsNewFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        vm.resize(24, 80)
        advanceTimeBy(200)
        runCurrent()

        vm.resize(24, 120) // only cols changed
        advanceTimeBy(200)
        runCurrent()

        val second = TerminalProtocol.encodeResize(24, 120)
        verify(exactly = 1) { connection.sendBinary(second) }
    }

    @Test
    fun enterLive_reAssertsCachedSize_evenWithoutPostConnectResize() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // A layout pass measured the grid before the socket connected. The
        // debounced frame is still pending — virtual time is never advanced past
        // the 100ms delay — modelling the race where that pre-connect frame is
        // dropped by the transport in production.
        vm.resize(40, 100)

        // Socket becomes Live: enterLiveState() must force the cached size out.
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()

        // resendCachedSize() is the only thing that could have sent this frame by
        // now — the debounce has not fired — so its arrival proves the connect-time
        // re-assert, not the layout debounce, delivered the size.
        val expected = TerminalProtocol.encodeResize(40, 100)
        verify(timeout = 2000) { connection.sendBinary(expected) }
    }

    @Test
    fun enterLive_forceResendBypassesDedupe_reEmitsSameSize() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // The size settles and is delivered once, so the cache now equals the
        // frame already on the wire — resize()'s dedupe would suppress re-sending it.
        vm.resize(24, 80)
        advanceTimeBy(200)
        runCurrent()

        // Connecting force-re-sends the cached size regardless of the dedupe,
        // because a freshly (re)spawned PTY is back at its 24x80 default.
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()

        // Debounce sent it once; the connect-time resend sends it again → >= 2.
        val expected = TerminalProtocol.encodeResize(24, 80)
        verify(atLeast = 2, timeout = 2000) { connection.sendBinary(expected) }
    }

    @Test
    fun enterLive_noSizeMeasuredYet_sendsNoFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // connect-before-layout: no resize() has run, so lastResize{Rows,Cols}
        // are still the -1 sentinel and resendCachedSize() must no-op rather than
        // push a bogus grid.
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()

        verify(exactly = 0) { connection.sendBinary(any()) }
    }

    @Test
    fun reconnect_reAssertsCachedSize_onEveryLiveEntry() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        vm.resize(24, 80)
        advanceTimeBy(200)
        runCurrent()

        val expected = TerminalProtocol.encodeResize(24, 80)

        // First connect: the debounced frame plus one connect-time resend.
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
        verify(atLeast = 2, timeout = 2000) { connection.sendBinary(expected) }

        // Drop, then reconnect: the PTY a reconnect finds still at its spawn
        // default of 24x80 must be re-corrected, so entering Live again emits a
        // further resend.
        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
        verify(atLeast = 3, timeout = 2000) { connection.sendBinary(expected) }
    }

    @Test
    fun binaryUpgrade_missedConnected_reAssertsCachedSize() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        vm.resize(50, 120)
        advanceTimeBy(200)
        runCurrent()

        // No WsEvent.Connected arrives: inbound data lands while still Reconnecting,
        // so handleBinary()'s safety upgrade is the sole path into Live. It reaches
        // the same enterLiveState() choke point and must re-assert the cached size.
        val outputFrame = byteArrayOf(TerminalProtocol.TYPE_OUTPUT)
            .plus("x".toByteArray())
            .toByteString()
        eventsFlow.emit(WsEvent.Binary(outputFrame))
        runCurrent()

        val expected = TerminalProtocol.encodeResize(50, 120)
        verify(atLeast = 2, timeout = 2000) { connection.sendBinary(expected) }
    }
}

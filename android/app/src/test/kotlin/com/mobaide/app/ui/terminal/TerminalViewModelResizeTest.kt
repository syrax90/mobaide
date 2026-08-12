package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
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
import kotlinx.coroutines.test.TestScope
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
            preferencesStore = preferencesStore,
        )

    /**
     * Fresh view models start as [SizeAuthority.Follower] (proto-v8
     * latest-active arbitration), so [TerminalViewModel.resize] discards
     * every layout pass until a solo `'w'` (or the replay-end fallback)
     * resolves the role. Deliver one and stay connected — this frame is
     * itself the model's first inbound binary, so it also performs
     * `handleBinary`'s "first binary out of Reconnecting" upgrade, leaving
     * [ConnectionState.Live] + [SizeAuthority.Solo] for the tests below.
     *
     * A trailing `WsEvent.Disconnected` is deliberately NOT emitted here
     * (round 2): a drop now demotes to [SizeAuthority.Follower] until the
     * reconnect handshake re-arbitrates, so resetting to Reconnecting this
     * way would leave every caller below a follower, not a solo, and none
     * of the debounce/dedupe/connect-reassert behavior under test would fire.
     * Tests that specifically need to exercise a real drop do so explicitly.
     */
    private suspend fun TestScope.promoteToSolo() {
        // A `w` before any state frame on the socket is ignored (it may be a
        // pre-registration relay broadcast), so lead with the handshake's
        // state frame exactly as the agent does.
        eventsFlow.emit(
            WsEvent.Binary(
                (byteArrayOf(TerminalProtocol.TYPE_STATE) +
                    """{"status":"running"}""".toByteArray()).toByteString(),
            ),
        )
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_WINSIZE, 0, 24, 0, 80, 0, 1).toByteString(),
            ),
        )
        runCurrent()
    }

    // ── Deduplication ─────────────────────────────────────────────────────────

    @Test
    fun resize_sameDimensionsTwice_sendsOnlyOneFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent() // let init coroutines start
        promoteToSolo()

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
        promoteToSolo()

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
        promoteToSolo()

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
        promoteToSolo()

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
        promoteToSolo()

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
        promoteToSolo()

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
        promoteToSolo()

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
        promoteToSolo()

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
    fun reconnect_reAssertsCachedSize_onceTheHandshakeRePromotes() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        promoteToSolo()

        vm.resize(24, 80)
        advanceTimeBy(200)
        runCurrent()

        val expected = TerminalProtocol.encodeResize(24, 80)

        // First connect: the debounced frame plus one connect-time resend.
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
        verify(atLeast = 2, timeout = 2000) { connection.sendBinary(expected) }

        // A drop forfeits authority (round 2): the reconnect handshake must
        // arbitrate before any size leaves, so a bare Connected on the new
        // socket — with no `w` yet — must NOT resend the stale grid, or an
        // ex-owner resuming from the background would steal the PTY with
        // zero user activity.
        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
        verify(exactly = 2) { connection.sendBinary(expected) }

        // The handshake's solo `w` (naming a grid other than the cache) is
        // what re-promotes and delivers the corrective. It must follow the
        // handshake's state frame — a `w` before one is ignored as a
        // pre-registration broadcast.
        eventsFlow.emit(
            WsEvent.Binary(
                (byteArrayOf(TerminalProtocol.TYPE_STATE) +
                    """{"status":"running"}""".toByteArray()).toByteString(),
            ),
        )
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_WINSIZE, 0, 10, 0, 40, 0, 1).toByteString(),
            ),
        )
        runCurrent()
        verify(atLeast = 3, timeout = 2000) { connection.sendBinary(expected) }
    }

    @Test
    fun binaryUpgrade_missedConnected_reAssertsCachedSize() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        promoteToSolo()

        vm.resize(50, 120)
        advanceTimeBy(200)
        runCurrent()

        // A drop demotes to Follower and resets to Reconnecting (round 2). No
        // WsEvent.Connected arrives on the new socket either: the handshake's
        // state frame is the first inbound binary (triggering handleBinary()'s
        // safety upgrade into Live), and the solo `w` that follows it
        // re-promotes authority and delivers the corrective.
        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        eventsFlow.emit(
            WsEvent.Binary(
                (byteArrayOf(TerminalProtocol.TYPE_STATE) +
                    """{"status":"running"}""".toByteArray()).toByteString(),
            ),
        )
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_WINSIZE, 0, 24, 0, 80, 0, 1).toByteString(),
            ),
        )
        runCurrent()

        val expected = TerminalProtocol.encodeResize(50, 120)
        verify(atLeast = 2, timeout = 2000) { connection.sendBinary(expected) }
    }
}

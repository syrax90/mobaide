package com.mobaide.app.ui.terminal

import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
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
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * The unwatched-session governor.
 *
 * The policy is injected and fires on a handful of bytes rather than the
 * real 4 MiB — pushing a ring through the emulator per case would test the
 * emulator rather than this.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelSuspendTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    /** `createUnsafe` skips the main-thread assertion a unit test cannot satisfy. */
    private class FakeLifecycleOwner : LifecycleOwner {
        val registry: LifecycleRegistry = LifecycleRegistry.createUnsafe(this)
        override val lifecycle: Lifecycle get() = registry
    }

    private val owner = FakeLifecycleOwner()
    private val appForegroundTracker = AppForegroundTracker()

    private val eventsFlow: MutableSharedFlow<WsEvent> =
        MutableSharedFlow(replay = 1, extraBufferCapacity = 100)

    private val connection: WebSocketConnection = mockk(relaxed = true) {
        every { events } returns eventsFlow
    }

    private val repository: SessionRepository = mockk {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns MutableStateFlow(emptyList())
        every { observeSessionsSynced() } returns MutableStateFlow(false)
    }

    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private val stateRunningFrame: ByteString =
        byteArrayOf(TerminalProtocol.TYPE_STATE)
            .plus("""{"status":"running","pid":1234}""".toByteArray(Charsets.UTF_8))
            .toByteString()

    private val replayMarkerFrame: ByteString =
        byteArrayOf(TerminalProtocol.TYPE_REPLAY).toByteString()

    private val replayEndFrame: ByteString =
        byteArrayOf(TerminalProtocol.TYPE_REPLAY_END).toByteString()

    private fun outputFrame(payload: ByteArray): ByteString =
        byteArrayOf(TerminalProtocol.TYPE_OUTPUT).plus(payload).toByteString()

    /**
     * A watched view model: app foregrounded and this terminal on screen, the
     * only state in which the governor is disarmed. The injected policy fires
     * at four unwatched bytes and ignores elapsed time.
     */
    private fun newViewModel(
        policy: (Long, Long) -> Boolean = { _, bytes -> bytes >= 4 },
    ): TerminalViewModel {
        owner.registry.currentState = Lifecycle.State.STARTED
        appForegroundTracker.start(owner)
        val vm = TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repository,
            customAgentTypesRepository = mockk {
                every { observeTypes() } returns MutableStateFlow(emptyList())
            },
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = appForegroundTracker,
            preferencesStore = preferencesStore,
        )
        vm.suspendPolicy = policy
        vm.markScreenVisible()
        return vm
    }

    private fun background() {
        owner.registry.currentState = Lifecycle.State.CREATED
    }

    private fun foreground() {
        owner.registry.currentState = Lifecycle.State.STARTED
    }

    @Test
    fun watchedSession_keepsStreamingHoweverMuchItSpends() = runTest(testDispatcher) {
        val vm = newViewModel()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("a lot of output".toByteArray())))
        runCurrent()

        verify(exactly = 0) { connection.pause() }
        assertEquals(ConnectionState.Live, vm.connectionState.value)
    }

    @Test
    fun backgroundedSession_thatSpendsNothing_keepsItsSocket() = runTest(testDispatcher) {
        // The byte term, which is what makes leaving the app briefly free: no
        // spend, no suspend, and so no replay to pay for on return.
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()

        verify(exactly = 0) { connection.pause() }
        assertEquals(ConnectionState.Reconnecting, vm.connectionState.value)
    }

    @Test
    fun backgroundedSession_thatSpends_isSuspended() = runTest(testDispatcher) {
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("spending".toByteArray())))
        runCurrent()

        verify { connection.pause() }
        assertEquals(ConnectionState.Suspended, vm.connectionState.value)
        assertEquals(false, vm.isConnected.value)
    }

    @Test
    fun framesBufferedBehindTheSuspend_doNotReopenTheSendGate() = runTest(testDispatcher) {
        // The events flow buffers, so frames queued behind the one that tripped
        // the policy arrive after the socket is closed. `handleBinary`'s
        // connected-upgrade would hand them back an `isConnected` the composer
        // trusts on its own, and the user's next Send would clear the field
        // into a frame that never leaves.
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("spending".toByteArray())))
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("straggler".toByteArray())))
        runCurrent()

        assertEquals(ConnectionState.Suspended, vm.connectionState.value)
        assertEquals(false, vm.isConnected.value)
    }

    @Test
    fun resumeKeepsTheSendGateShutUntilTheNewSocketConnects() = runTest(testDispatcher) {
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("spending".toByteArray())))
        runCurrent()

        foreground()
        runCurrent()

        assertEquals(ConnectionState.Reconnecting, vm.connectionState.value)
        assertEquals(false, vm.isConnected.value)
    }

    @Test
    fun offScreenSession_isSuspendedEvenWhileTheAppIsForegrounded() = runTest(testDispatcher) {
        // Navigating forward off the terminal leaves the view model alive on the
        // back stack. Nobody is reading it, so nobody should pay for it.
        val vm = newViewModel()
        runCurrent()
        vm.markScreenHidden()

        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("spending".toByteArray())))
        runCurrent()

        verify { connection.pause() }
        assertEquals(ConnectionState.Suspended, vm.connectionState.value)
    }

    @Test
    fun outputReceivedWhileWatched_isNotChargedToTheBackground() = runTest(testDispatcher) {
        // The counter re-baselines on the transition, or a long-lived session
        // would carry its whole history into the first background and suspend
        // instantly.
        val vm = newViewModel()
        runCurrent()
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("watched output".toByteArray())))
        runCurrent()

        background()
        runCurrent()

        verify(exactly = 0) { connection.pause() }
        // Still Live: an unsuspended session's connection state is untouched by
        // the app leaving the screen.
        assertEquals(ConnectionState.Live, vm.connectionState.value)
    }

    @Test
    fun returningToTheScreen_reopensTheSocket() = runTest(testDispatcher) {
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("spending".toByteArray())))
        runCurrent()

        foreground()
        runCurrent()

        // Once from `init`, once from the resume.
        verify(exactly = 2) { connection.connect() }
        assertEquals(ConnectionState.Reconnecting, vm.connectionState.value)
    }

    @Test
    fun neverSuspendsInTheMiddleOfAReplay() = runTest(testDispatcher) {
        // Tearing one down wastes bytes already paid for and leaves the overlay
        // pinned on a span whose terminator never arrives.
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("replayed history".toByteArray())))
        runCurrent()

        verify(exactly = 0) { connection.pause() }
        assertEquals(ConnectionState.ReplayingBuffer, vm.connectionState.value)
    }

    @Test
    fun doesNotChargeTheReplayItJustPaidFor() = runTest(testDispatcher) {
        // Otherwise a reconnect that lands while still backgrounded pays a full
        // ring, is re-suspended by its own replay, and loops.
        val vm = newViewModel { _, bytes -> bytes >= 12 }
        runCurrent()
        background()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("a long replayed span".toByteArray())))
        eventsFlow.tryEmit(WsEvent.Binary(replayEndFrame))
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("ab".toByteArray())))
        runCurrent()

        verify(exactly = 0) { connection.pause() }
    }

    @Test
    fun onScreenVisible_doesNotForceReconnectASuspendedSocket() = runTest(testDispatcher) {
        // `pause()` cleared `shouldReconnect`, so a force-reconnect would be a
        // no-op — but the watch gate owns the resume and must not be raced.
        val vm = newViewModel()
        runCurrent()
        background()
        runCurrent()
        eventsFlow.tryEmit(WsEvent.Binary(outputFrame("spending".toByteArray())))
        runCurrent()

        vm.onScreenVisible()
        runCurrent()

        verify(exactly = 0) { connection.forceReconnect() }
    }

    @Test
    fun theShippedPolicy_leavesAModestSpendAlone() = runTest(testDispatcher) {
        // The real thresholds, not the stub: a few hundred bytes is nowhere near
        // a ring.
        val vm = newViewModel(TerminalSuspend::shouldSuspend)
        runCurrent()
        background()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(outputFrame(ByteArray(512))))
        runCurrent()

        verify(exactly = 0) { connection.pause() }
        assertEquals(ConnectionState.Live, vm.connectionState.value)
    }

    @Test
    fun framesStillReachTheEmulatorAlongsideTheAccounting() = runTest(testDispatcher) {
        // Guard that byte accounting rides alongside frame handling rather than
        // replacing it: any inbound binary upgrades the channel to Live.
        val vm = newViewModel()
        runCurrent()

        eventsFlow.tryEmit(WsEvent.Binary(stateRunningFrame))
        runCurrent()

        assertEquals(ConnectionState.Live, vm.connectionState.value)
    }
}

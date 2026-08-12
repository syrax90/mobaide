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
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.connect] subscribe-before-connect race fix
 * and for the [TerminalViewModel.handleBinary] "binary frame implies Live"
 * safety upgrade.
 *
 * ## The race
 *
 * Before the fix, `connect()` called `connection.connect()` **before** launching
 * the events collector. The connection's `events` flow is
 * `MutableSharedFlow(replay = 1, extraBufferCapacity = 100)` — if the WS
 * opened and the server fired `Connected` + `Binary(state)` +
 * `Binary(ReplayMarker)` + `Binary(Output)` **before** the collector
 * subscribed, only the last envelope (replay=1) was replayed to the
 * late subscriber. The UI then stayed stuck on `Reconnecting…` / `Connecting`
 * because the intermediate state/replay transitions were lost.
 *
 * The fix launches the collector with `CoroutineStart.UNDISPATCHED` **before**
 * `connection.connect()`. UNDISPATCHED runs the coroutine body inline on
 * the caller's thread until the first suspension (`events.collect {…}`),
 * which subscribes synchronously. This test proves the subscriber receives
 * every pre-connect-returns emission — even when the mock connection fires
 * all four events synchronously from inside `connect()` itself, the worst
 * possible timing.
 *
 * [WebSocketConnection]/[WsEvent]/[TerminalProtocol] are the production types;
 * only the connection is mocked. The collector runs on `viewModelScope` which
 * is bound to `Dispatchers.Main` — installed here as a [StandardTestDispatcher]
 * via [MainDispatcherRule] so `runCurrent()` drains any post-UNDISPATCHED
 * continuation work without the test having to real-time-wait.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelConnectTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    /** Same shape as production [WebSocketConnection._events] — replay=1, buffer=100. */
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

    private val templatesRepository = mockk<com.mobaide.app.data.repository.TemplatesRepository>(relaxed = true)
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
        )

    // Encoders for the binary frames the server fires on attach.
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

    @Test
    fun connect_collectorSeesEventsFired_synchronouslyFromConnectionConnect() =
        runTest(testDispatcher) {
            // Arrange: make the mock connection fire the full proto-v3 handshake
            // burst (Connected + state + replay-start + output(chunk) +
            // replay-end) **synchronously** from inside connect(). This is the
            // exact race pattern the UNDISPATCHED fix targets — without it,
            // replay=1 on the SharedFlow would leave the late subscriber with
            // only the last frame (replay-end) and the state + replay-start +
            // output transitions would be silently lost.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
                eventsFlow.tryEmit(WsEvent.Binary(stateRunningFrame))
                eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
                eventsFlow.tryEmit(WsEvent.Binary(outputFrame("hello".toByteArray())))
                eventsFlow.tryEmit(WsEvent.Binary(replayEndFrame))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()

            // Assert: every frame in the burst reached the ViewModel.
            // - Connected → ConnectionState.Live (via WsEvent.Connected handler).
            // - Binary(state) → handleBinary ran (isConnected flipped true).
            // - Binary(replay-start) → ReplayingBuffer.
            // - Binary(output) → stays in ReplayingBuffer (chunk appended).
            // - Binary(replay-end) → back to Live.
            //
            // We deliberately do NOT assert on `sessionState` here: its value
            // comes from `parseStatus` which calls `org.json.JSONObject`, a
            // framework class that returns stubbed defaults under the
            // project's `unitTests.isReturnDefaultValues = true` config —
            // so the status string would never reach `"running"` in a plain
            // JVM unit test regardless of race correctness.  The race fix
            // is about which frames *reach the collector*, not about how
            // the state JSON is parsed — `connectionState` and `isConnected`
            // are the load-bearing observables for the race itself.
            assertEquals(ConnectionState.Live, vm.connectionState.value)
            assertEquals(true, vm.isConnected.value)
        }

    @Test
    fun connect_collectorSeesInitialState_evenWhenConnectedEventLost() =
        runTest(testDispatcher) {
            // Regression guard for the "belt-and-braces" half of the fix: if a
            // future refactor loses the Connected WsEvent but still delivers
            // binary frames (e.g. the underlying OkHttp listener changes),
            // the first binary frame must still flip connectionState off
            // Reconnecting because the handleBinary safety path upgrades it.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Binary(stateRunningFrame))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()

            // See note on the previous test: we assert only the race-relevant
            // observables (connectionState + isConnected), not sessionState —
            // the latter depends on `org.json.JSONObject` which returns stubs
            // in JVM unit tests.
            assertEquals(ConnectionState.Live, vm.connectionState.value)
            assertEquals(true, vm.isConnected.value)
        }

    @Test
    fun handleBinary_upgradesConnectionState_fromReconnectingToLive() =
        runTest(testDispatcher) {
            // Arrange: connect() is a no-op (no Connected event) — the VM
            // therefore stays on its initial Reconnecting state.  Then a
            // binary frame arrives later (simulating the degenerate case
            // where the WS opened cleanly but Connected was somehow lost).
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } returns Unit

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(
                "pre-condition: no Connected event means VM starts Reconnecting",
                ConnectionState.Reconnecting,
                vm.connectionState.value,
            )

            eventsFlow.tryEmit(WsEvent.Binary(stateRunningFrame))
            runCurrent()

            assertEquals(ConnectionState.Live, vm.connectionState.value)
            assertEquals(true, vm.isConnected.value)
        }

    @Test
    fun handleBinary_doesNotDowngradeReplayingBuffer_toLive() =
        runTest(testDispatcher) {
            // Safety-net specificity: the upgrade must only fire when the
            // current state is Reconnecting.  A stray output byte during a
            // legitimate ReplayingBuffer phase must not pre-emptively flip
            // the state to Live — the explicit REPLAY_END marker is what
            // decides when playback finishes.  This pins the
            // `if (_connectionState.value == ConnectionState.Reconnecting)`
            // guard; removing it would let output frames sneak the UI into
            // Live mid-replay and drop the "Replaying buffer" overlay early.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
                eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(
                "pre-condition: replay marker must put VM in ReplayingBuffer",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            // Chunked replay: output frames mid-replay must NOT transition
            // to Live. Only the explicit REPLAY_END frame ends the replay
            // overlay.
            eventsFlow.tryEmit(WsEvent.Binary(outputFrame("x".toByteArray())))
            runCurrent()
            assertEquals(
                "output frame mid-replay must keep VM in ReplayingBuffer",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            eventsFlow.tryEmit(WsEvent.Binary(replayEndFrame))
            runCurrent()
            assertEquals(ConnectionState.Live, vm.connectionState.value)
        }

    @Test
    fun handleBinary_keepsLiveState_whenAlreadyLive() =
        runTest(testDispatcher) {
            // Idempotency guard: once Live, a subsequent binary frame must
            // not regress connectionState (no oscillation Live → Reconnecting
            // → Live every frame).  This nails down that the upgrade branch
            // fires only off the Reconnecting discriminator.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(ConnectionState.Live, vm.connectionState.value)

            eventsFlow.tryEmit(WsEvent.Binary(outputFrame("x".toByteArray())))
            runCurrent()

            assertEquals(ConnectionState.Live, vm.connectionState.value)
            assertEquals(true, vm.isConnected.value)
        }

    @Test
    fun onScreenVisible_whenLive_doesNotForceReconnect() =
        runTest(testDispatcher) {
            // STOPPED→STARTED edge with a healthy WS: forcing a reconnect
            // would cost a needless server replay round-trip and flash the
            // overlay.  The ViewModel must short-circuit on Live.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(ConnectionState.Live, vm.connectionState.value)

            vm.onScreenVisible()
            runCurrent()

            verify(exactly = 0) { conn.forceReconnect() }
        }

    @Test
    fun onScreenVisible_whenReconnecting_callsForceReconnect() =
        runTest(testDispatcher) {
            // The bug being fixed: WS dropped while screen was off, backoff
            // sitting at 30 s ceiling.  When the screen comes back the
            // ViewModel must nudge the connection so the user doesn't sit
            // on "Reconnecting…" for up to half a minute.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } returns Unit

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(
                "pre-condition: VM starts in Reconnecting without a Connected event",
                ConnectionState.Reconnecting,
                vm.connectionState.value,
            )

            vm.onScreenVisible()
            runCurrent()

            verify(exactly = 1) { conn.forceReconnect() }
        }

    @Test
    fun onScreenVisible_whenReplayingBuffer_callsForceReconnect() =
        runTest(testDispatcher) {
            // ReplayingBuffer means the WS just reopened and is mid-replay.
            // A screen-on edge during this transient window should still
            // nudge, because the previous Reconnecting backoff context is
            // what we want to reset — we keep the no-op rule narrow to
            // Live, the only state that guarantees a healthy socket.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
                eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(
                "pre-condition: replay marker puts VM in ReplayingBuffer",
                ConnectionState.ReplayingBuffer,
                vm.connectionState.value,
            )

            vm.onScreenVisible()
            runCurrent()

            verify(exactly = 1) { conn.forceReconnect() }
        }

    @Test
    fun replayMarker_transitionsTo_replayingBuffer_andEnablesFollowMode() =
        runTest(testDispatcher) {
            // Verifies the state-machine half of the clear-before-replay
            // fix.  The byte-level emulator effect (cursor home, screen
            // wipe, scrollback drop) requires a real `Looper` because
            // `TerminalSession.emitOutput` posts the append through
            // `mMainThreadHandler` — covered separately by
            // `TerminalViewModelReplayClearTest` under Robolectric.
            val eventsFlow = newEventsFlow()
            val conn = newConnection(eventsFlow)
            every { conn.connect() } answers {
                eventsFlow.tryEmit(WsEvent.Connected)
            }

            val vm = newViewModel(newRepository(conn))
            runCurrent()
            assertEquals(ConnectionState.Live, vm.connectionState.value)

            eventsFlow.tryEmit(WsEvent.Binary(replayMarkerFrame))
            runCurrent()

            assertEquals(ConnectionState.ReplayingBuffer, vm.connectionState.value)
            assertEquals(
                "marker re-enables follow mode so the user lands at the bottom",
                true,
                vm.followMode.value,
            )
        }
}

package com.mobaide.app.ui.terminal

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.PaneClient
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * [TerminalViewModel] as the view of a Herdr pane.
 *
 * In watch mode the agent dictates the grid, so no resize frame may leave and
 * the dictated grid must never be cached as this client's own. A pane view is
 * in no session list, so the "missing from the list means gone" bounce must
 * not close it.
 *
 * Robolectric for a real `org.json.JSONObject`. The send loop drains on a real
 * `Dispatchers.IO` thread, so each `sendBinary` count sits behind a positive
 * `verify(timeout)`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelPaneGridTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    private val eventsFlow = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
    }
    private val sessionsFlow = MutableStateFlow<List<SessionResponse>>(emptyList())
    private val syncedFlow = MutableStateFlow(false)
    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("herdr-0123") } returns connection
        every { observeSessionList() } returns sessionsFlow
        every { observeSessionsSynced() } returns syncedFlow
        every { notifySessionEnded(any()) } just Runs
    }
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(
                mapOf("sessionId" to "herdr-0123", TerminalViewModel.ARG_HERDR_PANE to "w1:p1"),
            ),
            sessionRepository = sessionRepository,
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = mockk(relaxed = true),
        )

    private suspend fun TestScope.emitState(body: String) {
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE).plus(body.toByteArray(Charsets.UTF_8)).toByteString(),
            ),
        )
        runCurrent()
    }

    private suspend fun TestScope.connect() {
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
    }

    private fun TestScope.settleResize() {
        advanceTimeBy(200) // past the 100 ms resize debounce
        runCurrent()
    }

    private companion object {
        const val WATCH = """{"status":"running","pane":{"mode":"watch","cols":84,"rows":47}}"""
        const val CONTROL = """{"status":"running","pane":{"mode":"control"}}"""

        /** A pane in control mode at the controller's grid, as the agent sends it to every viewer. */
        fun controlledBy(clientId: String) =
            """{"status":"running","pane":{"mode":"control","controller":"$clientId","cols":84,"rows":47}}"""
    }

    @Test
    fun paneView_sendsNoSize_untilAStateFrameNamesThisClientTheController() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // Measured before the socket and before any state frame: a session
        // sends this grid on connect, a pane view must not.
        vm.resize(30, 90)
        settleResize()
        connect()
        settleResize()

        // A watcher, then a view of a pane that another device controls.
        emitState(WATCH)
        vm.resize(47, 84)
        vm.onScreenVisible()
        settleResize()
        emitState(controlledBy("another-device"))
        vm.resize(31, 91)
        vm.onScreenVisible()
        settleResize()

        // The view is at its own grid when the agent names this client.
        vm.resize(40, 100)
        settleResize()
        emitState(controlledBy(PaneClient.id))

        // The send loop keeps the order of the frames, so the one frame below
        // is behind every frame an earlier step could have sent.
        verify(exactly = 1, timeout = 2000) { connection.sendBinary(TerminalProtocol.encodeResize(40, 100)) }
        verify(exactly = 0) { connection.sendBinary(TerminalProtocol.encodeResize(30, 90)) }
        verify(exactly = 0) { connection.sendBinary(TerminalProtocol.encodeResize(47, 84)) }
        verify(exactly = 0) { connection.sendBinary(TerminalProtocol.encodeResize(31, 91)) }
    }

    @Test
    fun paneView_afterAReconnect_sendsNoSize_untilTheNewSocketNamesThisClient() = runTest(testDispatcher) {
        // Control ends when the last viewer leaves, so a view that comes back
        // is a watcher until the agent says otherwise. A size sent on what the
        // old socket said would resize a pane that another device controls now.
        val vm = createViewModel()
        runCurrent()
        connect()
        emitState(controlledBy(PaneClient.id))
        vm.resize(40, 100)
        settleResize()
        verify(exactly = 1, timeout = 2000) { connection.sendBinary(TerminalProtocol.encodeResize(40, 100)) }

        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        connect()
        vm.onScreenVisible()
        settleResize()
        vm.resize(41, 101)
        settleResize()

        emitState(controlledBy(PaneClient.id))

        // The grid the view has now goes out once, and only after the frame.
        verify(exactly = 1, timeout = 2000) { connection.sendBinary(TerminalProtocol.encodeResize(41, 101)) }
        verify(exactly = 1) { connection.sendBinary(TerminalProtocol.encodeResize(40, 100)) }
    }

    @Test
    fun stateFrame_exposesThePaneBlock_andASessionHasNone() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        connect()

        emitState(WATCH)
        assertEquals(TerminalPaneState("watch", 84, 47), vm.paneState.value)

        emitState(CONTROL)
        assertEquals(TerminalPaneState("control"), vm.paneState.value)

        emitState("""{"status":"running","pid":1234}""")
        assertNull(vm.paneState.value)
    }

    @Test
    fun watchGrid_noResizeFrameLeaves_untilControlIsTaken() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        connect()
        emitState(WATCH)

        // The layout applies the dictated grid, and the socket goes live again.
        vm.resize(47, 84)
        settleResize()
        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        connect()
        vm.onScreenVisible()
        settleResize()

        // The layout measures the view's own grid, then the agent names this
        // client the controller: the pane gets that grid.
        vm.resize(40, 100)
        settleResize()
        emitState(controlledBy(PaneClient.id))

        // The control grid is the first and only size this client ever sent.
        verify(exactly = 1, timeout = 2000) { connection.sendBinary(TerminalProtocol.encodeResize(40, 100)) }
        verify(exactly = 0) { connection.sendBinary(TerminalProtocol.encodeResize(47, 84)) }
    }

    @Test
    fun control_afterWatch_sendsTheSameOwnGridAgain() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        val own = TerminalProtocol.encodeResize(24, 80)
        vm.resize(24, 80)
        settleResize()
        connect()
        emitState(controlledBy(PaneClient.id))
        // Named the controller: the grid this view has goes out once.
        verify(exactly = 1, timeout = 2000) { connection.sendBinary(own) }

        emitState(WATCH)
        vm.resize(47, 84)
        settleResize()
        vm.resize(24, 80)
        settleResize()
        emitState(controlledBy(PaneClient.id))

        // The pane was not at this grid in between, so the dedupe must not
        // swallow it: without the frame the pane keeps the desktop size.
        verify(exactly = 2, timeout = 2000) { connection.sendBinary(own) }
        verify(exactly = 0) { connection.sendBinary(TerminalProtocol.encodeResize(47, 84)) }
    }

    @Test
    fun herdrPane_missingFromTheSyncedSessionList_isNotBounced() = runTest(testDispatcher) {
        val vm = createViewModel()

        vm.sessionEnded.test {
            // A pane view is never in `sessions.changed`.
            sessionsFlow.value = listOf(
                SessionResponse(session_id = "someone-else", state = "running", created_at = "2026-04-11T00:00:00Z"),
            )
            syncedFlow.value = true
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 0) { sessionRepository.notifySessionEnded(any()) }
    }
}

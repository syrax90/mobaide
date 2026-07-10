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
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel]'s "terminal wants input" detection state machine.
 *
 * The model: `wantsInput` latches true when the emulator reports DECTCEM cursor
 * visible AND bracketed-paste mode (DECSET 2004) on, after a 400 ms stability window
 * on both edges. A rising edge auto-raises the keyboard exactly once — targeting the
 * composer (`requestInputFocus`) while it is visible, or the terminal surface
 * (`terminalKeyboardRaise`) while it is hidden (auto-hidden by direct-input mode or
 * user-collapsed via [TerminalViewModel.hideComposer] — a hidden composer means the
 * terminal IS the input field). Raises are suppressed while not
 * [ConnectionState.Live]; an edge latched during replay fires once on Live entry.
 *
 * Escape bytes are fed straight to the JVM-hosted emulator via `emulator.append`
 * plus a manual `sessionCallback.onTextChanged` (what `notifyScreenUpdate` does in
 * production — `TerminalSession.emitOutput` posts to a main-Looper Handler, which is
 * a no-op in plain JVM tests).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelWantsInputTest {

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
    }
    private val templatesRepository = mockk<com.mobaide.app.data.repository.TemplatesRepository>(relaxed = true)
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
        every { composerAutoHideCoachSeen } returns flowOf(true)
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            templatesRepository = templatesRepository,
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = preferencesStore,
        )

    /** Append [sequence] to the emulator and fire the screen-update callback, exactly
     *  as `TerminalSession.notifyScreenUpdate` does after a live append. */
    private fun feed(vm: TerminalViewModel, sequence: String) {
        val bytes = sequence.toByteArray(Charsets.UTF_8)
        requireNotNull(vm.session.emulator).append(bytes, bytes.size)
        vm.sessionCallback.onTextChanged(vm.session)
    }

    private suspend fun TestScope.createLiveViewModel(): TerminalViewModel {
        val vm = createViewModel()
        runCurrent()
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
        assertEquals(ConnectionState.Live, vm.connectionState.value)
        return vm
    }

    @Test
    fun wantsInput_defaultsFalse() {
        val vm = createViewModel()
        assertFalse(vm.wantsInput.value)
        assertFalse(vm.directInputMode.value)
        assertFalse(vm.requestInputFocus.value)
    }

    @Test
    fun bracketedPasteWithVisibleCursor_afterDebounce_latchesTrue() = runTest(testDispatcher) {
        val vm = createLiveViewModel()

        feed(vm, BRACKETED_PASTE_ON) // DECTCEM is on by default
        runCurrent()
        assertFalse("latch must wait out the stability window", vm.wantsInput.value)

        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.wantsInput.value)
    }

    @Test
    fun bracketedPasteWithHiddenCursor_staysFalse() = runTest(testDispatcher) {
        val vm = createLiveViewModel()

        feed(vm, CURSOR_HIDE + BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertFalse("hidden cursor must veto wants-input (spinner/redraw state)", vm.wantsInput.value)
    }

    @Test
    fun cursorVisibleWithoutBracketedPaste_staysFalse() = runTest(testDispatcher) {
        val vm = createLiveViewModel()

        feed(vm, CURSOR_SHOW)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertFalse("cursor alone is not a wants-input signal", vm.wantsInput.value)
    }

    @Test
    fun riseRevertedWithinWindow_neverCommits() = runTest(testDispatcher) {
        val vm = createLiveViewModel()

        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(200) // inside the 400 ms window
        runCurrent()
        feed(vm, BRACKETED_PASTE_OFF) // back to the latched state → pending commit cancels
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertFalse("a sub-window pulse must not latch", vm.wantsInput.value)
    }

    @Test
    fun redrawFlicker_cursorHideShowWithinWindow_doesNotDropLatchOrRefire() = runTest(testDispatcher) {
        val vm = createLiveViewModel()
        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.wantsInput.value)
        vm.consumeFocusRequest()

        feed(vm, CURSOR_HIDE) // TUI redraw hides the cursor for a moment...
        advanceTimeBy(200)
        runCurrent()
        feed(vm, CURSOR_SHOW) // ...and shows it again inside the window
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue("redraw flicker must not drop the latch", vm.wantsInput.value)
        assertFalse("no new rising edge → no re-raise", vm.requestInputFocus.value)
    }

    @Test
    fun sustainedDrop_clearsLatch_afterDebounce() = runTest(testDispatcher) {
        val vm = createLiveViewModel()
        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.wantsInput.value)

        feed(vm, BRACKETED_PASTE_OFF)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertFalse(vm.wantsInput.value)
    }

    @Test
    fun cursorHide_viaCursorCallbackAlone_dropsLatch_withoutScreenUpdate() = runTest(testDispatcher) {
        val vm = createLiveViewModel()
        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.wantsInput.value)

        // Raw append, deliberately without the manual onTextChanged: DECSET 25 fires
        // onTerminalCursorStateChange synchronously from append, so this pins the
        // TerminalSessionCallback.onCursorStateChanged → sampleTerminalModes wiring
        // as a detection source in its own right.
        val bytes = CURSOR_HIDE.toByteArray(Charsets.UTF_8)
        requireNotNull(vm.session.emulator).append(bytes, bytes.size)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertFalse("DECTCEM transitions alone must drive detection", vm.wantsInput.value)
    }

    @Test
    fun risingEdge_atShellPrompt_requestsComposerFocus_once() = runTest(testDispatcher) {
        val vm = createLiveViewModel()

        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue("rising edge must focus the composer", vm.requestInputFocus.value)
        vm.consumeFocusRequest()

        // Ordinary streamed output while the state stays high must not re-raise —
        // a user-dismissed keyboard stays dismissed for this input episode.
        feed(vm, "output line\r\n")
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertFalse(vm.requestInputFocus.value)
    }

    @Test
    fun risingEdge_onAltScreen_raisesTerminalKeyboard_notComposerFocus() = runTest(testDispatcher) {
        val vm = createLiveViewModel()
        var raises = 0
        backgroundScope.launch { vm.terminalKeyboardRaise.collect { raises++ } }

        feed(vm, ALT_SCREEN_ON + BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue(vm.wantsInput.value)
        assertTrue("alt-screen + wants-input = direct-input mode", vm.directInputMode.value)
        assertEquals("IME must target the terminal surface", 1, raises)
        assertFalse("composer focus must not fire in direct mode", vm.requestInputFocus.value)
    }

    @Test
    fun risingEdge_withComposerForceHidden_raisesTerminalKeyboard_atShellPrompt() =
        runTest(testDispatcher) {
            val vm = createLiveViewModel()
            var raises = 0
            backgroundScope.launch { vm.terminalKeyboardRaise.collect { raises++ } }
            vm.hideComposer() // user collapsed the composer at an ordinary prompt

            feed(vm, BRACKETED_PASTE_ON) // main screen — no alt-screen involved
            advanceTimeBy(DEBOUNCE_PLUS)
            runCurrent()

            assertTrue(vm.wantsInput.value)
            assertFalse(vm.directInputMode.value)
            assertEquals(
                "hidden composer ⇒ the terminal is the input field, raise targets it",
                1,
                raises,
            )
            assertFalse(
                "composer focus must not fire while it is force-hidden",
                vm.requestInputFocus.value,
            )
        }

    @Test
    fun deferredReplayRaise_withComposerForceHidden_targetsTerminal() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        var raises = 0
        backgroundScope.launch { vm.terminalKeyboardRaise.collect { raises++ } }
        vm.hideComposer()

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        feed(vm, BRACKETED_PASTE_ON) // rising edge latched mid-replay → raise deferred
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertEquals("no raise while the replay overlay is up", 0, raises)

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_END_MARKER).toByteString()))
        runCurrent()

        assertEquals("the deferred raise honors the force-hidden composer", 1, raises)
        assertFalse(vm.requestInputFocus.value)
    }

    @Test
    fun dropThenRise_firesSecondRaise() = runTest(testDispatcher) {
        val vm = createLiveViewModel()
        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        vm.consumeFocusRequest()

        feed(vm, BRACKETED_PASTE_OFF) // command runs
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        feed(vm, BRACKETED_PASTE_ON) // next prompt
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue("each new input episode gets its own raise", vm.requestInputFocus.value)
    }

    @Test
    fun raiseSuppressed_duringReplay_thenFiresOnceOnReplayEnd() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        assertEquals(ConnectionState.ReplayingBuffer, vm.connectionState.value)

        feed(vm, BRACKETED_PASTE_ON) // ring replays a session sitting at a prompt
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.wantsInput.value)
        assertFalse("no raise while the replay overlay is up", vm.requestInputFocus.value)

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_END_MARKER).toByteString()))
        runCurrent()

        assertEquals(ConnectionState.Live, vm.connectionState.value)
        assertTrue("the deferred edge fires exactly once on Live entry", vm.requestInputFocus.value)
    }

    @Test
    fun disconnectMidReplay_pendingRaiseHeldUntilNextReplayEnd() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        feed(vm, BRACKETED_PASTE_ON) // rising edge latched mid-replay → raise deferred
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertFalse(vm.requestInputFocus.value)

        eventsFlow.emit(WsEvent.Disconnected) // drop before the replay end arrives
        runCurrent()
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()
        assertFalse(
            "reconnect's Connected must not fire the deferred raise — the new replay is still ahead",
            vm.requestInputFocus.value,
        )

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_END_MARKER).toByteString()))
        runCurrent()

        assertTrue(
            "the held raise fires once the genuine replay end lands",
            vm.requestInputFocus.value,
        )
    }

    @Test
    fun binaryUpgradeAfterDisconnect_holdsPendingRaise_untilReplayEnd() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        feed(vm, BRACKETED_PASTE_ON) // rising edge latched mid-replay → raise deferred
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertFalse(vm.requestInputFocus.value)

        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        // No Connected event: the reconnected socket's first sign of life is the new
        // replay-begin frame, whose handleBinary Reconnecting→Live upgrade must hold
        // the deferred raise — the new replay is still ahead.
        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        assertEquals(ConnectionState.ReplayingBuffer, vm.connectionState.value)
        assertFalse(
            "the binary Reconnecting→Live upgrade must not fire the deferred raise",
            vm.requestInputFocus.value,
        )

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_END_MARKER).toByteString()))
        runCurrent()

        assertTrue("the held raise fires on the genuine replay end", vm.requestInputFocus.value)
    }

    @Test
    fun reconnectWithLatchStillTrue_noDuplicateRaiseAfterReplay() = runTest(testDispatcher) {
        val vm = createLiveViewModel()
        feed(vm, BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.requestInputFocus.value)
        vm.consumeFocusRequest()

        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER).toByteString()))
        runCurrent()
        feed(vm, BRACKETED_PASTE_ON) // replay reproduces the same modes — no edge
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_END_MARKER).toByteString()))
        runCurrent()

        assertEquals(ConnectionState.Live, vm.connectionState.value)
        assertFalse(
            "an unchanged latch across reconnect must not re-raise a dismissed keyboard",
            vm.requestInputFocus.value,
        )
    }

    private companion object {
        /** Comfortably past the ViewModel's 400 ms stability window. */
        const val DEBOUNCE_PLUS = 500L
        const val REPLAY_MARKER: Byte = TerminalProtocol.TYPE_REPLAY
        const val REPLAY_END_MARKER: Byte = TerminalProtocol.TYPE_REPLAY_END
        const val BRACKETED_PASTE_ON = "[?2004h"
        const val BRACKETED_PASTE_OFF = "[?2004l"
        const val CURSOR_SHOW = "[?25h"
        const val CURSOR_HIDE = "[?25l"
        const val ALT_SCREEN_ON = "[?1049h"
    }
}

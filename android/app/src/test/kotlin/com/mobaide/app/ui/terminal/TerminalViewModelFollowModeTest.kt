package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel]'s follow-mode state machine.
 *
 * The model: `followMode` defaults to true (viewport pinned to tail). It flips to false
 * when the user manually scrolls up; it flips back to true when the user scrolls/flings
 * back to the bottom, taps the jump-to-bottom FAB, sends any input, or the WebSocket
 * starts replaying its buffer. While paused, `unreadRows` accumulates the per-update
 * `emulator.scrollCounter` delta and is capped at `MAX_UNREAD_DISPLAY` (= 9999) and
 * cleared whenever follow-mode resumes.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelFollowModeTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

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
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            templatesRepository = templatesRepository,
            preferencesStore = preferencesStore,
        )

    @Test
    fun followMode_default_isTrue() {
        val vm = createViewModel()
        assertTrue("default follow-mode should be true (viewport tracks tail)", vm.followMode.value)
        assertEquals(0, vm.unreadRows.value)
    }

    @Test
    fun setFollowMode_disable_togglesEmulatorGate() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator) { "emulator must exist after VM init" }
        assertFalse(emulator.isAutoScrollDisabled)

        vm.setFollowMode(false)

        assertFalse(vm.followMode.value)
        assertTrue(
            "emulator's isAutoScrollDisabled must mirror !followMode so the native preserve-mTopRow branch fires",
            emulator.isAutoScrollDisabled,
        )
    }

    @Test
    fun setFollowMode_enable_clearsUnreadAndUntogglesGate() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)

        vm.setFollowMode(false)
        emulator.append(NEWLINES_30, NEWLINES_30.size)
        vm.sessionCallback.onTextChanged(vm.session)
        assertTrue(vm.unreadRows.value > 0)

        vm.setFollowMode(true)

        assertTrue(vm.followMode.value)
        assertEquals("resuming follow-mode must clear the unread badge", 0, vm.unreadRows.value)
        assertFalse(
            "emulator gate must un-toggle when follow-mode resumes",
            emulator.isAutoScrollDisabled,
        )
    }

    @Test
    fun setFollowMode_enableTwice_isIdempotentOnEmulator() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)

        vm.setFollowMode(true)
        vm.setFollowMode(true)

        assertFalse(
            "double-enable must not flip the emulator gate (toggle would invert state)",
            emulator.isAutoScrollDisabled,
        )
    }

    @Test
    fun setFollowMode_disableTwice_isIdempotentOnEmulator() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)

        vm.setFollowMode(false)
        vm.setFollowMode(false)

        assertTrue(
            "double-disable must not flip the emulator gate (toggle would invert state)",
            emulator.isAutoScrollDisabled,
        )
    }

    @Test
    fun onUserScrollSettled_topRowZero_resumesFollowMode() {
        val vm = createViewModel()
        vm.setFollowMode(false)

        vm.onUserScrollSettled(0)

        assertTrue("dragging back to the tail must resume follow-mode", vm.followMode.value)
    }

    @Test
    fun onUserScrollSettled_negativeTopRow_pausesFollowMode() {
        val vm = createViewModel()

        vm.onUserScrollSettled(-3)

        assertFalse("dragging up must pause follow-mode", vm.followMode.value)
    }

    @Test
    fun jumpToBottom_resumesFollowMode() {
        val vm = createViewModel()
        vm.setFollowMode(false)

        vm.jumpToBottom()

        assertTrue(vm.followMode.value)
        assertEquals(0, vm.unreadRows.value)
    }

    @Test
    fun sendInput_resumesFollowMode() {
        val vm = createViewModel()
        vm.setFollowMode(false)

        vm.sendInput("ls")

        assertTrue(
            "typing must resume follow-mode (matches WezTerm/iTerm2/Alacritty convention)",
            vm.followMode.value,
        )
    }

    @Test
    fun sendBytes_resumesFollowMode() {
        val vm = createViewModel()
        vm.setFollowMode(false)

        vm.sendBytes(byteArrayOf(0x03))

        assertTrue("dispatching control bytes must resume follow-mode", vm.followMode.value)
    }

    @Test
    fun sendTappedRow_whileNotLive_doesNotResumeFollowMode() {
        val vm = createViewModel()
        vm.setFollowMode(false)

        vm.sendTappedRow("/help", isCurrentInput = false)

        assertFalse(
            "tap during reconnect/replay is intentionally a no-op — must not flip follow-mode either",
            vm.followMode.value,
        )
    }

    @Test
    fun replayMarker_forcesFollowMode() = runTest {
        val vm = createViewModel()
        vm.setFollowMode(false)

        eventsFlow.emit(WsEvent.Binary(byteArrayOf(REPLAY_MARKER_FRAME_TYPE).toByteString()))
        advanceUntilIdle()

        assertTrue("buffer replay must land the user at the tail", vm.followMode.value)
        assertEquals(ConnectionState.ReplayingBuffer, vm.connectionState.value)
    }

    @Test
    fun unreadRows_accumulatesWhilePaused() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)
        vm.setFollowMode(false)

        emulator.append(NEWLINES_30, NEWLINES_30.size)
        val firstDelta = emulator.scrollCounter
        vm.sessionCallback.onTextChanged(vm.session)
        assertEquals(firstDelta, vm.unreadRows.value)

        emulator.clearScrollCounter()
        emulator.append(NEWLINES_30, NEWLINES_30.size)
        val secondDelta = emulator.scrollCounter
        vm.sessionCallback.onTextChanged(vm.session)

        assertEquals(
            "two paused updates must accumulate scrollCounter deltas",
            firstDelta + secondDelta,
            vm.unreadRows.value,
        )
    }

    @Test
    fun unreadRows_doesNotAccumulateWhileFollowing() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)

        emulator.append(NEWLINES_30, NEWLINES_30.size)
        vm.sessionCallback.onTextChanged(vm.session)

        assertEquals(
            "follow-mode = on means new output is being shown — nothing to count",
            0,
            vm.unreadRows.value,
        )
    }

    @Test
    fun unreadRows_resetsWhenFollowModeResumes() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)

        vm.setFollowMode(false)
        emulator.append(NEWLINES_30, NEWLINES_30.size)
        vm.sessionCallback.onTextChanged(vm.session)
        assertTrue(vm.unreadRows.value > 0)

        vm.onUserScrollSettled(0)

        assertEquals(0, vm.unreadRows.value)
    }

    @Test
    fun unreadRows_isCappedAtMaxDisplay() {
        val vm = createViewModel()
        val emulator = requireNotNull(vm.session.emulator)
        vm.setFollowMode(false)

        emulator.append(NEWLINES_LARGE, NEWLINES_LARGE.size)
        vm.sessionCallback.onTextChanged(vm.session)

        assertEquals(
            "unread count must clamp at the display cap to avoid overflow and pointless arithmetic",
            MAX_UNREAD_DISPLAY,
            vm.unreadRows.value,
        )
    }

    private companion object {
        const val MAX_UNREAD_DISPLAY = 9999
        const val REPLAY_MARKER_FRAME_TYPE: Byte = 'b'.code.toByte()
        val NEWLINES_30 = ByteArray(30) { '\n'.code.toByte() }
        val NEWLINES_LARGE = ByteArray(MAX_UNREAD_DISPLAY + 200) { '\n'.code.toByte() }
    }
}

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
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Which send paths arm [TerminalViewModel.cursorReveal], the Static-mode typing latch.
 *
 * The VM-level sends — composer submit, control keys / chords, tapped row — are all the
 * user typing, so they arm. The terminal-surface funnel (`session.write` → input listener)
 * must not: it also carries mouse clicks and wheel reports (a vertical drag in Claude Code
 * is a stream of wheel reports, and arming on it would yank the view sideways), plus the
 * emulator's own auto-replies. Keystrokes on the surface arm at the view, before the bytes
 * reach this funnel. Each negative case also proves the bytes reached the wire, so it
 * cannot pass by the funnel simply dropping them.
 *
 * Robolectric for the `'s'` state-frame JSON parse and the main-looper `emitOutput` post
 * that raises the auto-reply flag (see [TerminalViewModelEmulatorReplyTest]). SDK pinned
 * to 33 to match the rest of the JVM/Robolectric suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelCursorRevealTest {

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
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Static)
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
            reviewPromptCoordinator = mockk(relaxed = true),
        )

    private suspend fun TestScope.liveRunningViewModel(): TerminalViewModel {
        val vm = createViewModel()
        eventsFlow.emit(WsEvent.Connected)
        advanceUntilIdle()
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE)
                    .plus("""{"status":"running","pid":1234}""".toByteArray(Charsets.UTF_8))
                    .toByteString(),
            ),
        )
        advanceUntilIdle()
        assertFalse("precondition: nothing has armed the latch yet", vm.cursorReveal.isArmed)
        return vm
    }

    private fun writeFromSurface(vm: TerminalViewModel, bytes: ByteArray) {
        vm.session.write(bytes, 0, bytes.size)
    }

    @Test
    fun composerSubmit_arms() = runTest(testDispatcher) {
        val vm = liveRunningViewModel()

        vm.sendInput("ls")

        assertTrue(vm.cursorReveal.isArmed)
    }

    @Test
    fun controlKeyBytes_arm() = runTest(testDispatcher) {
        val vm = liveRunningViewModel()

        vm.sendBytes(byteArrayOf(0x7F))

        assertTrue("the input-bar row (Backspace, arrows, chords) is the user typing", vm.cursorReveal.isArmed)
    }

    @Test
    fun wheelReportFromTheSurface_doesNotArm() = runTest(testDispatcher) {
        val vm = liveRunningViewModel()

        // SGR wheel-up at col 10 row 5: what a vertical drag in a mouse-tracking TUI sends.
        writeFromSurface(vm, "\u001B[<64;10;5M".toByteArray(Charsets.US_ASCII))

        verify(timeout = 2000, exactly = 1) { connection.sendBinary(any()) }
        assertFalse(
            "a scrollback drag in Claude Code must not yank the Static view sideways",
            vm.cursorReveal.isArmed,
        )
    }

    @Test
    fun emulatorAutoReply_doesNotArm() = runTest(testDispatcher) {
        val vm = liveRunningViewModel()

        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_OUTPUT)
                    .plus("\u001B[6n".toByteArray(Charsets.US_ASCII))
                    .toByteString(),
            ),
        )
        advanceUntilIdle()
        shadowOf(Looper.getMainLooper()).idle()

        verify(timeout = 2000, exactly = 1) { connection.sendBinary(any()) }
        assertFalse(
            "a cursor-position report is the program answering itself, not the user typing",
            vm.cursorReveal.isArmed,
        )
    }

    @Test
    fun surfaceBytes_stillResumeFollowMode() = runTest(testDispatcher) {
        val vm = liveRunningViewModel()
        vm.setFollowMode(false)

        writeFromSurface(vm, "x".toByteArray(Charsets.UTF_8))

        assertTrue(
            "splitting the funnel away from sendBytes must keep the follow-mode resume",
            vm.followMode.value,
        )
    }
}

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
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
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
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Guards that the emulator's *own* automatic replies never resume a paused follow mode.
 *
 * `TerminalSession.write` is one funnel with two sources: the user (typed keys, paste,
 * mouse) and the emulator answering a query it parsed out of inbound output (DSR/CPR,
 * DA, DECRQM, ...). Only the first is user activity. Routing both through `sendBytes`
 * let a bare `ESC[6n` — which powerlevel10k / starship emit routinely — clear
 * `unreadRows`, un-toggle `isAutoScrollDisabled` and snap a scrolled-up viewport back
 * to the tail.
 *
 * Robolectric is mandatory, not stylistic: the discriminating flag rises inside the
 * Runnable `TerminalSession.emitOutput` posts to `Looper.getMainLooper()`. A plain JVM
 * test never runs that Runnable, and a test calling `emulator.append(...)` directly
 * bypasses the flag entirely and would pass against the unfixed code. Every case here
 * therefore drives real query bytes through an `'o'` output frame on the WS events flow.
 * SDK pinned to 33 to match the rest of the JVM/Robolectric suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelEmulatorReplyTest {

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

    private suspend fun TestScope.goLive() {
        eventsFlow.emit(WsEvent.Connected)
        advanceUntilIdle()
    }

    private suspend fun TestScope.markSessionRunning() {
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE)
                    .plus("""{"status":"running","pid":1234}""".toByteArray(Charsets.UTF_8))
                    .toByteString(),
            ),
        )
        advanceUntilIdle()
    }

    /** Push PTY bytes the way the server does, then drain the main `Looper` the
     *  `emitOutput` append (and any reply it provokes) is posted to. */
    private suspend fun TestScope.feedOutput(payload: ByteArray) {
        eventsFlow.emit(
            WsEvent.Binary(byteArrayOf(TerminalProtocol.TYPE_OUTPUT).plus(payload).toByteString()),
        )
        advanceUntilIdle()
        shadowOf(Looper.getMainLooper()).idle()
    }

    /** Live + running session, follow mode paused by a scroll-up, with a non-zero
     *  unread tally built from real streamed output. */
    private suspend fun TestScope.pausedSessionWithUnread(): TerminalViewModel {
        val vm = createViewModel()
        goLive()
        markSessionRunning()
        vm.setFollowMode(false)
        feedOutput(NEWLINES_30)
        assertTrue("pre-condition: paused output must have built an unread tally", vm.unreadRows.value > 0)
        // TerminalView.onScreenUpdated clears the counter after the VM's callback has
        // consumed it; there is no View here, so an un-cleared counter would be
        // re-counted on the next update and mask what the query itself did.
        requireNotNull(vm.session.emulator).clearScrollCounter()
        return vm
    }

    private suspend fun TestScope.assertQueryDoesNotResumeFollowMode(query: ByteArray) {
        val vm = pausedSessionWithUnread()
        val emulator = requireNotNull(vm.session.emulator)
        val unreadBefore = vm.unreadRows.value

        feedOutput(query)

        // Nothing else in the arrangement sends, so this pins the assertions below to a
        // query that really did reach the input funnel rather than passing vacuously.
        verify(timeout = 2000, exactly = 1) { connection.sendBinary(any()) }
        assertFalse(
            "an emulator reply is the program answering its own query, not the user typing",
            vm.followMode.value,
        )
        assertEquals(
            "the unread tally must survive a reply the user never asked for",
            unreadBefore,
            vm.unreadRows.value,
        )
        assertTrue(
            "the emulator gate must stay set, or the surface resets view.topRow to the tail",
            emulator.isAutoScrollDisabled,
        )
    }

    private fun payloadOf(frame: ByteString): ByteArray =
        frame.toByteArray().let { it.copyOfRange(1, it.size) }

    private fun frameTypeOf(frame: ByteString): Byte = frame.toByteArray()[0]

    @Test
    fun dsrCursorReport_whilePaused_doesNotResumeFollowMode() =
        runTest(testDispatcher) { assertQueryDoesNotResumeFollowMode(DSR_CURSOR_REPORT) }

    @Test
    fun dsrCursorReport_whilePaused_stillAnswersTheQuery() =
        runTest(testDispatcher) {
            val vm = pausedSessionWithUnread()
            val captured = slot<ByteString>()

            feedOutput(DSR_CURSOR_REPORT)

            verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
            val emulator = requireNotNull(vm.session.emulator)
            assertEquals(
                "the reply still travels to the PTY as a normal input frame",
                'i'.code.toByte(),
                frameTypeOf(captured.captured),
            )
            assertArrayEquals(
                "CPR answer is ESC [ row ; col R with 1-based coordinates",
                "\u001B[${emulator.cursorRow + 1};${emulator.cursorCol + 1}R"
                    .toByteArray(Charsets.US_ASCII),
                payloadOf(captured.captured),
            )
        }

    @Test
    fun deviceAttributes_whilePaused_doesNotResumeFollowMode() =
        runTest(testDispatcher) { assertQueryDoesNotResumeFollowMode(DEVICE_ATTRIBUTES) }

    @Test
    fun decrqm_whilePaused_doesNotResumeFollowMode() =
        runTest(testDispatcher) { assertQueryDoesNotResumeFollowMode(DECRQM_SYNC_OUTPUT) }

    @Test
    fun terminalViewTyping_whilePaused_resumesFollowMode() =
        runTest(testDispatcher) {
            val vm = pausedSessionWithUnread()

            // Exactly how TerminalView delivers a keystroke: session.write from outside
            // any append. This is the half that proves the fix discriminates by source
            // rather than simply dropping the resume.
            val typed = "x".toByteArray(Charsets.UTF_8)
            vm.session.write(typed, 0, typed.size)

            assertTrue(
                "genuine typing on the terminal surface must still snap back to the tail",
                vm.followMode.value,
            )
            assertEquals("resuming follow mode clears the unread badge", 0, vm.unreadRows.value)
        }

    private companion object {
        val NEWLINES_30 = ByteArray(30) { '\n'.code.toByte() }

        /** DSR 6 — cursor position report. */
        val DSR_CURSOR_REPORT = "\u001B[6n".toByteArray(Charsets.US_ASCII)

        /** Primary Device Attributes; answered with ESC[?64;1;2;6;9;15;18;21;22c. */
        val DEVICE_ATTRIBUTES = "\u001B[c".toByteArray(Charsets.US_ASCII)

        /** DECRQM for synchronized output (2026); answered with ESC[?2026;<value>$y. */
        val DECRQM_SYNC_OUTPUT = "\u001B[?2026\$p".toByteArray(Charsets.US_ASCII)
    }
}

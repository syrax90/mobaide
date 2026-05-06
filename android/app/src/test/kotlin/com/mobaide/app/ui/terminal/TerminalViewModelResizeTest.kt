package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.resize] — verifies the debounce and
 * deduplication logic added to prevent rapid-fire PTY resize events during
 * keyboard animation.
 *
 * Key behaviors:
 * 1. Same dimensions are deduplicated (no frame sent at all).
 * 2. Rapid calls within the 100ms debounce window cancel previous sends;
 *    only the final dimensions are transmitted.
 * 3. After the debounce delay, a single 'r' (resize) frame is sent via
 *    the sendChannel → connection.sendBinary path.
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
}

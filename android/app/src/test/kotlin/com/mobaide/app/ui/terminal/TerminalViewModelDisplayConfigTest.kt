package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayConfig
import com.mobaide.app.data.local.TerminalDisplayMode
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
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.displayConfig] — verifies the
 * `combine(terminalDisplayMode, terminalStaticColumns) + stateIn(Eagerly)`
 * derivation.
 *
 * Behaviour pinned here:
 * 1. Initial seed value (before any upstream emission) is the
 *    documented default: `Static` mode with 120 columns.
 * 2. Static-mode prefs flow through to a [TerminalDisplayConfig] with
 *    matching `mode` + `staticColumns`.
 * 3. Combined StateFlow re-emits when either upstream prefs flow changes
 *    (mode toggle, columns adjustment).
 *
 * The StateFlow is consumed by `TerminalScreen.collectAsStateWithLifecycle`
 * and passed into `TerminalSurfaceView` to drive the Static-vs-Full layout
 * branch — getting these emissions wrong would mean the surface stays in
 * Full mode after the user picks Static (or vice versa).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelDisplayConfigTest {

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
    private val templatesRepository =
        mockk<com.mobaide.app.data.repository.TemplatesRepository>(relaxed = true)

    private fun createViewModel(prefs: PreferencesStore): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            templatesRepository = templatesRepository,
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = prefs,
        )

    @Test
    fun displayConfig_seedValue_isStaticModeWithDefaultColumns() = runTest(testDispatcher) {
        // Upstream prefs that never emit — combine() can only produce a value
        // once BOTH source flows have emitted at least once. Until then, the
        // SharingStarted.Eagerly seed must surface so the UI has something
        // to render on first composition.
        val prefs = mockk<PreferencesStore> {
            every { terminalDisplayMode } returns MutableSharedFlow<TerminalDisplayMode>()
            every { terminalStaticColumns } returns MutableSharedFlow<Int>()
        }

        val vm = createViewModel(prefs)
        runCurrent()

        assertEquals(
            TerminalDisplayConfig(TerminalDisplayMode.Static, TERMINAL_STATIC_COLUMNS_DEFAULT),
            vm.displayConfig.value,
        )
    }

    @Test
    fun displayConfig_staticModeWithCustomColumns_combinesIntoConfig() = runTest(testDispatcher) {
        val prefs = mockk<PreferencesStore> {
            every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Static)
            every { terminalStaticColumns } returns flowOf(180)
        }

        val vm = createViewModel(prefs)
        advanceUntilIdle()

        assertEquals(
            TerminalDisplayConfig(TerminalDisplayMode.Static, 180),
            vm.displayConfig.value,
        )
    }
}

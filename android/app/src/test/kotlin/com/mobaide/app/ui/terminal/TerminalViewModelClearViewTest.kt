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
import io.mockk.verifyOrder
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.clearLocalView] — the Ctrl+L clear path.
 * The InputBar invokes it before [TerminalViewModel.sendBytes], and the single
 * sendChannel must deliver the `'c'` (clear-replay) frame ahead of the `'i'`
 * frame so the agent's ring is gone before any output the 0x0c provokes.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelClearViewTest {

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
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = preferencesStore,
        )

    @Test
    fun clearLocalView_thenSendBytes_deliversClearFrameBeforeInputFrame() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        // The InputBar tap sequence for a clearsView key.
        vm.clearLocalView()
        vm.sendBytes(byteArrayOf(0x0c))
        runCurrent()

        verifyOrder {
            connection.sendBinary(TerminalProtocol.encodeClearReplay())
            connection.sendBinary(TerminalProtocol.encodeInput(byteArrayOf(0x0c)))
        }
    }
}

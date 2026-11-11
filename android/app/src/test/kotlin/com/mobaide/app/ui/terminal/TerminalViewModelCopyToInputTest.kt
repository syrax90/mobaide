package com.mobaide.app.ui.terminal

import androidx.compose.ui.text.TextRange
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
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
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.copyTextToInput] — the shared destination
 * for both the new long-press "Send to Input" action and the existing
 * double-tap-prompt-row shortcut.
 *
 * The load-bearing contract for both features: copying text into the input bar
 * must (1) populate the editable field, (2) place the caret at the end so the
 * user can append/edit, and (3) raise a focus request so the keyboard opens —
 * while emphatically NOT submitting. Unlike [TerminalViewModel.sendInput] /
 * [TerminalViewModel.sendTappedRow], it must never enqueue an outbound PTY frame,
 * so selected/tapped text is staged for edit-before-resend rather than executed.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelCopyToInputTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<com.mobaide.app.data.network.WsEvent>(
        replay = 1,
        extraBufferCapacity = 100,
    )
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
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = mockk(relaxed = true),
        )

    @Test
    fun copyTextToInput_multilineText_preservedAndCaretAtEnd() {
        val vm = createViewModel()
        val text = "line one\nline two\nline three"

        vm.copyTextToInput(text)

        assertEquals(
            "multi-line selections must be copied verbatim (no truncation at the first LF)",
            text,
            vm.inputText.value.text,
        )
        assertEquals(TextRange(text.length), vm.inputText.value.selection)
    }

    @Test
    fun copyTextToInput_raisesFocusRequest() {
        val vm = createViewModel()

        vm.copyTextToInput("git status")

        assertTrue(
            "copying must request input focus so the keyboard opens for edit-before-resend",
            vm.requestInputFocus.value,
        )
    }

    @Test
    fun copyTextToInput_doesNotEnqueueSendFrame_evenWhenLive() = runTest {
        val vm = createViewModel()
        eventsFlow.emit(com.mobaide.app.data.network.WsEvent.Connected)
        advanceUntilIdle()

        vm.copyTextToInput("rm -rf /")
        advanceUntilIdle()

        verify(exactly = 0) {
            connection.sendBinary(any())
        }
    }
}

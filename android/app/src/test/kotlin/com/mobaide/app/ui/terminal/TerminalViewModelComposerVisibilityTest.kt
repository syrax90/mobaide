package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.TemplateDto
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
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel]'s composer hide/reveal state machine.
 *
 * The model is purely user-controlled: `composerVisible` defaults to true.
 * [TerminalViewModel.hideComposer] collapses it and the choice sticks across TUI
 * activity (alt-screen, bracketed paste) and reconnects until
 * [TerminalViewModel.revealComposer] brings it back — alt-screen/wants-input never
 * auto-hides it. A gesture that explicitly targets the composer (copy-to-input,
 * applying a template) reveals it and requests focus even if it was hidden.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelComposerVisibilityTest {

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
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            templatesRepository = templatesRepository,
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = preferencesStore,
        )

    private fun feed(vm: TerminalViewModel, sequence: String) {
        val bytes = sequence.toByteArray(Charsets.UTF_8)
        requireNotNull(vm.session.emulator).append(bytes, bytes.size)
        vm.sessionCallback.onTextChanged(vm.session)
    }

    @Test
    fun composerVisible_defaultsTrue() {
        val vm = createViewModel()
        assertTrue(vm.composerVisible.value)
    }

    @Test
    fun hideComposer_hidesComposer_andDoesNotRequestFocus() {
        val vm = createViewModel()

        vm.hideComposer()

        assertFalse(vm.composerVisible.value)
        assertFalse(
            "collapse must not request focus — the keyboard dismisses with the field",
            vm.requestInputFocus.value,
        )
    }

    @Test
    fun revealComposer_afterHide_showsComposer_andRequestsFocus() {
        val vm = createViewModel()
        vm.hideComposer()

        vm.revealComposer()

        assertTrue(vm.composerVisible.value)
        assertTrue("reveal must focus the field the user just asked for", vm.requestInputFocus.value)
    }

    @Test
    fun hideComposer_survivesReconnect() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        vm.hideComposer()

        eventsFlow.emit(WsEvent.Disconnected)
        runCurrent()
        eventsFlow.emit(WsEvent.Connected)
        runCurrent()

        assertFalse("connection churn must not resurrect the composer", vm.composerVisible.value)
    }

    @Test
    fun copyTextToInput_whileComposerHidden_revealsComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        vm.hideComposer()

        vm.copyTextToInput("git status")

        assertTrue(
            "a gesture that targets the composer (row copy / Send to Input) must reveal it",
            vm.composerVisible.value,
        )
        assertTrue(vm.requestInputFocus.value)
    }

    @Test
    fun applyTemplate_whileComposerHidden_revealsComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        vm.hideComposer()

        vm.applyTemplate(
            TemplateDto(
                template_id = "t1",
                name = "status",
                body = "git status",
                is_favorite = false,
                created_at = "2026-01-01T00:00:00Z",
                updated_at = "2026-01-01T00:00:00Z",
                version = 1,
            ),
        )
        runCurrent()

        assertTrue(
            "picking a template explicitly targets the composer — it must reveal",
            vm.composerVisible.value,
        )
        assertTrue(vm.requestInputFocus.value)
    }

    @Test
    fun altScreenTui_doesNotHideComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        feed(vm, ALT_SCREEN_ON + BRACKETED_PASTE_ON) // a TUI enters the alt screen and reads input
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue(vm.wantsInput.value)
        assertTrue(
            "alt-screen/wants-input must never auto-hide the composer — visibility is user-only",
            vm.composerVisible.value,
        )
    }

    @Test
    fun hideComposer_persistsThroughAltScreenEnterAndExit() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        vm.hideComposer()

        feed(vm, ALT_SCREEN_ON + BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertFalse(vm.composerVisible.value)
        assertFalse(
            "TUI activity must not hand focus back to a hidden composer",
            vm.requestInputFocus.value,
        )

        feed(vm, ALT_SCREEN_OFF)
        runCurrent()

        assertFalse(
            "a manual collapse must stick through alt-screen enter and exit",
            vm.composerVisible.value,
        )
        assertFalse(vm.requestInputFocus.value)
    }

    private companion object {
        const val DEBOUNCE_PLUS = 500L
        const val BRACKETED_PASTE_ON = "[?2004h"
        const val ALT_SCREEN_ON = "[?1049h"
        const val ALT_SCREEN_OFF = "[?1049l"
    }
}

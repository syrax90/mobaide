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
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
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
 * The model: `composerVisible` defaults to true and auto-hides while direct-input
 * mode is active (alt-screen + wants-input) — unless the user manually revealed it
 * via [TerminalViewModel.revealComposer], which wins for the remainder of that
 * direct-mode episode. Exiting direct mode restores the composer and resets the
 * override, so the next episode auto-hides again. The first auto-hide surfaces a
 * one-time coach mark gated by a persisted [PreferencesStore] flag.
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

    private fun feed(vm: TerminalViewModel, sequence: String) {
        val bytes = sequence.toByteArray(Charsets.UTF_8)
        requireNotNull(vm.session.emulator).append(bytes, bytes.size)
        vm.sessionCallback.onTextChanged(vm.session)
    }

    /** Drive the emulator into direct-input mode (alt-screen + wants-input latched). */
    private fun TestScope.enterDirectMode(vm: TerminalViewModel) {
        feed(vm, ALT_SCREEN_ON + BRACKETED_PASTE_ON)
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()
        assertTrue(vm.directInputMode.value)
    }

    @Test
    fun composerVisible_defaultsTrue() {
        val vm = createViewModel()
        assertTrue(vm.composerVisible.value)
    }

    @Test
    fun directMode_altScreenPlusWantsInput_hidesComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        enterDirectMode(vm)

        assertFalse("direct-input mode must auto-hide the composer", vm.composerVisible.value)
    }

    @Test
    fun wantsInputOnMainScreen_keepsComposerVisible() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()

        feed(vm, BRACKETED_PASTE_ON) // shell prompt: no alt-screen
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue(vm.wantsInput.value)
        assertFalse(vm.directInputMode.value)
        assertTrue("line-buffered flow keeps the composer", vm.composerVisible.value)
    }

    @Test
    fun directModeExit_restoresComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)

        feed(vm, ALT_SCREEN_OFF) // alt-screen exit is un-debounced
        runCurrent()

        assertFalse(vm.directInputMode.value)
        assertTrue(vm.composerVisible.value)
    }

    @Test
    fun directModeExit_handsFocusBackToComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)
        assertFalse(vm.requestInputFocus.value)

        feed(vm, ALT_SCREEN_OFF)
        runCurrent()

        assertTrue(
            "a raised IME must retarget the restored composer, not keep feeding the PTY",
            vm.requestInputFocus.value,
        )
    }

    @Test
    fun revealComposer_showsComposer_andRequestsFocus() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)

        vm.revealComposer()

        assertTrue(vm.composerVisible.value)
        assertTrue("reveal must focus the field the user just asked for", vm.requestInputFocus.value)
    }

    @Test
    fun revealOverride_persistsAcrossScreenUpdates_withinEpisode() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)
        vm.revealComposer()

        feed(vm, "TUI keeps redrawing\r\n")
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        assertTrue("no auto-hide tug-of-war after a manual reveal", vm.composerVisible.value)
    }

    @Test
    fun directModeExit_resetsOverride_nextEpisodeHidesAgain() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)
        vm.revealComposer()

        feed(vm, ALT_SCREEN_OFF) // episode ends
        runCurrent()
        assertTrue(vm.composerVisible.value)

        feed(vm, ALT_SCREEN_ON) // next episode (wants-input still latched)
        runCurrent()

        assertTrue(vm.directInputMode.value)
        assertFalse("the reveal choice must not leak into the next episode", vm.composerVisible.value)
    }

    @Test
    fun copyTextToInput_whileComposerHidden_revealsComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)

        vm.copyTextToInput("git status")

        assertTrue(
            "a gesture that targets the composer (row copy / Send to Input) must reveal it",
            vm.composerVisible.value,
        )
        assertTrue(vm.requestInputFocus.value)
    }

    @Test
    fun mainScreenCopyToInput_doesNotSuppressNextEpisodeAutoHide() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        feed(vm, BRACKETED_PASTE_ON) // ordinary main-screen prompt
        advanceTimeBy(DEBOUNCE_PLUS)
        runCurrent()

        vm.copyTextToInput("ls") // reveal with no episode active — must not arm the override

        feed(vm, ALT_SCREEN_ON) // a TUI episode starts later
        runCurrent()

        assertTrue(vm.directInputMode.value)
        assertFalse(
            "a reveal at a normal prompt must not suppress the next episode's auto-hide",
            vm.composerVisible.value,
        )
    }

    @Test
    fun applyTemplate_whileComposerHidden_revealsComposer() = runTest(testDispatcher) {
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)

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
    fun firstAutoHide_showsCoachMark_whenFlagUnseen() = runTest(testDispatcher) {
        every { preferencesStore.composerAutoHideCoachSeen } returns flowOf(false)
        val vm = createViewModel()
        runCurrent()

        enterDirectMode(vm)
        runCurrent()

        assertTrue(vm.showComposerCoachMark.value)
    }

    @Test
    fun autoHide_skipsCoachMark_whenFlagSeen() = runTest(testDispatcher) {
        every { preferencesStore.composerAutoHideCoachSeen } returns flowOf(true)
        val vm = createViewModel()
        runCurrent()

        enterDirectMode(vm)
        runCurrent()

        assertFalse(vm.showComposerCoachMark.value)
    }

    @Test
    fun markCoachMarkSeen_persistsFlag_andClearsState() = runTest(testDispatcher) {
        every { preferencesStore.composerAutoHideCoachSeen } returns flowOf(false)
        coEvery { preferencesStore.setComposerAutoHideCoachSeen() } just Runs
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)
        runCurrent()
        assertTrue(vm.showComposerCoachMark.value)

        vm.markCoachMarkSeen()
        runCurrent()

        assertFalse(vm.showComposerCoachMark.value)
        coVerify(exactly = 1) { preferencesStore.setComposerAutoHideCoachSeen() }
    }

    @Test
    fun revealComposer_dismissesPendingCoachMark() = runTest(testDispatcher) {
        every { preferencesStore.composerAutoHideCoachSeen } returns flowOf(false)
        val vm = createViewModel()
        runCurrent()
        enterDirectMode(vm)
        runCurrent()
        assertTrue(vm.showComposerCoachMark.value)

        vm.revealComposer()

        assertFalse(
            "using the affordance proves it was discovered — no tooltip needed",
            vm.showComposerCoachMark.value,
        )
    }

    private companion object {
        const val DEBOUNCE_PLUS = 500L
        const val BRACKETED_PASTE_ON = "[?2004h"
        const val ALT_SCREEN_ON = "[?1049h"
        const val ALT_SCREEN_OFF = "[?1049l"
    }
}

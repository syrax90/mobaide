package com.mobaide.app.ui.terminal

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * [TerminalViewModel.insertTranscript]: a dictated transcript lands at the
 * composer's cursor (replacing a selection), spaced from the words around it,
 * with the cursor after it and the composer revealed — and is never sent.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelInsertTranscriptTest {

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

    private fun TerminalViewModel.typed(text: String, selection: TextRange) =
        updateInputText(TextFieldValue(text, selection))

    @Test
    fun intoAnEmptyComposer_theTranscriptIsTheText_cursorAfterIt() {
        val vm = createViewModel()

        vm.insertTranscript("  fix the login bug \n")

        assertEquals(TextFieldValue("fix the login bug", TextRange(17)), vm.inputText.value)
    }

    @Test
    fun rightAfterAWord_leadsWithASpace_andReusesTheSpaceThatFollows() {
        val vm = createViewModel()
        vm.typed("please now", TextRange(6))

        vm.insertTranscript("fix it")

        assertEquals(TextFieldValue("please fix it now", TextRange("please fix it".length)), vm.inputText.value)
    }

    @Test
    fun insideAWord_spacesItFromBothHalves() {
        val vm = createViewModel()
        vm.typed("pleasenow", TextRange(6))

        vm.insertTranscript("fix it")

        assertEquals(TextFieldValue("please fix it now", TextRange("please fix it ".length)), vm.inputText.value)
    }

    @Test
    fun nextToExistingWhitespace_addsNoExtraSpace() {
        val vm = createViewModel()
        vm.typed("please  now", TextRange(7))

        vm.insertTranscript("fix it")

        assertEquals(TextFieldValue("please fix it now", TextRange("please fix it".length)), vm.inputText.value)
    }

    @Test
    fun aSelection_isReplaced_whicheverWayItWasDragged() {
        val vm = createViewModel()
        vm.typed("run the old tests", TextRange(12, 8))

        vm.insertTranscript("new")

        assertEquals("run the new tests", vm.inputText.value.text)
        assertEquals(TextRange("run the new ".length), vm.inputText.value.selection)
    }

    @Test
    fun atTheEndAfterAWord_leadsWithASpace() {
        val vm = createViewModel()
        vm.typed("git", TextRange(3))

        vm.insertTranscript("status")

        assertEquals(TextFieldValue("git status", TextRange(10)), vm.inputText.value)
    }

    @Test
    fun aBlankTranscript_changesNothing() {
        val vm = createViewModel()
        vm.typed("keep me", TextRange(4))
        vm.hideComposer()

        vm.insertTranscript("   ")

        assertEquals(TextFieldValue("keep me", TextRange(4)), vm.inputText.value)
        assertEquals(false, vm.composerVisible.value)
    }

    @Test
    fun revealsAHiddenComposer_andRequestsFocus_butNeverSends() {
        val vm = createViewModel()
        vm.hideComposer()

        vm.insertTranscript("rm -rf build")

        assertTrue(vm.composerVisible.value)
        assertTrue(vm.requestInputFocus.value)
        verify(exactly = 0) { connection.sendBinary(any()) }
    }
}

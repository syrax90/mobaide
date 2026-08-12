package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.headerLabel] — what the terminal top bar names
 * this session. Derived from the app-scoped [SessionRepository.observeSessionList]
 * matched on the session id, it renders the live OSC title once the agent pushes one
 * and the kind label until then; it never renders the session id.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelHeaderLabelTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    private val connection: WebSocketConnection = mockk(relaxed = true) {
        every { events } returns MutableSharedFlow(replay = 1, extraBufferCapacity = 100)
    }

    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun session(
        id: String = "test-session",
        title: String? = null,
        kind: SessionKind = SessionKind.Claude,
    ): SessionResponse = SessionResponse(
        session_id = id,
        state = "running",
        created_at = "2026-01-01T00:00:00Z",
        title = title,
        kind = kind,
    )

    private fun customType(id: String, name: String) = CustomAgentTypeDto(
        id = id,
        name = name,
        command = "aider",
        created_at = "2026-01-01T00:00:00Z",
        updated_at = "2026-01-01T00:00:00Z",
    )

    private fun newViewModel(
        sessions: MutableStateFlow<List<SessionResponse>>,
        types: MutableStateFlow<List<CustomAgentTypeDto>> = MutableStateFlow(emptyList()),
        restSessions: List<SessionResponse> = emptyList(),
    ): TerminalViewModel {
        val repo = mockk<SessionRepository> {
            every { createSessionIoConnection("test-session") } returns connection
            every { observeSessionList() } returns sessions
            every { observeSessionsSynced() } returns MutableStateFlow(false)
            coEvery { listSessions() } returns restSessions
        }
        val typesRepo = mockk<CustomAgentTypesRepository> {
            every { observeTypes() } returns types
        }
        return TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repo,
            customAgentTypesRepository = typesRepo,
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
        )
    }

    @Test
    fun headerLabel_isEmpty_whenNeitherSourceKnowsTheSession() =
        runTest(testDispatcher) {
            // Nothing to name it with: an empty bar beats a wrong label.
            val vm = newViewModel(MutableStateFlow(emptyList()))
            runCurrent()

            assertEquals("", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_seedsFromRest_whenLiveListEmptyOnColdStart() =
        runTest(testDispatcher) {
            // Cold launch (notification tap / deep link) builds the VM before the
            // first sessions.changed snapshot, and listSessions() never writes the
            // cache — without the one-shot seed the bar stays blank for the whole
            // visit if the event bus never connects.
            val vm = newViewModel(
                MutableStateFlow(emptyList()),
                restSessions = listOf(session(title = "* Fix the header")),
            )
            runCurrent()

            assertEquals("* Fix the header", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_prefersSnapshotThatLandsDuringTheSeedFetch() =
        runTest(testDispatcher) {
            // The REST response is a snapshot of when the request was issued. A
            // sessions.changed that arrives while it is in flight carries a newer
            // OSC title, and the seed must not overwrite it with the stale copy.
            val sessions = MutableStateFlow<List<SessionResponse>>(emptyList())
            val repo = mockk<SessionRepository> {
                every { createSessionIoConnection("test-session") } returns connection
                every { observeSessionList() } returns sessions
                every { observeSessionsSynced() } returns MutableStateFlow(false)
                coEvery { listSessions() } coAnswers {
                    sessions.value = listOf(session(title = "* Newer title"))
                    listOf(session(title = "* Stale title"))
                }
            }
            val vm = TerminalViewModel(
                savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
                sessionRepository = repo,
                customAgentTypesRepository = mockk {
                    every { observeTypes() } returns MutableStateFlow(emptyList())
                },
                templatesRepository = mockk(relaxed = true),
                shortcutsRepository = mockk(),
                foregroundSessionTracker = ForegroundSessionTracker(),
                appForegroundTracker = AppForegroundTracker(),
                preferencesStore = preferencesStore,
            )
            runCurrent()

            assertEquals("* Newer title", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_skipsRestSeed_whenLiveListAlreadyHasSession() =
        runTest(testDispatcher) {
            // Normal navigation from the session list: the row is already there,
            // and sessionKind shares this flow, so neither costs a fetch.
            val sessions = MutableStateFlow(listOf(session(title = "* Fix the header")))
            val repo = mockk<SessionRepository> {
                every { createSessionIoConnection("test-session") } returns connection
                every { observeSessionList() } returns sessions
                every { observeSessionsSynced() } returns MutableStateFlow(false)
                coEvery { listSessions() } returns emptyList()
            }
            val vm = TerminalViewModel(
                savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
                sessionRepository = repo,
                customAgentTypesRepository = mockk {
                    every { observeTypes() } returns MutableStateFlow(emptyList())
                },
                templatesRepository = mockk(relaxed = true),
                shortcutsRepository = mockk(),
                foregroundSessionTracker = ForegroundSessionTracker(),
                appForegroundTracker = AppForegroundTracker(),
                preferencesStore = preferencesStore,
            )
            runCurrent()

            assertEquals("* Fix the header", vm.headerLabel.value)
            coVerify(exactly = 0) { repo.listSessions() }
        }

    @Test
    fun headerLabel_rendersKind_whenSessionHasNoTitleYet() =
        runTest(testDispatcher) {
            val vm = newViewModel(MutableStateFlow(listOf(session(kind = SessionKind.Codex))))
            runCurrent()

            assertEquals("Codex", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_matchesOnSessionId() =
        runTest(testDispatcher) {
            val other = session(id = "other-session", title = "Someone else's task")
            val vm = newViewModel(MutableStateFlow(listOf(other)))
            runCurrent()

            assertEquals("", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_flipsToTitle_whenAgentPushesOne() =
        runTest(testDispatcher) {
            val sessions = MutableStateFlow(listOf(session()))
            val vm = newViewModel(sessions)
            runCurrent()
            assertEquals("pre-condition: untitled session shows its kind", "Claude Code", vm.headerLabel.value)

            sessions.value = listOf(session(title = "* Fix the header"))
            runCurrent()

            assertEquals("* Fix the header", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_keepsLastLabel_whenSessionLeavesTheList() =
        runTest(testDispatcher) {
            // The screen pops itself once the snapshot proves the session is gone —
            // blanking the bar first would only flash an empty title on the way out.
            val sessions = MutableStateFlow(listOf(session(title = "* Fix the header")))
            val vm = newViewModel(sessions)
            runCurrent()

            sessions.value = emptyList()
            runCurrent()

            assertEquals("* Fix the header", vm.headerLabel.value)
        }

    @Test
    fun headerLabel_resolvesCustomKindThroughLiveRegistry() =
        runTest(testDispatcher) {
            val sessions = MutableStateFlow(
                listOf(session(kind = SessionKind.Custom("custom_ab12cd34ef56"))),
            )
            val types = MutableStateFlow(listOf(customType("ab12cd34ef56", "Aider")))
            val vm = newViewModel(sessions, types)
            runCurrent()

            assertEquals("Aider", vm.headerLabel.value)
        }
}

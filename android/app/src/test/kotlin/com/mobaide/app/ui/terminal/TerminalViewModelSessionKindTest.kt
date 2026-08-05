package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
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
 * Unit tests for [TerminalViewModel.sessionKind] — the CLI-aware control-key bar reads
 * this to pick the Codex vs Claude/shell key set. It is derived from the app-scoped
 * [SessionRepository.observeSessionList] (the single source of truth), matched on the
 * session id, staying null (kind-neutral rendering, proto-v7 — never a guessed Claude
 * seed, which would flash the wrong bar for Codex/custom sessions) until the snapshot
 * resolves and flipping reactively when it does. On a cold launch (the WS list still
 * empty), a one-shot [SessionRepository.listSessions] REST seed resolves the kind
 * before the first snapshot.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelSessionKindTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    private val connection: WebSocketConnection = mockk(relaxed = true) {
        every { events } returns MutableSharedFlow(replay = 1, extraBufferCapacity = 100)
    }

    private val templatesRepository =
        mockk<com.mobaide.app.data.repository.TemplatesRepository>(relaxed = true)

    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun session(id: String, kind: SessionKind): SessionResponse =
        SessionResponse(
            session_id = id,
            state = "running",
            created_at = "2026-01-01T00:00:00Z",
            kind = kind,
        )

    private fun newRepository(
        sessions: MutableStateFlow<List<SessionResponse>>,
        restSessions: List<SessionResponse> = emptyList(),
    ): SessionRepository = mockk {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns sessions
        every { observeSessionsSynced() } returns MutableStateFlow(false)
        coEvery { listSessions() } returns restSessions
    }

    private fun newViewModel(repo: SessionRepository): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repo,
            templatesRepository = templatesRepository,
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = preferencesStore,
        )

    @Test
    fun sessionKind_resolvesCodex_whenListHasMatchingCodexSession() =
        runTest(testDispatcher) {
            val sessions = MutableStateFlow(listOf(session("test-session", SessionKind.Codex)))
            val vm = newViewModel(newRepository(sessions))
            runCurrent()

            assertEquals(SessionKind.Codex, vm.sessionKind.value)
        }

    @Test
    fun sessionKind_staysNull_whenSessionNotInList() =
        runTest(testDispatcher) {
            // Unresolved must be null, not a guessed Claude — consumers render
            // the kind-neutral default bar and skip MRU bucket writes.
            val sessions = MutableStateFlow<List<SessionResponse>>(emptyList())
            val vm = newViewModel(newRepository(sessions))
            runCurrent()

            assertEquals(null, vm.sessionKind.value)
        }

    @Test
    fun sessionKind_ignoresOtherSessions_matchingOnlyOnSessionId() =
        runTest(testDispatcher) {
            val sessions = MutableStateFlow(listOf(session("other-session", SessionKind.Codex)))
            val vm = newViewModel(newRepository(sessions))
            runCurrent()

            assertEquals(null, vm.sessionKind.value)
        }

    @Test
    fun sessionKind_resolvesCustomKind_preservingRawWireString() =
        runTest(testDispatcher) {
            // A custom-agent-type session must resolve to its verbatim wire
            // kind — the key bar and shortcut buckets key off the raw string.
            val kind = SessionKind.Custom("custom_ab12cd34ef56")
            val sessions = MutableStateFlow(listOf(session("test-session", kind)))
            val vm = newViewModel(newRepository(sessions))
            runCurrent()

            assertEquals(kind, vm.sessionKind.value)
        }

    @Test
    fun sessionKind_flipsToCodex_whenSnapshotArrivesAfterInit() =
        runTest(testDispatcher) {
            val sessions = MutableStateFlow<List<SessionResponse>>(emptyList())
            val vm = newViewModel(newRepository(sessions))
            runCurrent()
            assertEquals(
                "pre-condition: empty list stays unresolved (null)",
                null,
                vm.sessionKind.value,
            )

            sessions.value = listOf(session("test-session", SessionKind.Codex))
            runCurrent()

            assertEquals(SessionKind.Codex, vm.sessionKind.value)
        }

    @Test
    fun sessionKind_seedsCodexFromRest_whenLiveListEmptyOnColdStart() =
        runTest(testDispatcher) {
            // Cold launch (notification tap / deep link): the WS-pushed list has not
            // delivered its first snapshot yet (empty), but the one-shot REST seed
            // resolves the Codex kind so the bar shows the right keys immediately.
            val sessions = MutableStateFlow<List<SessionResponse>>(emptyList())
            val vm = newViewModel(
                newRepository(
                    sessions,
                    restSessions = listOf(session("test-session", SessionKind.Codex)),
                ),
            )
            runCurrent()

            assertEquals(SessionKind.Codex, vm.sessionKind.value)
        }

    @Test
    fun sessionKind_skipsRestSeed_whenLiveListAlreadyHasSession() =
        runTest(testDispatcher) {
            // Normal navigation from the session list: the live list already holds the
            // session, so the cold-launch REST seed must not fire a redundant fetch.
            val sessions = MutableStateFlow(listOf(session("test-session", SessionKind.Codex)))
            val repo = newRepository(sessions)
            val vm = newViewModel(repo)
            runCurrent()

            assertEquals(SessionKind.Codex, vm.sessionKind.value)
            coVerify(exactly = 0) { repo.listSessions() }
        }

    @Test
    fun sessionKind_staysNull_whenRestSeedFetchFails() =
        runTest(testDispatcher) {
            // A failed REST seed must not crash the flow: the kind stays
            // unresolved (null), and the live list can still resolve it
            // reactively later.
            val sessions = MutableStateFlow<List<SessionResponse>>(emptyList())
            val repo = mockk<SessionRepository> {
                every { createSessionIoConnection("test-session") } returns connection
                every { observeSessionList() } returns sessions
                every { observeSessionsSynced() } returns MutableStateFlow(false)
                coEvery { listSessions() } throws RuntimeException("offline")
            }
            val vm = newViewModel(repo)
            runCurrent()

            assertEquals(null, vm.sessionKind.value)
        }
}

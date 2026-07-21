package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.ShortcutDto
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.ShortcutsRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Called
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for the [TerminalViewModel] saved-shortcut path:
 * [TerminalViewModel.savedShortcutsForKind] plus the
 * refresh/save/rename/remove actions backed by [ShortcutsRepository].
 *
 * Load-bearing contracts: NOTHING touches the repository before the UI first
 * collects or an action fires — `savedShortcutsForKind` is `by lazy` and every
 * existing VM test passes a strict zero-stub [ShortcutsRepository] mock, so an
 * eager access would break the whole suite (same tripwire as
 * [TerminalViewModelSendChordTest.shortcutRecents_emptyAndUntouched_beforeFirstRefresh]).
 * The mapped list is kind-filtered by wire name, sorted by
 * (position, created_at, shortcut_id), drops unparseable tokens, blank names
 * collapse to null, and riskiness is re-derived from the encoder — and it is
 * gated on a RESOLVED kind: until the session list (or REST seed) names the
 * real kind, the list stays empty rather than rendering the eagerly-seeded
 * Claude default's bucket. Saves
 * re-resolve the session's kind from the live session list (REST fallback) —
 * never the eagerly-seeded `sessionKind.value` Claude default — and go out
 * under that kind's wire name with the chord's token and a trimmed
 * (blank -> null) name; an unresolvable kind aborts the save with a snackbar
 * before the repository is touched; failures surface as snackbars, never
 * silently.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelSavedShortcutsTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
    }
    private val sessionListFlow = MutableStateFlow<List<SessionResponse>>(emptyList())
    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns sessionListFlow
    }
    private val templatesRepository = mockk<TemplatesRepository>(relaxed = true)
    // Deliberately strict with zero default stubs — the laziness tripwire.
    private val shortcutsRepository = mockk<ShortcutsRepository>()
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            templatesRepository = templatesRepository,
            shortcutsRepository = shortcutsRepository,
            foregroundSessionTracker = ForegroundSessionTracker(),
            preferencesStore = preferencesStore,
        )

    private fun session(
        id: String = "test-session",
        kind: SessionKind = SessionKind.Claude,
    ): SessionResponse = SessionResponse(
        session_id = id,
        state = "running",
        created_at = "2026-07-20T12:00:00Z",
        kind = kind,
    )

    private fun dto(
        id: String,
        kind: String = "claude",
        chordToken: String,
        name: String? = null,
        position: Int = 0,
        createdAt: String = "2026-07-20T12:00:00Z",
    ): ShortcutDto = ShortcutDto(
        shortcut_id = id,
        kind = kind,
        chord_token = chordToken,
        name = name,
        position = position,
        created_at = createdAt,
        updated_at = createdAt,
        version = 1,
    )

    @Test
    fun constructionAndIdle_neverTouchShortcutsRepository() = runTest {
        // No stubs on purpose: the strict mock makes any eager access blow up,
        // and the verify pins it to zero — the contract every existing VM test
        // fixture (strict `shortcutsRepository = mockk()`) relies on.
        createViewModel()
        advanceUntilIdle()

        verify { shortcutsRepository wasNot Called }
    }

    @Test
    fun savedShortcutsForKind_filtersByKind_dropsUnparseable_andMapsFields() = runTest {
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        every { shortcutsRepository.observeShortcuts() } returns MutableStateFlow(
            listOf(
                dto("s-b", chordToken = "ctrl+b", name = "   ", position = 1),
                dto("s-a", chordToken = "ctrl+a", name = "Alpha", position = 0),
                dto("s-codex", kind = "codex", chordToken = "ctrl+x", position = 0),
                dto("s-bad", chordToken = "meta+q", position = 2),
                dto("s-risky", chordToken = "ctrl+shift+i", position = 3),
            ),
        )
        val vm = createViewModel()

        backgroundScope.launch { vm.savedShortcutsForKind.collect {} }
        advanceUntilIdle()

        assertEquals(
            listOf(
                SavedShortcutUi(
                    id = "s-a",
                    chord = KeyChord(ctrl = true, key = "a"),
                    name = "Alpha",
                    risky = false,
                ),
                // Blank server name collapses to null so the label falls back
                // to the chord display.
                SavedShortcutUi(
                    id = "s-b",
                    chord = KeyChord(ctrl = true, key = "b"),
                    name = null,
                    risky = false,
                ),
                // CSI-u chord: riskiness is re-derived from the local encoder.
                SavedShortcutUi(
                    id = "s-risky",
                    chord = KeyChord(ctrl = true, shift = true, key = "i"),
                    name = null,
                    risky = true,
                ),
            ),
            vm.savedShortcutsForKind.value,
        )
    }

    @Test
    fun savedShortcutsForKind_sortsByPosition_thenCreatedAt_thenId() = runTest {
        // Push-applied rows append to the cache in arrival order, so the VM
        // must re-sort; created_at and id break position ties (possible after
        // cross-device races).
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        every { shortcutsRepository.observeShortcuts() } returns MutableStateFlow(
            listOf(
                dto("s-z", chordToken = "ctrl+z", position = 0, createdAt = "2026-01-01T00:00:00Z"),
                dto("s-y", chordToken = "ctrl+y", position = 0, createdAt = "2026-01-02T00:00:00Z"),
                dto("s-a", chordToken = "ctrl+a", position = 0, createdAt = "2026-01-01T00:00:00Z"),
                dto("s-first", chordToken = "ctrl+f", position = 1, createdAt = "2025-01-01T00:00:00Z"),
            ),
        )
        val vm = createViewModel()

        backgroundScope.launch { vm.savedShortcutsForKind.collect {} }
        advanceUntilIdle()

        assertEquals(
            listOf("s-a", "s-z", "s-y", "s-first"),
            vm.savedShortcutsForKind.value.map { it.id },
        )
    }

    @Test
    fun savedShortcutsForKind_kindUnresolved_staysEmpty_untilKindResolves() = runTest {
        // sessionKind eagerly seeds Claude, but the SEED must never leak into
        // the saved-button row: with the session list empty and the REST
        // fallback down, the Claude bucket in the cache stays hidden — its
        // buttons would be tappable in a session whose real kind is unknown.
        coEvery { sessionRepository.listSessions() } throws RuntimeException("offline")
        every { shortcutsRepository.observeShortcuts() } returns MutableStateFlow(
            listOf(dto("s-a", chordToken = "ctrl+a", name = "Alpha")),
        )
        val vm = createViewModel()

        vm.savedShortcutsForKind.test {
            assertEquals(emptyList<SavedShortcutUi>(), awaitItem())
            advanceUntilIdle()
            expectNoEvents()

            // First sessions.changed snapshot resolves the real kind — only
            // now may that kind's bucket render.
            sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
            advanceUntilIdle()
            assertEquals(
                listOf(
                    SavedShortcutUi(
                        id = "s-a",
                        chord = KeyChord(ctrl = true, key = "a"),
                        name = "Alpha",
                        risky = false,
                    ),
                ),
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun refreshSavedShortcuts_delegatesToRepository() = runTest {
        coJustRun { shortcutsRepository.refreshShortcuts() }
        val vm = createViewModel()

        vm.refreshSavedShortcuts()
        advanceUntilIdle()

        coVerify(exactly = 1) { shortcutsRepository.refreshShortcuts() }
    }

    @Test
    fun refreshSavedShortcuts_whileInFlight_doesNotStartSecondFetch() = runTest {
        val gate = CompletableDeferred<Unit>()
        coEvery { shortcutsRepository.refreshShortcuts() } coAnswers { gate.await() }
        val vm = createViewModel()

        vm.refreshSavedShortcuts()
        vm.refreshSavedShortcuts()
        gate.complete(Unit)
        advanceUntilIdle()

        coVerify(exactly = 1) { shortcutsRepository.refreshShortcuts() }
    }

    @Test
    fun refreshSavedShortcuts_failure_emitsUserMessage_andGuardDoesNotLatch() = runTest {
        coEvery { shortcutsRepository.refreshShortcuts() } throws RuntimeException("boom")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.refreshSavedShortcuts()
            advanceUntilIdle()
            val message = awaitItem()
            assertTrue(
                "a failed fetch must surface a snackbar, never fail silently; got: \"$message\"",
                message.startsWith("Failed to load saved shortcuts") &&
                    message.contains("boom"),
            )
            cancelAndIgnoreRemainingEvents()
        }

        // The guard must reset after a failure so the next open retries.
        vm.refreshSavedShortcuts()
        advanceUntilIdle()
        coVerify(exactly = 2) { shortcutsRepository.refreshShortcuts() }
    }

    @Test
    fun saveShortcut_sendsWireKindAndToken_withTrimmedName_andSuccessSnackbar() = runTest {
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", chordToken = "ctrl+x", name = "Build")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.saveShortcut(KeyChord(ctrl = true, key = "x"), "  Build  ")
            advanceUntilIdle()
            assertEquals("Saved shortcut \"Build\"", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        // "claude" pins the wire name of the kind resolved from the live
        // session list — the only source saveShortcut may trust.
        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", "Build")
        }
    }

    @Test
    fun saveShortcut_blankName_savesUnnamed_andSnackbarFallsBackToChordLabel() = runTest {
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", chordToken = "ctrl+x")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.saveShortcut(KeyChord(ctrl = true, key = "x"), "   ")
            advanceUntilIdle()
            assertEquals("Saved shortcut \"Ctrl+X\"", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", null)
        }
    }

    @Test
    fun saveShortcut_failure_emitsFailureSnackbar() = runTest {
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } throws RuntimeException("boom")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.saveShortcut(KeyChord(ctrl = true, key = "x"), null)
            advanceUntilIdle()
            val message = awaitItem()
            assertTrue(
                "a failed save must surface a snackbar; got: \"$message\"",
                message.startsWith("Failed to save shortcut") && message.contains("boom"),
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun saveShortcut_kindFromLiveList_matchesThisSession_notAnotherEntry() = runTest {
        // The resolver must key on this VM's sessionId, not just take any
        // list entry — and must use that entry's real kind.
        sessionListFlow.value = listOf(
            session(id = "other-session", kind = SessionKind.Claude),
            session(id = "test-session", kind = SessionKind.Codex),
        )
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", kind = "codex", chordToken = "ctrl+x")
        val vm = createViewModel()

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"), null)
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("codex", "ctrl+x", null)
        }
    }

    @Test
    fun saveShortcut_coldLaunch_waitsForRestKind_neverSeededClaudeDefault() = runTest {
        // The regression this pins: sessionKind is eagerly seeded Claude, so
        // a save fired before the cold-launch REST seed lands used to file a
        // Codex session's shortcut under "claude" permanently. The save must
        // suspend on the REST fetch and use the kind it resolves.
        val gate = CompletableDeferred<List<SessionResponse>>()
        coEvery { sessionRepository.listSessions() } coAnswers { gate.await() }
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", kind = "codex", chordToken = "ctrl+x")
        val vm = createViewModel()

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"), null)
        advanceUntilIdle() // the save is now suspended on the REST fetch
        gate.complete(listOf(session(kind = SessionKind.Codex)))
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("codex", "ctrl+x", null)
        }
    }

    @Test
    fun saveShortcut_kindUnresolvable_abortsWithSnackbar_andNeverTouchesRepository() = runTest {
        // Cold launch with the session absent from the live list and the
        // REST fallback failing: the save must abort with a snackbar rather
        // than crash or fall back to the seeded Claude default.
        coEvery { sessionRepository.listSessions() } throws RuntimeException("offline")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.saveShortcut(KeyChord(ctrl = true, key = "x"), "Build")
            advanceUntilIdle()
            assertEquals(
                "Failed to save shortcut: session type not known yet",
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
        verify { shortcutsRepository wasNot Called }
    }

    @Test
    fun renameShortcut_delegatesWithTrimmedName() = runTest {
        coEvery {
            shortcutsRepository.renameShortcut(any(), any())
        } returns dto("s-1", chordToken = "ctrl+x", name = "New")
        val vm = createViewModel()

        vm.renameShortcut("s-1", "  New  ")
        advanceUntilIdle()

        coVerify(exactly = 1) { shortcutsRepository.renameShortcut("s-1", "New") }
    }

    @Test
    fun renameShortcut_blankName_sendsEmptyStringToClear() = runTest {
        // Blank collapses to "" — the wire sentinel that clears the custom
        // name back to the chord label.
        coEvery {
            shortcutsRepository.renameShortcut(any(), any())
        } returns dto("s-1", chordToken = "ctrl+x")
        val vm = createViewModel()

        vm.renameShortcut("s-1", "   ")
        advanceUntilIdle()

        coVerify(exactly = 1) { shortcutsRepository.renameShortcut("s-1", "") }
    }

    @Test
    fun renameShortcut_failure_emitsUserMessage() = runTest {
        coEvery {
            shortcutsRepository.renameShortcut(any(), any())
        } throws RuntimeException("boom")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.renameShortcut("s-1", "New")
            advanceUntilIdle()
            assertEquals("Failed to rename shortcut", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun removeShortcut_delegatesToRepository() = runTest {
        coJustRun { shortcutsRepository.deleteShortcut(any()) }
        val vm = createViewModel()

        vm.removeShortcut("s-1")
        advanceUntilIdle()

        coVerify(exactly = 1) { shortcutsRepository.deleteShortcut("s-1") }
    }

    @Test
    fun removeShortcut_failure_emitsUserMessage() = runTest {
        coEvery {
            shortcutsRepository.deleteShortcut(any())
        } throws RuntimeException("boom")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.removeShortcut("s-1")
            advanceUntilIdle()
            assertEquals("Failed to remove shortcut", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }
}

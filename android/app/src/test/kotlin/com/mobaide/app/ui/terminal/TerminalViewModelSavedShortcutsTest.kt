package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.ShortcutDto
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.ShortcutsRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Called
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.every
import io.mockk.justRun
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
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
 * under that kind's wire name with the chord's token, always unnamed (the
 * picker names a saved shortcut afterwards via a separate rename action); a
 * successful save is silent — no snackbar, since the picker's derived
 * "Saved" state is itself the confirmation; a save that resolves the kind
 * publishes it, so the gated-empty list opens up and that derived confirmation
 * can actually render; a save of a chord already in flight is dropped, since
 * the server's upsert probe sees live rows only and a late duplicate would
 * resurrect a chord deleted in between — but the per-chord guard always
 * releases, on success, on failure and on abort alike; an unresolvable kind
 * aborts the save with a snackbar
 * before the repository is touched and publishes nothing; failures surface as
 * snackbars, never silently — except an already-gone delete (403/404, the
 * non-idempotent DELETE a double tap produces), which is absorbed into a cache
 * reconcile instead of a false failure report.
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
        every { observeSessionsSynced() } returns MutableStateFlow(false)
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
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = templatesRepository,
            shortcutsRepository = shortcutsRepository,
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = mockk(relaxed = true),
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
    fun saveShortcut_sendsWireKindFromLiveSessionList_andToken_unnamed() = runTest {
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", chordToken = "ctrl+x")
        val vm = createViewModel()

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()

        // "claude" pins the wire name of the kind resolved from the live
        // session list — the only source saveShortcut may trust — and the
        // picker always saves unnamed.
        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", null)
        }
    }

    @Test
    fun saveShortcut_failure_emitsFailureSnackbar_andGuardDoesNotLatch() = runTest {
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } throws RuntimeException("boom")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
            advanceUntilIdle()
            val message = awaitItem()
            assertTrue(
                "a failed save must surface a snackbar; got: \"$message\"",
                message.startsWith("Failed to save shortcut") && message.contains("boom"),
            )
            cancelAndIgnoreRemainingEvents()
        }

        // The in-flight guard clears in a finally, so a failed save leaves the
        // chord savable — a latched guard would make the retry the snackbar
        // just invited impossible.
        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()
        coVerify(exactly = 2) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", null)
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

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("codex", "ctrl+x", null)
        }
    }

    @Test
    fun savedShortcutsForKind_customKind_filtersByRawWireKind() = runTest {
        // Per-type shortcut scoping: a custom-agent session's saved
        // row must show only its own "custom_<id>" bucket — never another
        // custom type's bucket and never a built-in kind's.
        sessionListFlow.value = listOf(
            session(kind = SessionKind.Custom("custom_ab12cd34ef56")),
        )
        every { shortcutsRepository.observeShortcuts() } returns MutableStateFlow(
            listOf(
                dto("s-mine", kind = "custom_ab12cd34ef56", chordToken = "ctrl+a", name = "Mine"),
                dto("s-other-type", kind = "custom_ffffffffffff", chordToken = "ctrl+b"),
                dto("s-claude", kind = "claude", chordToken = "ctrl+c"),
            ),
        )
        val vm = createViewModel()

        backgroundScope.launch { vm.savedShortcutsForKind.collect {} }
        advanceUntilIdle()

        assertEquals(
            listOf(
                SavedShortcutUi(
                    id = "s-mine",
                    chord = KeyChord(ctrl = true, key = "a"),
                    name = "Mine",
                    risky = false,
                ),
            ),
            vm.savedShortcutsForKind.value,
        )
    }

    @Test
    fun saveShortcut_customKindSession_savesUnderRawWireKind() = runTest {
        // The save must go out under the raw "custom_<id>" wire kind — the id
        // (not the display name) keys the bucket, so renames never orphan it.
        sessionListFlow.value = listOf(
            session(kind = SessionKind.Custom("custom_ab12cd34ef56")),
        )
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", kind = "custom_ab12cd34ef56", chordToken = "ctrl+x")
        val vm = createViewModel()

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("custom_ab12cd34ef56", "ctrl+x", null)
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

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
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
            vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
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
    fun saveShortcut_publishesTheKindItResolved_soTheSavedListStopsBeingGatedEmpty() = runTest {
        // Cold launch while offline: the one-shot REST seed behind sessionKind
        // fails and the WS session list never arrives, so resolvedKind stays
        // null and savedShortcutsForKind is gated empty. The user taps Save
        // once the network is back. A successful save is silent, so the
        // derived "Saved as …" hint row is the ONLY feedback there is — and it
        // cannot appear unless the save publishes the kind it just resolved.
        var restCalls = 0
        coEvery { sessionRepository.listSessions() } coAnswers {
            if (restCalls++ == 0) throw RuntimeException("offline")
            listOf(session(kind = SessionKind.Claude))
        }
        val cache = MutableStateFlow<List<ShortcutDto>>(emptyList())
        every { shortcutsRepository.observeShortcuts() } returns cache
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } coAnswers {
            dto("s-1", chordToken = "ctrl+x").also { cache.value = listOf(it) }
        }
        val vm = createViewModel()

        backgroundScope.launch { vm.savedShortcutsForKind.collect {} }
        advanceUntilIdle()
        assertEquals(
            "precondition: the kind is unresolved, so the list starts gated empty",
            emptyList<SavedShortcutUi>(),
            vm.savedShortcutsForKind.value,
        )

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()

        assertEquals(
            listOf(
                SavedShortcutUi(
                    id = "s-1",
                    chord = KeyChord(ctrl = true, key = "x"),
                    name = null,
                    risky = false,
                ),
            ),
            vm.savedShortcutsForKind.value,
        )
    }

    @Test
    fun saveShortcut_kindUnresolvable_publishesNoKind_soTheSavedListStaysEmpty() = runTest {
        // The publish must sit AFTER the null-kind abort: opening the gate on
        // an aborted save — say by falling back to the eagerly-seeded Claude
        // default — would hand the picker another kind's buttons in a session
        // whose kind is still unknown.
        coEvery { sessionRepository.listSessions() } throws RuntimeException("offline")
        every { shortcutsRepository.observeShortcuts() } returns MutableStateFlow(
            listOf(dto("s-a", chordToken = "ctrl+a", name = "Alpha")),
        )
        val vm = createViewModel()

        backgroundScope.launch { vm.savedShortcutsForKind.collect {} }
        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()

        assertEquals(emptyList<SavedShortcutUi>(), vm.savedShortcutsForKind.value)
        coVerify(exactly = 0) {
            shortcutsRepository.createShortcut(any(), any(), any())
        }
    }

    @Test
    fun saveShortcut_duplicateWhileInFlight_isDropped_andTheGuardReleasesAfterwards() = runTest {
        // A double tap on Save is ordinary: the toggle only flips once the
        // response lands, so it reads "Save" for the whole round trip. Letting
        // the second POST through is not just wasteful — the server's upsert
        // probe only sees LIVE rows, so a duplicate arriving after the user has
        // let the first land and then tapped "Saved" to delete takes the create
        // branch and resurrects a chord they deliberately removed.
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        val gate = CompletableDeferred<Unit>()
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } coAnswers {
            gate.await()
            dto("s-1", chordToken = "ctrl+x")
        }
        val vm = createViewModel()
        val chord = KeyChord(ctrl = true, key = "x")

        vm.saveShortcut(chord)
        advanceUntilIdle() // the first save is now suspended inside createShortcut
        vm.saveShortcut(chord)
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut(any(), any(), any())
        }

        gate.complete(Unit)
        advanceUntilIdle()

        // Releasing matters more than dropping: a guard that latched would make
        // this chord permanently unsavable for the life of the ViewModel.
        vm.saveShortcut(chord)
        advanceUntilIdle()
        coVerify(exactly = 2) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", null)
        }
    }

    @Test
    fun saveShortcut_differentChordsInFlight_bothReachTheRepository() = runTest {
        // The guard is keyed on the chord token, not one global flag: a save
        // still in flight must not swallow a different chord queued behind it.
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        val gate = CompletableDeferred<Unit>()
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } coAnswers {
            gate.await()
            dto("s-1", chordToken = "ctrl+x")
        }
        val vm = createViewModel()

        vm.saveShortcut(KeyChord(ctrl = true, key = "x"))
        advanceUntilIdle()
        vm.saveShortcut(KeyChord(ctrl = true, key = "y"))
        gate.complete(Unit)
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", null)
        }
        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("claude", "ctrl+y", null)
        }
    }

    @Test
    fun saveShortcut_abortedOnUnresolvableKind_releasesTheGuard_soARetryIsNotBlocked() = runTest {
        // The guard is taken BEFORE the coroutine starts, so the null-kind
        // abort — which returns without ever reaching createShortcut — has to
        // release it on the way out too. Otherwise the "session type not known
        // yet" snackbar would invite a retry it has itself made impossible.
        coEvery { sessionRepository.listSessions() } throws RuntimeException("offline")
        coEvery {
            shortcutsRepository.createShortcut(any(), any(), any())
        } returns dto("s-1", chordToken = "ctrl+x")
        val vm = createViewModel()
        val chord = KeyChord(ctrl = true, key = "x")

        vm.saveShortcut(chord)
        advanceUntilIdle()
        coVerify(exactly = 0) {
            shortcutsRepository.createShortcut(any(), any(), any())
        }

        // The first sessions.changed snapshot lands and the user taps again.
        sessionListFlow.value = listOf(session(kind = SessionKind.Claude))
        vm.saveShortcut(chord)
        advanceUntilIdle()

        coVerify(exactly = 1) {
            shortcutsRepository.createShortcut("claude", "ctrl+x", null)
        }
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
        // deleteShortcut reconciles its own cache on success; a second
        // applyDeleted here would be the ViewModel duplicating that job.
        verify(exactly = 0) { shortcutsRepository.applyDeleted(any()) }
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
        // A non-API failure says nothing about the row's server-side fate, so
        // the cache must be left alone rather than optimistically pruned.
        verify(exactly = 0) { shortcutsRepository.applyDeleted(any()) }
    }

    @Test
    fun removeShortcut_serverSaysAlreadyGone403_prunesCacheSilently() = runTest {
        assertAlreadyGoneIsAbsorbed(code = 403)
    }

    @Test
    fun removeShortcut_serverSaysAlreadyGone404_prunesCacheSilently() = runTest {
        assertAlreadyGoneIsAbsorbed(code = 404)
    }

    /**
     * DELETE is not idempotent server-side: the ownership check filters on
     * `deleted_at IS NULL`, so deleting an already-soft-deleted row answers 403
     * (404 when the row was never visible). The picker's Saved toggle carries
     * no in-flight state and stays tappable for the whole round trip, so a
     * double tap removed the shortcut correctly and then reported a failure —
     * now straight into the in-sheet snackbar. Ids only ever come from the
     * user's own cached list, so those two codes can only mean "already gone":
     * absorb them and reconcile the cache the failed call never got to prune.
     */
    private suspend fun TestScope.assertAlreadyGoneIsAbsorbed(code: Int) {
        coEvery {
            shortcutsRepository.deleteShortcut(any())
        } throws ApiException(code, "not the owner of this shortcut")
        justRun { shortcutsRepository.applyDeleted(any()) }
        val vm = createViewModel()

        vm.userMessages.test {
            vm.removeShortcut("s-1")
            advanceUntilIdle()
            // Not vacuous: the sibling failure tests await a real message
            // through this exact shape, so the subscription is live here too.
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 1) { shortcutsRepository.applyDeleted("s-1") }
    }

    @Test
    fun removeShortcut_apiFailureWithAnotherCode_stillReportsFailure() = runTest {
        // Only 403/404 are the already-gone signal. A 500 is a real failure and
        // the row may well still exist — absorbing it would drop the button out
        // of the UI while the server keeps serving it back on the next refresh.
        coEvery {
            shortcutsRepository.deleteShortcut(any())
        } throws ApiException(500, "internal server error")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.removeShortcut("s-1")
            advanceUntilIdle()
            assertEquals("Failed to remove shortcut", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 0) { shortcutsRepository.applyDeleted(any()) }
    }
}

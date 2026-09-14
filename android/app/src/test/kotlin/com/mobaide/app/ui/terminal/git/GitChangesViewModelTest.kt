package com.mobaide.app.ui.terminal.git

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitContextResponse
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.data.repository.SessionGitStatusLoad
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.IOException

/**
 * Unit tests for [GitChangesViewModel]. The repository poll is replaced by a
 * hand-driven [MutableSharedFlow] of [SessionGitStatusLoad]s, so each test
 * pushes exactly the load it is about; the two lambdas and the refresh flow
 * the ViewModel hands the repository are captured so the project-name lookup
 * and the etag seed can be checked as values and a test can stand in for the
 * poll that listens for pulls.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitChangesViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private class Fixture(
        val vm: GitChangesViewModel,
        val repo: GitRepository,
        val handle: SavedStateHandle,
        val loads: MutableSharedFlow<SessionGitStatusLoad>,
        val refreshRequests: Flow<Unit>,
        val tracker: ForegroundSessionTracker,
        val projectName: () -> String?,
        val initial: () -> GitStatusResponse?,
    )

    private fun newFixture(
        projectName: String? = "alpha",
        capabilities: GitCapabilities = GitCapabilities.NONE,
        commit: Boolean = false,
    ): Fixture {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.gitCapabilities() } returns capabilities
        val loads = MutableSharedFlow<SessionGitStatusLoad>(replay = 0, extraBufferCapacity = 8)
        val projectNameSlot = slot<() -> String?>()
        val refreshSlot = slot<Flow<Unit>>()
        val initialSlot = slot<() -> GitStatusResponse?>()
        every {
            repo.observeSessionGitStatus(
                sessionId = "s1",
                projectName = capture(projectNameSlot),
                foreground = any(),
                refreshRequests = capture(refreshSlot),
                initial = capture(initialSlot),
                interval = any(),
            )
        } returns loads
        val sessionRepository = mockk<SessionRepository> {
            every { observeSessionList() } returns MutableStateFlow(
                listOf(
                    SessionResponse(
                        session_id = "s1",
                        state = "running",
                        created_at = "2026-09-07T10:00:00Z",
                        cwd = "/repo",
                        project_name = projectName,
                    ),
                ),
            )
        }
        val tracker = ForegroundSessionTracker()
        val handle = SavedStateHandle(mapOf("sessionId" to "s1", "commit" to commit))
        val vm = GitChangesViewModel(
            savedStateHandle = handle,
            gitRepository = repo,
            sessionRepository = sessionRepository,
            appForegroundTracker = AppForegroundTracker(),
            foregroundSessionTracker = tracker,
        )
        return Fixture(
            vm = vm,
            repo = repo,
            handle = handle,
            loads = loads,
            refreshRequests = refreshSlot.captured,
            tracker = tracker,
            projectName = { projectNameSlot.captured() },
            initial = { initialSlot.captured() },
        )
    }

    /** `WhileSubscribed` sharing: nothing flows until someone collects. */
    private fun TestScope.subscribe(vm: GitChangesViewModel) {
        backgroundScope.launch { vm.uiState.collect { } }
        runCurrent()
    }

    /**
     * The real poll collects the ViewModel's refresh requests for as long as
     * it runs; the stubbed one never does, so a test that pulls stands in for
     * it. Cancel the returned collector to end the poll.
     */
    private fun TestScope.attachFeed(f: Fixture): Job {
        val feed = backgroundScope.launch { f.refreshRequests.collect { } }
        runCurrent()
        return feed
    }

    private fun entry(
        group: String,
        path: String,
        code: String,
        origPath: String? = null,
    ): GitStatusEntry = GitStatusEntry(
        group = group,
        path = path,
        orig_path = origPath,
        code = code,
        index_status = code,
        worktree_status = ".",
    )

    private val conflict = entry(GitStatusEntry.GROUP_CONFLICTS, "src/conflict.py", "UU")
    private val staged = entry(GitStatusEntry.GROUP_STAGED, "src/new name.py", "R", "src/old.py")
    private val unstaged = entry(GitStatusEntry.GROUP_UNSTAGED, "src/both.py", "M")
    private val untracked = entry(GitStatusEntry.GROUP_UNTRACKED, "notes/тест.md", "?")

    private fun status(
        etag: String = "e1",
        entries: List<GitStatusEntry> = listOf(untracked, staged, conflict, unstaged),
        repo: Boolean = true,
        unavailableReason: String? = null,
    ): GitStatusResponse = GitStatusResponse(
        session_id = "s1",
        cwd = "/repo",
        etag = etag,
        unavailable_reason = unavailableReason,
        repo = if (!repo) null else GitRepoInfo(
            toplevel = "/repo",
            git_dir = "/repo/.git",
            common_dir = "/repo/.git",
            head = GitHeadInfo(branch = "main"),
        ),
        entries = entries,
    )

    private fun ready(vm: GitChangesViewModel): GitChangesUiState.Ready =
        vm.uiState.value as GitChangesUiState.Ready

    private val allCaps = GitCapabilities(stage = true, discard = true, commit = true)

    private fun gitError(code: String, message: String? = null) =
        SessionGitException.GitError(code, message, ApiException(409, ""))

    /** Counts the pulls the ViewModel asks the (stubbed) poll for. */
    private fun TestScope.countPulls(f: Fixture): MutableList<Unit> {
        val pulls = mutableListOf<Unit>()
        backgroundScope.launch { f.refreshRequests.collect { pulls += it } }
        runCurrent()
        return pulls
    }

    /** A subscribed ViewModel showing [status], with [capabilities] already probed. */
    private suspend fun TestScope.loaded(capabilities: GitCapabilities = allCaps): Fixture {
        val f = newFixture(capabilities = capabilities)
        subscribe(f.vm)
        f.loads.emit(SessionGitStatusLoad.Loaded(status()))
        runCurrent()
        return f
    }

    // MARK: - Load → state

    @Test
    fun startsLoading_untilTheFirstLoad() = runTest {
        val f = newFixture()
        subscribe(f.vm)
        assertEquals(GitChangesUiState.Loading, f.vm.uiState.value)
    }

    @Test
    fun loaded_becomesReady_withSectionsInDisplayOrder() = runTest {
        // Entries arrive in the agent's order; the screen groups them
        // Conflicts / Staged / Changes / Untracked regardless.
        val f = newFixture()
        subscribe(f.vm)

        f.loads.emit(SessionGitStatusLoad.Loaded(status()))
        runCurrent()

        val ready = ready(f.vm)
        assertEquals(
            listOf("Conflicts", "Staged", "Changes", "Untracked"),
            ready.sections.map { it.title },
        )
        assertEquals(listOf(conflict), ready.sections[0].entries)
        assertEquals(listOf(staged), ready.sections[1].entries)
        assertEquals(listOf(unstaged), ready.sections[2].entries)
        assertEquals(listOf(untracked), ready.sections[3].entries)
        assertFalse(ready.refreshing)
        assertNull(ready.staleError)
    }

    @Test
    fun emptyGroups_areOmitted_andACleanTreeHasNoSections() = runTest {
        val f = newFixture()
        subscribe(f.vm)

        f.loads.emit(SessionGitStatusLoad.Loaded(status(entries = listOf(untracked))))
        runCurrent()
        assertEquals(listOf("Untracked"), ready(f.vm).sections.map { it.title })

        f.loads.emit(SessionGitStatusLoad.Loaded(status(entries = emptyList())))
        runCurrent()
        assertTrue(ready(f.vm).sections.isEmpty())
    }

    @Test
    fun sectionKeys_prefixTheGroup_andNumberRepeatedPaths() {
        // The agent's lossy filename decoding can hand two entries the same
        // path; LazyColumn needs a distinct key for each row.
        val twin = entry(GitStatusEntry.GROUP_UNTRACKED, "notes/�.md", "?")
        val stagedBoth = entry(GitStatusEntry.GROUP_STAGED, unstaged.path, "M")

        val sections = sectionsFor(status(entries = listOf(twin, unstaged, twin, stagedBoth)))

        assertEquals(listOf("Staged", "Changes", "Untracked"), sections.map { it.title })
        assertEquals(listOf("staged:1:src/both.py"), sections[0].keys)
        assertEquals(listOf("unstaged:1:src/both.py"), sections[1].keys)
        assertEquals(
            listOf("untracked:1:notes/�.md", "untracked:2:notes/�.md"),
            sections[2].keys,
        )
    }

    @Test
    fun anUnavailableReason_becomesUnavailable_withTheCwd() = runTest {
        val f = newFixture()
        subscribe(f.vm)

        f.loads.emit(
            SessionGitStatusLoad.Loaded(
                status(repo = false, unavailableReason = GitStatusResponse.UNAVAILABLE_NOT_A_REPO),
            ),
        )
        runCurrent()

        assertEquals(
            GitChangesUiState.Unavailable(GitStatusResponse.UNAVAILABLE_NOT_A_REPO, "/repo"),
            f.vm.uiState.value,
        )
    }

    @Test
    fun agentOutdated_andSessionGone_becomeTheirOwnStates() = runTest {
        val f = newFixture()
        subscribe(f.vm)

        f.loads.emit(
            SessionGitStatusLoad.Failed(null, SessionGitException.AgentOutdated(ApiException(404, ""))),
        )
        runCurrent()
        assertEquals(GitChangesUiState.AgentOutdated, f.vm.uiState.value)

        f.loads.emit(
            SessionGitStatusLoad.Failed(null, SessionGitException.SessionGone(ApiException(404, ""))),
        )
        runCurrent()
        assertEquals(GitChangesUiState.SessionGone, f.vm.uiState.value)
    }

    @Test
    fun aFailedPollWithACachedBody_keepsTheListAndFlagsItStale() = runTest {
        val f = newFixture()
        subscribe(f.vm)
        val cached = status()

        f.loads.emit(
            SessionGitStatusLoad.Failed(cached, SessionGitException.Network(IOException("down"))),
        )
        runCurrent()

        val ready = ready(f.vm)
        assertSame(cached, ready.status)
        assertEquals(4, ready.sections.size)
        assertEquals(
            "Could not reach the agent. Check the connection and try again.",
            ready.staleError,
        )
        assertFalse(ready.refreshing)
    }

    @Test
    fun aFailedPollWithoutACache_isAnError() = runTest {
        val f = newFixture()
        subscribe(f.vm)

        f.loads.emit(
            SessionGitStatusLoad.Failed(
                null,
                SessionGitException.GitError(
                    SessionGitException.CODE_GIT_TIMEOUT, null, ApiException(504, ""),
                ),
            ),
        )
        runCurrent()

        assertEquals(
            GitChangesUiState.Error("git took too long to answer. Try again."),
            f.vm.uiState.value,
        )
    }

    // MARK: - Refresh

    @Test
    fun refresh_marksReadyRefreshing_untilTheNextLoad() = runTest {
        val f = newFixture()
        subscribe(f.vm)
        attachFeed(f)
        f.loads.emit(SessionGitStatusLoad.Loaded(status("e1")))
        runCurrent()
        assertFalse(ready(f.vm).refreshing)

        f.vm.refresh()
        runCurrent()
        assertTrue(ready(f.vm).refreshing)

        val next = status("e2")
        f.loads.emit(SessionGitStatusLoad.Loaded(next, manual = true))
        runCurrent()
        val ready = ready(f.vm)
        assertSame(next, ready.status)
        assertFalse(ready.refreshing)
    }

    @Test
    fun refresh_outlivesAnOlderLoad_andClearsOnTheOneItCaused() = runTest {
        // A tick fetch already in flight when the user pulled answers first:
        // its body shows, but the spinner stays until the pull's own load lands.
        val f = newFixture()
        subscribe(f.vm)
        attachFeed(f)
        f.loads.emit(SessionGitStatusLoad.Loaded(status("e1")))
        runCurrent()

        f.vm.refresh()
        runCurrent()
        assertTrue(ready(f.vm).refreshing)

        val older = status("e2")
        f.loads.emit(SessionGitStatusLoad.Loaded(older, manual = false))
        runCurrent()
        var ready = ready(f.vm)
        assertSame(older, ready.status)
        assertTrue(ready.refreshing)

        val answer = status("e3")
        f.loads.emit(SessionGitStatusLoad.Loaded(answer, manual = true))
        runCurrent()
        ready = ready(f.vm)
        assertSame(answer, ready.status)
        assertFalse(ready.refreshing)
    }

    @Test
    fun refreshAfterTheFeedEnded_isDroppedWithoutArmingTheIndicator() = runTest {
        // An old agent or a gone session ends the poll; a pull then has no
        // fetch to answer it, so the indicator must not be armed.
        val f = newFixture()
        subscribe(f.vm)
        val feed = attachFeed(f)
        f.loads.emit(SessionGitStatusLoad.Loaded(status("e1")))
        runCurrent()

        feed.cancelAndJoin()
        f.vm.refresh()
        runCurrent()

        assertFalse(ready(f.vm).refreshing)
    }

    @Test
    fun retryWithNothingCached_showsLoading_untilTheRetryAnswers() = runTest {
        // A retry that fails the same way would otherwise rebuild an equal
        // Error, which the StateFlow dedupes into no visible reaction at all.
        val f = newFixture()
        subscribe(f.vm)
        attachFeed(f)
        val down = SessionGitStatusLoad.Failed(null, SessionGitException.Network(IOException("down")))
        val error = GitChangesUiState.Error(
            "Could not reach the agent. Check the connection and try again.",
        )

        f.loads.emit(down)
        runCurrent()
        assertEquals(error, f.vm.uiState.value)

        f.vm.refresh()
        runCurrent()
        assertEquals(GitChangesUiState.Loading, f.vm.uiState.value)

        f.loads.emit(down.copy(manual = true))
        runCurrent()
        assertEquals(error, f.vm.uiState.value)
    }

    @Test
    fun retryWithAStaleCache_marksReadyRefreshing_untilTheRetryAnswers() = runTest {
        // Same dedupe trap with a cached list: the retry has to show as
        // `refreshing` on the stale rows, not as an unchanged Ready.
        val f = newFixture()
        subscribe(f.vm)
        attachFeed(f)
        val cached = status()
        val down = SessionGitStatusLoad.Failed(
            cached, SessionGitException.Network(IOException("down")),
        )
        val stale = "Could not reach the agent. Check the connection and try again."

        f.loads.emit(down)
        runCurrent()
        var ready = ready(f.vm)
        assertSame(cached, ready.status)
        assertEquals(stale, ready.staleError)
        assertFalse(ready.refreshing)

        f.vm.refresh()
        runCurrent()
        ready = ready(f.vm)
        assertSame(cached, ready.status)
        assertTrue(ready.refreshing)
        assertEquals(stale, ready.staleError)

        f.loads.emit(down.copy(manual = true))
        runCurrent()
        ready = ready(f.vm)
        assertSame(cached, ready.status)
        assertFalse(ready.refreshing)
        assertEquals(stale, ready.staleError)
    }

    // MARK: - Diff navigation

    @Test
    fun onEntryClick_conflictOpensTheUnstagedDiff() = runTest {
        // Conflicted files are diffed against HEAD, which the agent serves as
        // `unstaged`; there is no `conflicts` scope on the wire.
        val f = newFixture()
        f.vm.events.test {
            f.vm.onEntryClick(conflict)
            runCurrent()

            val event = awaitItem() as GitChangesEvent.OpenDiff
            assertEquals("s1", event.sessionId)
            assertSame(conflict, event.entry)
            assertEquals(GitDiffResponse.SCOPE_UNSTAGED, event.scope)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun onEntryClick_forwardsTheGroupAsTheScope_forEveryOtherGroup() = runTest {
        val f = newFixture()
        f.vm.events.test {
            f.vm.onEntryClick(staged)
            f.vm.onEntryClick(unstaged)
            f.vm.onEntryClick(untracked)
            runCurrent()

            assertEquals(GitDiffResponse.SCOPE_STAGED, (awaitItem() as GitChangesEvent.OpenDiff).scope)
            assertEquals(GitDiffResponse.SCOPE_UNSTAGED, (awaitItem() as GitChangesEvent.OpenDiff).scope)
            assertEquals(GitDiffResponse.SCOPE_UNTRACKED, (awaitItem() as GitChangesEvent.OpenDiff).scope)
            cancelAndIgnoreRemainingEvents()
        }
    }

    // MARK: - Repository wiring

    @Test
    fun theRepositoryIsHandedTheSessionProjectName_andTheLastBodyAsTheSeed() = runTest {
        val f = newFixture(projectName = "alpha")
        subscribe(f.vm)
        assertEquals("alpha", f.projectName())
        assertNull(f.initial())

        val first = status("e1")
        f.loads.emit(SessionGitStatusLoad.Loaded(first))
        runCurrent()
        assertSame(first, f.initial())

        // A failure without a body must not wipe the seed either.
        f.loads.emit(
            SessionGitStatusLoad.Failed(null, SessionGitException.Network(IOException("down"))),
        )
        runCurrent()
        assertSame(first, f.initial())
    }

    @Test
    fun aSessionWithoutAProject_handsTheRepositoryNull() = runTest {
        val f = newFixture(projectName = null)
        subscribe(f.vm)
        assertNull(f.projectName())
    }

    @Test
    fun holdAndRelease_bracketTheTrackerWatch() = runTest {
        val f = newFixture()
        assertFalse(f.tracker.isHeld("s1"))
        f.vm.holdWatch()
        assertTrue(f.tracker.isHeld("s1"))
        f.vm.releaseWatch()
        assertFalse(f.tracker.isHeld("s1"))
    }

    // MARK: - Capabilities

    @Test
    fun theProbedCapabilities_landInReady() = runTest {
        val f = loaded(allCaps)

        assertEquals(allCaps, ready(f.vm).capabilities)
    }

    @Test
    fun aFailedProbe_keepsTheReadOnlyViewer() = runTest {
        val f = newFixture()
        coEvery { f.repo.gitCapabilities() } throws SessionGitException.Network(IOException("down"))
        subscribe(f.vm)

        f.loads.emit(SessionGitStatusLoad.Loaded(status()))
        runCurrent()

        assertEquals(GitCapabilities.NONE, ready(f.vm).capabilities)
        assertNull(ready(f.vm).staleError)
    }

    // MARK: - Stage / unstage

    @Test
    fun stage_sendsBothSidesOfARename_isBusyUntilTheAnswer_thenPulls() = runTest {
        val f = loaded()
        val pulls = countPulls(f)
        val reply = CompletableDeferred<Unit>()
        val sent = slot<List<String>>()
        coEvery { f.repo.stageSessionPaths("s1", capture(sent)) } coAnswers { reply.await() }

        f.vm.stage(listOf(staged))
        runCurrent()

        assertTrue(ready(f.vm).busy)
        assertEquals(listOf("src/new name.py", "src/old.py"), sent.captured)
        assertEquals(0, pulls.size)

        reply.complete(Unit)
        runCurrent()

        assertFalse(ready(f.vm).busy)
        assertEquals(1, pulls.size)
    }

    @Test
    fun aSecondMutationWhileOneIsInFlight_isDropped() = runTest {
        // In flight = suspended on the request; the busy flag is raised by the
        // launched coroutine, which the test scheduler runs on `runCurrent`.
        val f = loaded()
        val reply = CompletableDeferred<Unit>()
        val sent = mutableListOf<List<String>>()
        coEvery { f.repo.stageSessionPaths("s1", capture(sent)) } coAnswers { reply.await() }
        f.vm.stage(listOf(unstaged))
        runCurrent()

        f.vm.stage(listOf(untracked))
        f.vm.unstage(listOf(staged))
        runCurrent()
        reply.complete(Unit)
        runCurrent()

        assertEquals(listOf(listOf("src/both.py")), sent)
        assertFalse(ready(f.vm).busy)
    }

    @Test
    fun aRefusedMutation_showsItsCopy_pullsAnyway_andClearsBusy() = runTest {
        // The tree may have moved under the request (pathspec_mismatch), so
        // the list is re-read even though nothing was staged.
        val f = loaded()
        val pulls = countPulls(f)
        coEvery { f.repo.stageSessionPaths(any(), any()) } throws
            gitError(SessionGitException.CODE_INDEX_LOCKED, "index.lock exists")
        coEvery { f.repo.unstageSessionPaths(any(), any()) } throws
            gitError(SessionGitException.CODE_GIT_TOO_OLD)

        f.vm.events.test {
            f.vm.stage(listOf(unstaged))
            runCurrent()
            assertEquals(
                GitChangesEvent.ShowMessage("Another git process is running — try again."),
                awaitItem(),
            )

            f.vm.unstage(listOf(staged))
            runCurrent()
            assertEquals(
                GitChangesEvent.ShowMessage("Staging needs git 2.26 or newer on the agent machine."),
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }

        assertEquals(2, pulls.size)
        assertFalse(ready(f.vm).busy)
    }

    // MARK: - Selection

    @Test
    fun longPress_selects_tapTogglesInsteadOfOpeningTheDiff_andClearLeaves() = runTest {
        val f = loaded()

        f.vm.events.test {
            f.vm.onEntryLongClick(unstaged)
            runCurrent()
            assertEquals(setOf("unstaged:1:src/both.py"), ready(f.vm).selectedKeys)
            assertTrue(ready(f.vm).inSelectionMode)

            f.vm.onEntryClick(untracked)
            runCurrent()
            assertEquals(
                setOf("unstaged:1:src/both.py", "untracked:1:notes/тест.md"),
                ready(f.vm).selectedKeys,
            )

            f.vm.onEntryClick(untracked)
            runCurrent()
            assertEquals(setOf("unstaged:1:src/both.py"), ready(f.vm).selectedKeys)

            f.vm.clearSelection()
            runCurrent()
            assertFalse(ready(f.vm).inSelectionMode)
            expectNoEvents()

            f.vm.onEntryClick(untracked)
            runCurrent()
            assertSame(untracked, (awaitItem() as GitChangesEvent.OpenDiff).entry)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun longPress_onAConflict_orWithoutTheCapability_selectsNothing() = runTest {
        val f = loaded()
        f.vm.onEntryLongClick(conflict)
        runCurrent()
        assertFalse(ready(f.vm).inSelectionMode)

        val readOnly = loaded(GitCapabilities.NONE)
        readOnly.vm.onEntryLongClick(unstaged)
        runCurrent()
        assertFalse(ready(readOnly.vm).inSelectionMode)
    }

    @Test
    fun selectionActions_applyToTheirSubsets_andForgetTheRowsTheyActedOn() = runTest {
        val f = loaded()
        f.vm.toggleSelection("staged:1:src/new name.py")
        f.vm.toggleSelection("unstaged:1:src/both.py")
        f.vm.toggleSelection("untracked:1:notes/тест.md")
        runCurrent()
        val stagedPaths = slot<List<String>>()
        val unstagedPaths = slot<List<String>>()
        coEvery { f.repo.stageSessionPaths("s1", capture(stagedPaths)) } returns Unit
        coEvery { f.repo.unstageSessionPaths("s1", capture(unstagedPaths)) } returns Unit

        f.vm.stageSelection()
        runCurrent()

        assertEquals(listOf("src/both.py", "notes/тест.md"), stagedPaths.captured)
        assertEquals(setOf("staged:1:src/new name.py"), ready(f.vm).selectedKeys)

        f.vm.unstageSelection()
        runCurrent()

        assertEquals(listOf("src/new name.py", "src/old.py"), unstagedPaths.captured)
        assertFalse(ready(f.vm).inSelectionMode)
    }

    @Test
    fun tickedRowsThatVanishWithARefresh_areDropped() = runTest {
        val f = loaded()
        f.vm.toggleSelection("unstaged:1:src/both.py")
        f.vm.toggleSelection("untracked:1:notes/тест.md")
        runCurrent()

        f.loads.emit(SessionGitStatusLoad.Loaded(status("e2", entries = listOf(untracked))))
        runCurrent()

        assertEquals(setOf("untracked:1:notes/тест.md"), ready(f.vm).selectedKeys)
    }

    @Test
    fun enterSelection_showsTheCheckboxesBeforeAnythingIsTicked_andClearLeaves() = runTest {
        val f = loaded()

        f.vm.enterSelection()
        runCurrent()
        var ready = ready(f.vm)
        assertTrue(ready.selecting)
        assertTrue(ready.inSelectionMode)
        assertEquals(emptySet<String>(), ready.selectedKeys)

        f.vm.toggleSelection("unstaged:1:src/both.py")
        runCurrent()
        assertEquals(setOf("unstaged:1:src/both.py"), ready(f.vm).selectedKeys)

        f.vm.clearSelection()
        runCurrent()
        ready = ready(f.vm)
        assertFalse(ready.selecting)
        assertFalse(ready.inSelectionMode)
        assertEquals(emptySet<String>(), ready.selectedKeys)
    }

    @Test
    fun aMutationOnAnUntickedRow_keepsTheTickedOnes() = runTest {
        // The prune after a mutation is keyed by entry: a rename carries an
        // `orig_path` too, and acting on it must forget only its own key.
        val f = loaded()
        f.vm.toggleSelection("unstaged:1:src/both.py")
        runCurrent()
        coEvery { f.repo.unstageSessionPaths("s1", any()) } returns Unit

        f.vm.unstage(listOf(staged))
        runCurrent()

        assertEquals(setOf("unstaged:1:src/both.py"), ready(f.vm).selectedKeys)
        assertTrue(ready(f.vm).inSelectionMode)
        assertFalse(ready(f.vm).busy)
    }

    // MARK: - Discard

    @Test
    fun discard_goesThroughThePrompt_thenSendsOneRequestPerScope() = runTest {
        val f = loaded()
        val pulls = countPulls(f)
        val calls = mutableListOf<Pair<List<String>, String>>()
        coEvery { f.repo.discardSessionPaths("s1", any(), any()) } coAnswers {
            calls += secondArg<List<String>>() to thirdArg<String>()
        }

        f.vm.requestDiscard(emptyList())
        assertNull(f.vm.discardPrompt.value)

        f.vm.requestDiscard(listOf(unstaged, untracked))
        assertEquals("Discard 2 files?", f.vm.discardPrompt.value?.title)
        f.vm.dismissDiscard()
        runCurrent()
        assertNull(f.vm.discardPrompt.value)
        assertEquals(emptyList<Pair<List<String>, String>>(), calls)

        f.vm.requestDiscard(listOf(unstaged, untracked))
        f.vm.confirmDiscard()
        runCurrent()

        assertNull(f.vm.discardPrompt.value)
        assertEquals(
            listOf(listOf("src/both.py") to "unstaged", listOf("notes/тест.md") to "untracked"),
            calls,
        )
        assertEquals(1, pulls.size)
        assertFalse(ready(f.vm).busy)
    }

    @Test
    fun discardSelection_promptsOnlyForTheDiscardableSubset() = runTest {
        val f = loaded()
        f.vm.toggleSelection("staged:1:src/new name.py")
        f.vm.toggleSelection("unstaged:1:src/both.py")
        runCurrent()

        f.vm.discardSelection()

        assertEquals(listOf(unstaged), f.vm.discardPrompt.value?.entries)
        assertEquals("Discard changes?", f.vm.discardPrompt.value?.title)
    }

    // MARK: - Commit

    private val author = GitCommitAuthor("Ada Lovelace", "ada@example.com")
    private val head = GitCommitHead(
        "4890423520f4dcd158b85374f44fc8819a813ac2", "4890423", "Fix the login redirect", "Why.",
    )

    @Test
    fun openCommit_showsTheSheetLoading_thenWithTheContext_andCloseDropsIt() = runTest {
        val f = loaded()
        val reply = CompletableDeferred<GitCommitContextResponse>()
        coEvery { f.repo.sessionCommitContext("s1") } coAnswers { reply.await() }

        f.vm.openCommit()
        runCurrent()
        assertEquals(CommitSheetState(context = CommitContextState.Loading), f.vm.commitSheet.value)

        reply.complete(GitCommitContextResponse(author = author, head = head))
        runCurrent()
        assertEquals(CommitContextState.Ready(author, head), f.vm.commitSheet.value?.context)

        f.vm.closeCommit()
        assertNull(f.vm.commitSheet.value)
    }

    @Test
    fun aFailedContextLoad_isReportedInsideTheSheet() = runTest {
        val f = loaded()
        coEvery { f.repo.sessionCommitContext("s1") } throws
            SessionGitException.Network(IOException("down"))

        f.vm.openCommit()
        runCurrent()

        assertEquals(
            CommitContextState.Failed("Could not reach the agent. Check the connection and try again."),
            f.vm.commitSheet.value?.context,
        )
    }

    @Test
    fun commit_composesTheMessage_closesTheSheet_announcesTheHead_andPulls() = runTest {
        val f = loaded()
        val pulls = countPulls(f)
        coEvery { f.repo.sessionCommitContext("s1") } returns
            GitCommitContextResponse(author = author, head = head)
        val message = slot<String>()
        val amend = slot<Boolean>()
        val noVerify = slot<Boolean>()
        coEvery {
            f.repo.commitSession("s1", capture(message), capture(amend), capture(noVerify))
        } returns GitCommitResponse("abc1234def5678", "abc1234", "Add a")
        f.vm.openCommit()
        runCurrent()

        f.vm.events.test {
            f.vm.commit("  Add a ", "Why.\n", amend = false, noVerify = true)
            runCurrent()

            assertEquals(GitChangesEvent.ShowMessage("Committed abc1234 · Add a"), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertEquals("Add a\n\nWhy.", message.captured)
        assertFalse(amend.captured)
        assertTrue(noVerify.captured)
        assertNull(f.vm.commitSheet.value)
        assertEquals(1, pulls.size)
        assertFalse(ready(f.vm).busy)
    }

    @Test
    fun aBlankMessage_isNeverSent() = runTest {
        val f = loaded()
        coEvery { f.repo.sessionCommitContext("s1") } returns
            GitCommitContextResponse(author = author, head = head)
        val sent = mutableListOf<String>()
        coEvery { f.repo.commitSession("s1", capture(sent), any(), any()) } returns
            GitCommitResponse("x", "x", "x")
        f.vm.openCommit()
        runCurrent()

        f.vm.commit("   ", "\n", amend = false, noVerify = false)
        runCurrent()

        assertEquals(emptyList<String>(), sent)
        assertNotNull(f.vm.commitSheet.value)
    }

    @Test
    fun aHookRefusal_staysInTheSheet_withTheHookOutput_andCloseIsBlockedWhileCommitting() = runTest {
        val f = loaded()
        val pulls = countPulls(f)
        coEvery { f.repo.sessionCommitContext("s1") } returns
            GitCommitContextResponse(author = author, head = head)
        val reply = CompletableDeferred<GitCommitResponse>()
        coEvery { f.repo.commitSession("s1", any(), any(), any()) } coAnswers { reply.await() }
        f.vm.openCommit()
        runCurrent()

        f.vm.commit("hooked", "", amend = false, noVerify = false)
        runCurrent()
        assertTrue(f.vm.commitSheet.value?.committing == true)
        assertTrue(ready(f.vm).busy)
        f.vm.closeCommit()
        assertNotNull(f.vm.commitSheet.value)

        reply.completeExceptionally(gitError(SessionGitException.CODE_HOOK_FAILED, "lint: nope"))
        runCurrent()

        val sheet = f.vm.commitSheet.value
        assertEquals(false, sheet?.committing)
        assertEquals(CommitError("A commit hook rejected the commit.", "lint: nope"), sheet?.error)
        assertEquals(0, pulls.size)
        assertFalse(ready(f.vm).busy)
    }

    @Test
    fun aNonHookRefusal_carriesNoOutput() = runTest {
        val f = loaded()
        coEvery { f.repo.sessionCommitContext("s1") } returns
            GitCommitContextResponse(author = author, head = head)
        coEvery { f.repo.commitSession("s1", any(), any(), any()) } throws
            gitError(SessionGitException.CODE_IDENTITY_REQUIRED, "no identity")
        f.vm.openCommit()
        runCurrent()

        f.vm.commit("who", "", amend = false, noVerify = false)
        runCurrent()

        assertEquals(
            CommitError("Git doesn't know who you are on the agent machine.", null),
            f.vm.commitSheet.value?.error,
        )
    }

    @Test
    fun theCommitRouteArg_opensTheSheetOnce_andIsConsumed() = runTest {
        val f = newFixture(capabilities = allCaps, commit = true)

        assertNotNull(f.vm.commitSheet.value)
        assertEquals(false, f.handle.get<Boolean>("commit"))

        val plain = newFixture(capabilities = allCaps)
        assertNull(plain.vm.commitSheet.value)
    }

    @Test
    fun ready_carriesTheStagedCount_theCommitButtonGatesOn() = runTest {
        val f = loaded()

        f.loads.emit(
            SessionGitStatusLoad.Loaded(
                status().copy(counts = GitStatusCounts(staged = 2, unstaged = 1, total = 3)),
            ),
        )
        runCurrent()

        assertEquals(2, ready(f.vm).stagedCount)
    }
}

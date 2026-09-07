package com.mobaide.app.ui.terminal.git

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.data.repository.SessionGitStatusLoad
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
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
        val loads: MutableSharedFlow<SessionGitStatusLoad>,
        val refreshRequests: Flow<Unit>,
        val tracker: ForegroundSessionTracker,
        val projectName: () -> String?,
        val initial: () -> GitStatusResponse?,
    )

    private fun newFixture(projectName: String? = "alpha"): Fixture {
        val repo = mockk<GitRepository>(relaxed = true)
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
        val vm = GitChangesViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "s1")),
            gitRepository = repo,
            sessionRepository = sessionRepository,
            appForegroundTracker = AppForegroundTracker(),
            foregroundSessionTracker = tracker,
        )
        return Fixture(
            vm = vm,
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
}

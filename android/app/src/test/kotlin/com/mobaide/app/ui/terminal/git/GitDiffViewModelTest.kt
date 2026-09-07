package com.mobaide.app.ui.terminal.git

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitDiffHunk
import com.mobaide.app.data.network.dto.GitDiffLine
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [GitDiffViewModel]: route args → fetch, the context chips,
 * and the rule that a failed reload keeps the chosen context.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitDiffViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val routeArgs: Map<String, Any?> =
        mapOf("sessionId" to "s1", "path" to "src/a.py", "scope" to "unstaged")

    private fun diff(vararg texts: String): GitDiffResponse = GitDiffResponse(
        path = "src/a.py",
        scope = GitDiffResponse.SCOPE_UNSTAGED,
        kind = GitDiffResponse.KIND_TEXT,
        change = GitDiffResponse.CHANGE_MODIFIED,
        hunks = listOf(
            GitDiffHunk(
                old_start = 1, old_lines = texts.size, new_start = 1, new_lines = texts.size,
                lines = texts.mapIndexed { i, text ->
                    GitDiffLine(t = GitDiffLine.T_CTX, old = i + 1, new = i + 1, text = text)
                },
            ),
        ),
    )

    private fun newViewModel(
        repo: GitRepository,
        args: Map<String, Any?> = routeArgs,
        tracker: ForegroundSessionTracker = ForegroundSessionTracker(),
    ): GitDiffViewModel = GitDiffViewModel(SavedStateHandle(args), repo, tracker)

    private fun TestScope.subscribe(vm: GitDiffViewModel) {
        backgroundScope.launch { vm.uiState.collect { } }
        runCurrent()
    }

    private fun ready(vm: GitDiffViewModel): GitDiffUiState.Ready =
        vm.uiState.value as GitDiffUiState.Ready

    @Test
    fun loadsTheRouteArgs_atTheDefaultContext() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        val response = diff("def foo():", "    return x")
        coEvery { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 3, null) } returns response
        val vm = newViewModel(repo)
        assertEquals(GitDiffUiState.Loading(DIFF_CONTEXT_DEFAULT), vm.uiState.value)

        subscribe(vm)

        val ready = ready(vm)
        assertSame(response, ready.diff)
        assertEquals(3, ready.context)
        assertFalse(ready.refreshing)
        assertEquals(buildDiffRows(response), ready.rows)
        assertEquals(2, ready.gutterDigits)
        assertEquals(listOf("    return x", "def foo():"), ready.widthCandidates)
    }

    @Test
    fun theOriginalPath_isForwardedForRenames() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionGitDiff("s1", "src/new.py", "staged", 3, "src/old.py") } returns diff("x")
        val vm = newViewModel(
            repo,
            mapOf(
                "sessionId" to "s1", "path" to "src/new.py",
                "scope" to "staged", "origPath" to "src/old.py",
            ),
        )

        subscribe(vm)

        assertTrue(vm.uiState.value is GitDiffUiState.Ready)
        coVerify(exactly = 1) { repo.sessionGitDiff("s1", "src/new.py", "staged", 3, "src/old.py") }
    }

    @Test
    fun setContext_refetches_andKeepsTheOldRowsWhileRefreshing() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        val narrow = diff("narrow")
        val wide = diff("wide", "wider", "widest")
        coEvery { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 3, null) } returns narrow
        val reply = CompletableDeferred<GitDiffResponse>()
        coEvery { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 10, null) } coAnswers { reply.await() }
        val vm = newViewModel(repo)
        subscribe(vm)
        val before = ready(vm)

        vm.setContext(10)
        runCurrent()

        val refreshing = ready(vm)
        assertEquals(10, refreshing.context)
        assertTrue(refreshing.refreshing)
        assertEquals(before.rows, refreshing.rows)

        reply.complete(wide)
        runCurrent()

        val after = ready(vm)
        assertSame(wide, after.diff)
        assertEquals(10, after.context)
        assertFalse(after.refreshing)
        assertEquals(buildDiffRows(wide), after.rows)
    }

    @Test
    fun aFailureAfterAContextChange_keepsTheChosenContext() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 3, null) } returns diff("x")
        coEvery { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 25, null) } throws
            SessionGitException.GitError(
                SessionGitException.CODE_GIT_TIMEOUT, null, ApiException(504, ""),
            )
        val vm = newViewModel(repo)
        subscribe(vm)

        vm.setContext(25)
        runCurrent()

        assertEquals(
            GitDiffUiState.Error("git took too long to answer. Try again.", context = 25),
            vm.uiState.value,
        )
    }

    @Test
    fun agentOutdated_andSessionGone_haveTheirOwnStates() = runTest {
        val outdated = mockk<GitRepository>(relaxed = true)
        coEvery { outdated.sessionGitDiff(any(), any(), any(), any(), any()) } throws
            SessionGitException.AgentOutdated(ApiException(404, """{"detail":"Not Found"}"""))
        val outdatedVm = newViewModel(outdated)
        subscribe(outdatedVm)
        assertEquals(GitDiffUiState.AgentOutdated(3), outdatedVm.uiState.value)

        val gone = mockk<GitRepository>(relaxed = true)
        coEvery { gone.sessionGitDiff(any(), any(), any(), any(), any()) } throws
            SessionGitException.SessionGone(ApiException(404, """{"detail":"Session 's1' not found"}"""))
        val goneVm = newViewModel(gone)
        subscribe(goneVm)
        assertEquals(GitDiffUiState.SessionGone(3), goneVm.uiState.value)
    }

    @Test
    fun aRouteWithoutAPath_isAnError_andNeverFetches() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        val vm = newViewModel(repo, mapOf("sessionId" to "s1", "scope" to "unstaged"))

        subscribe(vm)

        assertEquals(GitDiffUiState.Error("Missing file path", context = 3), vm.uiState.value)
        coVerify(exactly = 0) { repo.sessionGitDiff(any(), any(), any(), any(), any()) }
    }

    @Test
    fun refresh_refetchesAtTheSameContext() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 3, null) } returns diff("x")
        val vm = newViewModel(repo)
        subscribe(vm)

        vm.refresh()
        runCurrent()

        assertTrue(vm.uiState.value is GitDiffUiState.Ready)
        coVerify(exactly = 2) { repo.sessionGitDiff("s1", "src/a.py", "unstaged", 3, null) }
    }

    @Test
    fun holdAndRelease_bracketTheTrackerWatch() = runTest {
        val tracker = ForegroundSessionTracker()
        val vm = newViewModel(mockk(relaxed = true), tracker = tracker)

        vm.holdWatch()
        assertTrue(tracker.isHeld("s1"))
        vm.releaseWatch()
        assertFalse(tracker.isHeld("s1"))
    }
}

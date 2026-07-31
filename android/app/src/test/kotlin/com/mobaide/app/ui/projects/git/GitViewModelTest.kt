package com.mobaide.app.ui.projects.git

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitDeleteResultItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.repository.GitBusyException
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [GitViewModel]. [MainDispatcherRule] installs a
 * [kotlinx.coroutines.test.StandardTestDispatcher] so `viewModelScope`
 * advances only on explicit `advanceUntilIdle()` — which also fast-forwards
 * the 5s undo window, so tests that need the pre-commit state step time
 * forward explicitly instead.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val mainId = "${GitItem.KIND_BRANCH_ONLY}:main"
    private val staleId = "${GitItem.KIND_BRANCH_ONLY}:stale"
    private val topicId = "${GitItem.KIND_BRANCH_WITH_WORKTREE}:topic"
    private val orphanId = "${GitItem.KIND_ORPHAN_WORKTREE}:/repo/wt/orphan"

    private fun branchOnly(name: String, isCurrent: Boolean = false): GitItem =
        GitItem(
            kind = GitItem.KIND_BRANCH_ONLY,
            branch = name,
            is_current = isCurrent,
        )

    private fun branchWithWorktree(
        name: String,
        path: String,
        isCurrent: Boolean = false,
    ): GitItem = GitItem(
        kind = GitItem.KIND_BRANCH_WITH_WORKTREE,
        branch = name,
        worktree_path = path,
        is_current = isCurrent,
    )

    private fun orphanWorktree(path: String): GitItem = GitItem(
        kind = GitItem.KIND_ORPHAN_WORKTREE,
        worktree_path = path,
    )

    private fun okResult(kind: String, target: String) =
        GitDeleteResultItem(kind = kind, target = target, ok = true)

    private fun failedResult(kind: String, target: String, error: String) =
        GitDeleteResultItem(kind = kind, target = target, ok = false, error = error)

    private class Fixture(
        val vm: GitViewModel,
        val repo: GitRepository,
        val changed: MutableSharedFlow<String>,
    )

    private fun newFixture(
        projectName: String = "alpha",
        initialItems: List<GitItem> = listOf(
            branchOnly("main", isCurrent = true),
            branchOnly("stale"),
            branchWithWorktree("topic", "/repo/wt/topic"),
            orphanWorktree("/repo/wt/orphan"),
        ),
    ): Fixture {
        val repo = mockk<GitRepository>(relaxed = true)
        val changed = MutableSharedFlow<String>(replay = 0, extraBufferCapacity = 8)
        every { repo.changed } returns changed
        coEvery { repo.listGitItems(projectName) } returns initialItems
        val handle = SavedStateHandle(mapOf("projectName" to projectName))
        val vm = GitViewModel(repo, handle)
        return Fixture(vm, repo, changed)
    }

    @Test
    fun init_loadsItems_andMarksLoadedFalse() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        assertEquals(4, f.vm.uiState.value.items.size)
        assertFalse(f.vm.uiState.value.isLoading)
        coVerify(exactly = 1) { f.repo.listGitItems("alpha") }
    }

    @Test
    fun init_loadFailure_setsErrorMessage_andClearsIsLoading() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        every { repo.changed } returns MutableSharedFlow()
        coEvery { repo.listGitItems("alpha") } throws RuntimeException("net down")
        val vm = GitViewModel(repo, SavedStateHandle(mapOf("projectName" to "alpha")))
        advanceUntilIdle()
        assertFalse(vm.uiState.value.isLoading)
        assertEquals("net down", vm.uiState.value.errorMessage)
        assertTrue(vm.uiState.value.items.isEmpty())
    }

    // MARK: - Selection

    @Test
    fun enterSelection_onDeletableItem_entersSelectionMode_andSelects() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection(staleId)
        val state = f.vm.uiState.value
        assertTrue(state.inSelectionMode)
        assertTrue(staleId in state.selected)
    }

    @Test
    fun enterSelection_onCurrentBranch_isNoOp() = runTest {
        // The current branch is non-deletable; long-press must be ignored,
        // otherwise users could enter selection mode with a row they cannot delete.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection(mainId)
        val state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun enterSelectionMode_fromTopBar_entersWithNothingSelected() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelectionMode()
        val state = f.vm.uiState.value
        assertTrue(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun enterSelectionMode_withNothingDeletable_isNoOp() = runTest {
        val f = newFixture(initialItems = listOf(branchOnly("main", isCurrent = true)))
        advanceUntilIdle()
        assertFalse(f.vm.uiState.value.hasDeletableItems)
        f.vm.enterSelectionMode()
        assertFalse(f.vm.uiState.value.inSelectionMode)
    }

    @Test
    fun toggle_addsThenRemoves_andStaysInSelectionMode() = runTest {
        // Selection mode is now also entered from the top bar with an empty
        // selection, so unchecking the last row must not eject the user.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection(staleId)
        f.vm.toggle(staleId)
        val state = f.vm.uiState.value
        assertTrue(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun toggle_currentBranch_isNoOp() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection(staleId)
        f.vm.toggle(mainId)
        assertEquals(setOf(staleId), f.vm.uiState.value.selected)
    }

    @Test
    fun exitSelection_clearsSelectedAndMode() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection(staleId)
        f.vm.exitSelection()
        val state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    // MARK: - Delete + undo window

    @Test
    fun requestDelete_hidesRows_opensUndoWindow_andSendsOnlyAfterIt() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val payload = slot<List<GitDeleteItem>>()
        coEvery { f.repo.deleteGitItems(eq("alpha"), capture(payload)) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_BRANCH_ONLY, "stale")),
        )
        f.vm.enterSelection(staleId)
        f.vm.requestDelete()

        var state = f.vm.uiState.value
        assertFalse("selection mode ends immediately", state.inSelectionMode)
        assertEquals(setOf(staleId), state.pendingDeleteIds)
        assertTrue("the queued row hides at once", state.visibleItems.none { it.id == staleId })
        assertEquals(1, state.undoPrompt?.count)

        advanceTimeBy(4_000)
        runCurrent()
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }

        advanceUntilIdle()
        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", any()) }
        assertEquals(1, payload.captured.size)
        assertEquals("stale", payload.captured[0].branch)
        assertFalse("force must default to false", payload.captured[0].force)
        state = f.vm.uiState.value
        assertNull("the snackbar closes once the window elapses", state.undoPrompt)
        assertFalse(state.deleting)
    }

    @Test
    fun requestDelete_skipsWhenNothingSelected() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.requestDelete()
        advanceUntilIdle()
        assertNull(f.vm.uiState.value.undoPrompt)
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun requestDelete_buildsItemsForBranchWithWorktree_andOrphanWorktree() = runTest {
        // The wire payload must echo `kind`, `branch`, and `worktree_path` so the
        // server receives enough info to dispatch each row correctly.
        val f = newFixture()
        advanceUntilIdle()
        val payload = slot<List<GitDeleteItem>>()
        coEvery { f.repo.deleteGitItems(eq("alpha"), capture(payload)) } returns GitDeleteResponse(
            results = listOf(
                okResult(GitItem.KIND_BRANCH_WITH_WORKTREE, "topic"),
                okResult(GitItem.KIND_ORPHAN_WORKTREE, "/repo/wt/orphan"),
            ),
        )
        f.vm.enterSelection(topicId)
        f.vm.toggle(orphanId)
        f.vm.requestDelete()
        advanceUntilIdle()

        val byKind = payload.captured.associateBy { it.kind }
        assertEquals("topic", byKind[GitItem.KIND_BRANCH_WITH_WORKTREE]?.branch)
        assertEquals("/repo/wt/topic", byKind[GitItem.KIND_BRANCH_WITH_WORKTREE]?.worktree_path)
        assertEquals("/repo/wt/orphan", byKind[GitItem.KIND_ORPHAN_WORKTREE]?.worktree_path)
    }

    @Test
    fun deleteItem_swipe_hidesTheRowAndSendsAfterTheWindow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_BRANCH_ONLY, "stale")),
        )
        f.vm.deleteItem(staleId)
        assertEquals(setOf(staleId), f.vm.uiState.value.pendingDeleteIds)
        advanceUntilIdle()
        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", any()) }
    }

    @Test
    fun deleteItem_currentBranch_isNoOp() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.deleteItem(mainId)
        advanceUntilIdle()
        assertNull(f.vm.uiState.value.undoPrompt)
        assertTrue(f.vm.uiState.value.pendingDeleteIds.isEmpty())
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun secondDeleteInsideTheWindow_isCoalescedIntoOneRequest() = runTest {
        // Restarting the window must extend the batch, never discard the first
        // item — the failure mode a per-row swipe makes trivially reachable.
        val f = newFixture()
        advanceUntilIdle()
        val payload = slot<List<GitDeleteItem>>()
        coEvery { f.repo.deleteGitItems(eq("alpha"), capture(payload)) } returns GitDeleteResponse(
            results = listOf(
                okResult(GitItem.KIND_BRANCH_ONLY, "stale"),
                okResult(GitItem.KIND_BRANCH_WITH_WORKTREE, "topic"),
            ),
        )
        f.vm.deleteItem(staleId)
        advanceTimeBy(2_000)
        runCurrent()
        f.vm.deleteItem(topicId)

        val state = f.vm.uiState.value
        assertEquals(2, state.undoPrompt?.count)
        assertEquals(setOf(staleId, topicId), state.pendingDeleteIds)

        advanceUntilIdle()
        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", any()) }
        assertEquals(listOf("stale", "topic"), payload.captured.map { it.branch })
    }

    @Test
    fun undo_cancelsThePendingDelete_andRestoresEveryRow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.deleteItem(staleId)
        advanceTimeBy(2_000)
        runCurrent()
        f.vm.deleteItem(topicId)

        f.vm.undo()
        val state = f.vm.uiState.value
        assertNull(state.undoPrompt)
        assertTrue(state.pendingDeleteIds.isEmpty())

        advanceUntilIdle()
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun commitNow_sendsWithoutWaitingOutTheWindow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_BRANCH_ONLY, "stale")),
        )
        f.vm.deleteItem(staleId)
        f.vm.commitNow()
        runCurrent()
        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", any()) }
        assertNull(f.vm.uiState.value.undoPrompt)
    }

    // MARK: - Result handling

    @Test
    fun forceRequired_populatesPrompt_andRestoresTheRowUnderIt() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "stale", "force_required")),
        )
        f.vm.deleteItem(staleId)
        advanceUntilIdle()

        val state = f.vm.uiState.value
        val prompt = state.forceDeletePrompt
        assertNotNull("force_required result must populate forceDeletePrompt", prompt)
        assertEquals(listOf("stale"), prompt!!.refs)
        // Items in the prompt are the original (force=false) request — the user
        // must opt in by hitting Confirm.
        assertFalse(prompt.items[0].item.force)
        assertTrue(
            "nothing was deleted, so the row is visible under the prompt",
            state.visibleItems.any { it.id == staleId },
        )
    }

    @Test
    fun confirmForceDelete_reissuesWithForceTrue_andRehidesTheRow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "stale", "force_required")),
        )
        f.vm.deleteItem(staleId)
        advanceUntilIdle()

        val forced = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale", force = true))
        coEvery { f.repo.deleteGitItems("alpha", forced) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_BRANCH_ONLY, "stale")),
        )
        f.vm.confirmForceDelete()
        assertEquals(setOf(staleId), f.vm.uiState.value.pendingDeleteIds)
        advanceUntilIdle()

        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", forced) }
        assertNull(
            "forceDeletePrompt must clear after the user confirmed",
            f.vm.uiState.value.forceDeletePrompt,
        )
    }

    @Test
    fun dirtyWorktree_forceRequired_promptsWithThePath_thenReissuesWithForce() = runTest {
        // A dirty worktree comes back as force_required just like an unmerged
        // branch; the prompt must surface the worktree path (no branch ref).
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(
                failedResult(GitItem.KIND_ORPHAN_WORKTREE, "/repo/wt/orphan", "force_required"),
            ),
        )
        f.vm.deleteItem(orphanId)
        advanceUntilIdle()

        val prompt = f.vm.uiState.value.forceDeletePrompt
        assertNotNull("dirty worktree force_required must populate the prompt", prompt)
        assertEquals(listOf("/repo/wt/orphan"), prompt!!.refs)

        val forced = listOf(
            GitDeleteItem(
                kind = GitItem.KIND_ORPHAN_WORKTREE,
                worktree_path = "/repo/wt/orphan",
                force = true,
            ),
        )
        coEvery { f.repo.deleteGitItems("alpha", forced) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_ORPHAN_WORKTREE, "/repo/wt/orphan")),
        )
        f.vm.confirmForceDelete()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", forced) }
        assertNull(f.vm.uiState.value.forceDeletePrompt)
    }

    @Test
    fun partialFailure_restoresOnlyTheFailedRow_andNamesItInTheMessage() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(
                okResult(GitItem.KIND_BRANCH_ONLY, "stale"),
                failedResult(GitItem.KIND_BRANCH_WITH_WORKTREE, "topic", "locked"),
            ),
        )
        f.vm.events.test {
            f.vm.deleteItem(staleId)
            advanceTimeBy(2_000)
            runCurrent()
            f.vm.deleteItem(topicId)
            advanceUntilIdle()

            val event = awaitItem()
            assertTrue(event is GitUiEvent.Message)
            assertEquals("Couldn't delete topic: the worktree is locked", (event as GitUiEvent.Message).text)
            cancelAndIgnoreRemainingEvents()
        }
        assertEquals(setOf(staleId), f.vm.uiState.value.pendingDeleteIds)
    }

    @Test
    fun reloadDuringAnInFlightDelete_doesNotCancelResponseHandling() = runTest {
        // The agent publishes `git.changed` before it replies, so a reload can
        // land while the delete is still awaiting its response. Cancelling that
        // request would drop the force prompt and strand the failed row hidden.
        val f = newFixture()
        advanceUntilIdle()
        val reply = CompletableDeferred<GitDeleteResponse>()
        coEvery { f.repo.deleteGitItems("alpha", any()) } coAnswers { reply.await() }

        f.vm.deleteItem(staleId)
        advanceUntilIdle()
        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", any()) }

        f.changed.emit("alpha")
        advanceUntilIdle()

        reply.complete(
            GitDeleteResponse(
                results = listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "stale", "force_required")),
            ),
        )
        advanceUntilIdle()

        val state = f.vm.uiState.value
        assertNotNull("the force prompt must survive the concurrent reload", state.forceDeletePrompt)
        assertTrue("the un-deleted row must come back", state.visibleItems.any { it.id == staleId })
    }

    @Test
    fun busyException_setsBusyDialog_andRestoresEveryRow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val holders = listOf(
            BusyHolder(kind = "session", session_id = "s-1", cwd = "/repo/wt/topic"),
        )
        coEvery { f.repo.deleteGitItems("alpha", any()) } throws GitBusyException(holders)

        f.vm.deleteItem(topicId)
        advanceUntilIdle()
        val state = f.vm.uiState.value
        assertEquals(holders, state.busyDialog)
        assertFalse(state.deleting)
        assertTrue(
            "409 rejects the whole batch, so every row comes back",
            state.pendingDeleteIds.isEmpty(),
        )
    }

    @Test
    fun unknownException_emitsMessageEvent_andRestoresRows() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } throws RuntimeException("oops")

        f.vm.events.test {
            f.vm.deleteItem(staleId)
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is GitUiEvent.Message)
            assertTrue((event as GitUiEvent.Message).text.contains("oops"))
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(f.vm.uiState.value.deleting)
        assertTrue(f.vm.uiState.value.pendingDeleteIds.isEmpty())
    }

    @Test
    fun dismissBusyDialog_clearsBusyState() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } throws
            GitBusyException(listOf(BusyHolder(kind = "auto_scan", project_name = "alpha")))

        f.vm.deleteItem(orphanId)
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.busyDialog)
        f.vm.dismissBusyDialog()
        assertNull(f.vm.uiState.value.busyDialog)
    }

    @Test
    fun dismissForceDeletePrompt_clearsPromptState() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "stale", "force_required")),
        )
        f.vm.deleteItem(staleId)
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.forceDeletePrompt)
        f.vm.dismissForceDeletePrompt()
        assertNull(f.vm.uiState.value.forceDeletePrompt)
    }

    // MARK: - Reload

    @Test
    fun gitChangedForOurProject_triggersReload() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        // After init we expect 1 listGitItems call. Trigger a change for our
        // project name → should re-fetch.
        val updatedItems = listOf(branchOnly("main", isCurrent = true))
        coEvery { f.repo.listGitItems("alpha") } returns updatedItems

        f.changed.emit("alpha")
        advanceUntilIdle()

        assertEquals(1, f.vm.uiState.value.items.size)
        // Total: initial + reload = 2 calls.
        coVerify(exactly = 2) { f.repo.listGitItems("alpha") }
    }

    @Test
    fun gitChangedForOtherProject_doesNotReload() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.changed.emit("beta")
        advanceUntilIdle()
        // No additional fetch — initial only.
        coVerify(exactly = 1) { f.repo.listGitItems("alpha") }
    }

    @Test
    fun reload_intersectsPriorSelectionWithCurrentItems() = runTest {
        // If a selected item disappears after a remote refresh, it must be
        // dropped from `selected` so the user can't accidentally request a
        // delete on a now-stale id.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection(staleId)
        assertTrue(staleId in f.vm.uiState.value.selected)

        // Server emits git.changed; reload returns a list without 'stale'.
        coEvery { f.repo.listGitItems("alpha") } returns listOf(
            branchOnly("main", isCurrent = true),
        )
        f.changed.emit("alpha")
        advanceUntilIdle()

        assertFalse(staleId in f.vm.uiState.value.selected)
    }

    @Test
    fun reload_dropsHiddenIdsTheServerNoLongerLists() = runTest {
        // The optimistic hide must self-heal: once the delete lands and the row
        // is gone from the server list, the id can't stay stuck in the hide set
        // and re-hide a branch that gets re-created later.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", any()) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_BRANCH_ONLY, "stale")),
        )
        f.vm.deleteItem(staleId)
        advanceUntilIdle()
        assertEquals(setOf(staleId), f.vm.uiState.value.pendingDeleteIds)

        coEvery { f.repo.listGitItems("alpha") } returns listOf(branchOnly("main", isCurrent = true))
        f.changed.emit("alpha")
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.pendingDeleteIds.isEmpty())

        coEvery { f.repo.listGitItems("alpha") } returns listOf(
            branchOnly("main", isCurrent = true),
            branchOnly("stale"),
        )
        f.vm.refresh()
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.visibleItems.any { it.id == staleId })
    }

    @Test
    fun reload_dropsQueuedRowsDeletedElsewhere_andClosesTheUndoWindow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.deleteItem(staleId)
        assertNotNull(f.vm.uiState.value.undoPrompt)

        coEvery { f.repo.listGitItems("alpha") } returns listOf(branchOnly("main", isCurrent = true))
        f.changed.emit("alpha")
        advanceUntilIdle()

        assertNull(f.vm.uiState.value.undoPrompt)
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun refresh_re_fetchesViaRepository() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.refresh()
        advanceUntilIdle()
        coVerify(exactly = 2) { f.repo.listGitItems("alpha") }
    }

    // MARK: - Failure copy

    @Test
    fun deleteFailureMessage_namesTargetAndReason() {
        assertEquals(
            "Couldn't delete wt: the worktree is locked",
            deleteFailureMessage(listOf(failedResult(GitItem.KIND_ORPHAN_WORKTREE, "wt", "locked"))),
        )
        assertEquals(
            "Couldn't delete b: it no longer exists",
            deleteFailureMessage(listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "b", "not_found"))),
        )
        assertEquals(
            "Couldn't delete b: the request was invalid",
            deleteFailureMessage(listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "b", "invalid"))),
        )
        assertEquals(
            "Couldn't delete b: unknown error",
            deleteFailureMessage(listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "b", "error"))),
        )
    }

    @Test
    fun deleteFailureMessage_prefersTheFirstLineOfErrorDetail() {
        val failure = GitDeleteResultItem(
            kind = GitItem.KIND_BRANCH_ONLY,
            target = "b",
            ok = false,
            error = "error",
            error_detail = "  fatal: branch 'b' is checked out at '/wt'  \nhint: use --force",
        )
        assertEquals(
            "Couldn't delete b: fatal: branch 'b' is checked out at '/wt'",
            deleteFailureMessage(listOf(failure)),
        )
    }

    @Test
    fun deleteFailureMessage_truncatesLongDetail_andCountsMultipleFailures() {
        val failure = GitDeleteResultItem(
            kind = GitItem.KIND_BRANCH_ONLY,
            target = "b",
            ok = false,
            error = "error",
            error_detail = "x".repeat(200),
        )
        assertEquals("Couldn't delete b: " + "x".repeat(120), deleteFailureMessage(listOf(failure)))
        assertEquals(
            "2 item(s) failed to delete",
            deleteFailureMessage(
                listOf(
                    failedResult(GitItem.KIND_BRANCH_ONLY, "b1", "locked"),
                    failedResult(GitItem.KIND_BRANCH_ONLY, "b2", "error"),
                ),
            ),
        )
    }
}

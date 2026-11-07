package com.mobaide.app.ui.projects.git

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitBranchCreateRequest
import com.mobaide.app.data.network.dto.GitBranchResponse
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitDeleteResultItem
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.repository.GitBusyException
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
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
        capabilities: GitCapabilities = GitCapabilities.NONE,
    ): Fixture {
        val repo = mockk<GitRepository>(relaxed = true)
        val changed = MutableSharedFlow<String>(replay = 0, extraBufferCapacity = 8)
        every { repo.changed } returns changed
        coEvery { repo.listGitItems(projectName) } returns initialItems
        coEvery { repo.gitCapabilities() } returns capabilities
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
            "Couldn't delete b: git took too long to answer. Try again.",
            deleteFailureMessage(listOf(failedResult(GitItem.KIND_BRANCH_ONLY, "b", "git_timeout"))),
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

    // MARK: - Branches: capability, create, check out

    private val remoteId = "${GitItem.KIND_REMOTE_BRANCH}:origin/feature-x|"

    private fun remoteBranch(name: String): GitItem = GitItem(
        kind = GitItem.KIND_REMOTE_BRANCH,
        branch = "origin/$name",
        remote = "origin",
    )

    private fun gitError(code: String, message: String? = null) =
        SessionGitException.GitError(code, message, ApiException(409, ""))

    /** Local rows, a worktree-backed one and a remote-tracking one, with the capability on. */
    private fun branchesFixture(): Fixture = newFixture(
        initialItems = listOf(
            branchOnly("main", isCurrent = true),
            branchOnly("stale"),
            branchWithWorktree("topic", "/repo/wt/topic"),
            remoteBranch("feature-x"),
        ),
        capabilities = GitCapabilities(branches = true),
    )

    private val dirtyCopy =
        "The project folder has local changes this checkout would overwrite. " +
            "Commit or discard them from a session's Changes screen first."

    @Test
    fun init_probesTheCapability_besideTheSingleListLoad() = runTest {
        val f = newFixture(capabilities = GitCapabilities(branches = true))
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.branchesEnabled)
        coVerify(exactly = 1) { f.repo.listGitItems("alpha") }
        coVerify(exactly = 1) { f.repo.gitCapabilities() }
    }

    @Test
    fun init_aFailedProbe_keepsTheDeleteOnlyScreen_withTheListLoaded() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        every { repo.changed } returns MutableSharedFlow()
        coEvery { repo.listGitItems("alpha") } returns listOf(branchOnly("stale"))
        coEvery { repo.gitCapabilities() } throws RuntimeException("offline")
        val vm = GitViewModel(repo, SavedStateHandle(mapOf("projectName" to "alpha")))
        advanceUntilIdle()
        val state = vm.uiState.value
        assertFalse(state.branchesEnabled)
        assertEquals(1, state.items.size)
        assertNull(state.errorMessage)
    }

    @Test
    fun openNewBranch_isGatedOnTheCapability() = runTest {
        val without = newFixture()
        advanceUntilIdle()
        without.vm.openNewBranch()
        assertNull(without.vm.uiState.value.newBranch)

        val with = branchesFixture()
        advanceUntilIdle()
        with.vm.openNewBranch()
        assertEquals(NewBranchSheetState(), with.vm.uiState.value.newBranch)
    }

    @Test
    fun createBranch_sendsTheTrimmedForm_closesTheSheet_andReportsTheResult() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        coEvery {
            f.repo.createBranch("alpha", GitBranchCreateRequest("feature/new", "main", true))
        } returns GitBranchResponse(branch = "feature/new", checked_out = true, created = true)
        f.vm.openNewBranch()
        f.vm.events.test {
            f.vm.createBranch("  feature/new ", "main", true)
            assertEquals(NewBranchSheetState(creating = true), f.vm.uiState.value.newBranch)
            advanceUntilIdle()
            assertEquals(GitUiEvent.Message("Created and checked out feature/new"), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertNull(f.vm.uiState.value.newBranch)
    }

    @Test
    fun createBranch_ignoresABlankName_andAClosedSheet() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        f.vm.createBranch("feature/new", null, false)
        f.vm.openNewBranch()
        f.vm.createBranch("   ", null, false)
        advanceUntilIdle()
        coVerify(exactly = 0) { f.repo.createBranch(any(), any()) }
        assertEquals(NewBranchSheetState(), f.vm.uiState.value.newBranch)
    }

    @Test
    fun createBranch_exists_keepsTheSheetOpen_withTheRefusalInline() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        coEvery { f.repo.createBranch("alpha", any()) } throws gitError("exists", "feature/new")
        f.vm.openNewBranch()
        f.vm.createBranch("feature/new", null, false)
        advanceUntilIdle()
        assertEquals(
            NewBranchSheetState(error = "A branch named feature/new already exists."),
            f.vm.uiState.value.newBranch,
        )
        f.vm.dismissNewBranch()
        assertNull(f.vm.uiState.value.newBranch)
    }

    @Test
    fun createBranch_busy_closesTheSheet_andOffersAForcedRetry() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val holders = listOf(BusyHolder(kind = "session", session_id = "s-1", cwd = "/repo"))
        val request = GitBranchCreateRequest(name = "feature/new", checkout = true)
        coEvery { f.repo.createBranch("alpha", request) } throws GitBusyException(holders)
        coEvery { f.repo.createBranch("alpha", request.copy(force = true)) } returns
            GitBranchResponse("feature/new", checked_out = true, created = true)
        f.vm.openNewBranch()
        f.vm.createBranch("feature/new", null, true)
        advanceUntilIdle()
        assertNull(f.vm.uiState.value.newBranch)
        assertNull(f.vm.uiState.value.busyDialog)
        assertEquals(
            BusyCheckoutPrompt(holders, "feature/new", BusyCheckoutPrompt.Retry.Create(request)),
            f.vm.uiState.value.busyCheckoutPrompt,
        )

        f.vm.events.test {
            f.vm.confirmBusyCheckout()
            advanceUntilIdle()
            assertEquals(GitUiEvent.Message("Created and checked out feature/new"), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertNull(f.vm.uiState.value.busyCheckoutPrompt)
        coVerify(exactly = 1) { f.repo.createBranch("alpha", request.copy(force = true)) }
    }

    @Test
    fun dismissNewBranch_isRefusedWhileTheRequestIsInFlight() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val reply = CompletableDeferred<GitBranchResponse>()
        coEvery { f.repo.createBranch("alpha", any()) } coAnswers { reply.await() }
        f.vm.openNewBranch()
        f.vm.createBranch("feature/new", null, false)
        advanceUntilIdle()

        f.vm.dismissNewBranch()
        assertEquals(NewBranchSheetState(creating = true), f.vm.uiState.value.newBranch)

        reply.complete(GitBranchResponse("feature/new", checked_out = false, created = true))
        advanceUntilIdle()
        assertNull(f.vm.uiState.value.newBranch)
    }

    @Test
    fun requestCheckout_promptsOnlyForRowsNothingHasCheckedOut() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        f.vm.requestCheckout(mainId)
        assertNull("the current branch", f.vm.uiState.value.checkoutPrompt)
        f.vm.requestCheckout(topicId)
        assertNull("a worktree-backed branch", f.vm.uiState.value.checkoutPrompt)

        f.vm.requestCheckout(remoteId)
        assertEquals(remoteBranch("feature-x"), f.vm.uiState.value.checkoutPrompt)
        f.vm.dismissCheckoutPrompt()
        assertNull(f.vm.uiState.value.checkoutPrompt)

        f.vm.requestCheckout(staleId)
        assertEquals(branchOnly("stale"), f.vm.uiState.value.checkoutPrompt)
    }

    @Test
    fun requestCheckout_isANoOpWithoutTheCapability() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.requestCheckout(staleId)
        assertNull(f.vm.uiState.value.checkoutPrompt)
    }

    @Test
    fun confirmCheckout_sendsTheRowsRef_andReportsWhatTheAgentCheckedOut() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        coEvery { f.repo.checkout("alpha", "origin/feature-x") } returns
            GitBranchResponse(branch = "feature-x", checked_out = true, created = true)
        f.vm.requestCheckout(remoteId)
        f.vm.events.test {
            f.vm.confirmCheckout()
            val state = f.vm.uiState.value
            assertNull("the prompt closes as the request starts", state.checkoutPrompt)
            assertTrue(state.checkingOut)
            assertTrue(state.rowBusy)
            advanceUntilIdle()
            assertEquals(GitUiEvent.Message("Checked out feature-x"), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(f.vm.uiState.value.checkingOut)
        coVerify(exactly = 1) { f.repo.checkout("alpha", "origin/feature-x") }
    }

    @Test
    fun confirmCheckout_withoutAPrompt_sendsNothing() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        f.vm.confirmCheckout()
        advanceUntilIdle()
        coVerify(exactly = 0) { f.repo.checkout(any(), any()) }
    }

    @Test
    fun confirmCheckout_busy_offersAForcedRetry() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val holders = listOf(
            BusyHolder(kind = "auto_scan", project_name = "alpha", worktree_path = "/repo"),
        )
        coEvery { f.repo.checkout("alpha", "stale", false) } throws GitBusyException(holders)
        f.vm.requestCheckout(staleId)
        f.vm.confirmCheckout()
        advanceUntilIdle()
        assertNull(f.vm.uiState.value.busyDialog)
        assertEquals(
            BusyCheckoutPrompt(holders, "stale", BusyCheckoutPrompt.Retry.Checkout("stale")),
            f.vm.uiState.value.busyCheckoutPrompt,
        )
        assertFalse(f.vm.uiState.value.checkingOut)
    }

    @Test
    fun confirmBusyCheckout_retriesWithForce() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val holders = listOf(BusyHolder(kind = "session", session_id = "s-1", cwd = "/repo"))
        coEvery { f.repo.checkout("alpha", "stale", false) } throws GitBusyException(holders)
        coEvery { f.repo.checkout("alpha", "stale", true) } returns
            GitBranchResponse(branch = "stale", checked_out = true, created = false)
        f.vm.requestCheckout(staleId)
        f.vm.confirmCheckout()
        advanceUntilIdle()
        f.vm.events.test {
            f.vm.confirmBusyCheckout()
            assertNull(f.vm.uiState.value.busyCheckoutPrompt)
            assertTrue(f.vm.uiState.value.checkingOut)
            advanceUntilIdle()
            assertEquals(GitUiEvent.Message("Checked out stale"), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(f.vm.uiState.value.checkingOut)
        coVerify(exactly = 1) { f.repo.checkout("alpha", "stale", true) }
    }

    @Test
    fun confirmBusyCheckout_anAgentWithoutForce_asksForAnUpdate() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val holders = listOf(BusyHolder(kind = "session", session_id = "s-1", cwd = "/repo"))
        coEvery { f.repo.checkout("alpha", "stale", any()) } throws GitBusyException(holders)
        f.vm.requestCheckout(staleId)
        f.vm.confirmCheckout()
        advanceUntilIdle()
        f.vm.events.test {
            f.vm.confirmBusyCheckout()
            advanceUntilIdle()
            assertEquals(
                GitUiEvent.Message("The project folder is still in use. Update the agent to check out anyway."),
                awaitItem(),
            )
            cancelAndIgnoreRemainingEvents()
        }
        assertNull(f.vm.uiState.value.busyCheckoutPrompt)
    }

    @Test
    fun dismissBusyCheckoutPrompt_sendsNothing() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val holders = listOf(BusyHolder(kind = "session", session_id = "s-1", cwd = "/repo"))
        coEvery { f.repo.checkout("alpha", "stale", false) } throws GitBusyException(holders)
        f.vm.requestCheckout(staleId)
        f.vm.confirmCheckout()
        advanceUntilIdle()
        f.vm.dismissBusyCheckoutPrompt()
        f.vm.confirmBusyCheckout()
        advanceUntilIdle()
        assertNull(f.vm.uiState.value.busyCheckoutPrompt)
        coVerify(exactly = 0) { f.repo.checkout("alpha", "stale", true) }
    }

    @Test
    fun confirmCheckout_dirty_emitsTheDirtyCopy() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        coEvery { f.repo.checkout("alpha", "stale") } throws
            gitError("dirty", "error: Your local changes")
        f.vm.requestCheckout(staleId)
        f.vm.events.test {
            f.vm.confirmCheckout()
            advanceUntilIdle()
            assertEquals(GitUiEvent.Message(dirtyCopy), awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(f.vm.uiState.value.checkingOut)
    }

    @Test
    fun requestCheckout_waitsOutAnInFlightCheckout() = runTest {
        val f = branchesFixture()
        advanceUntilIdle()
        val reply = CompletableDeferred<GitBranchResponse>()
        coEvery { f.repo.checkout("alpha", "stale") } coAnswers { reply.await() }
        f.vm.requestCheckout(staleId)
        f.vm.confirmCheckout()
        advanceUntilIdle()

        f.vm.requestCheckout(remoteId)
        assertNull(f.vm.uiState.value.checkoutPrompt)

        reply.complete(GitBranchResponse("stale", checked_out = true, created = false))
        advanceUntilIdle()
        f.vm.requestCheckout(remoteId)
        assertNotNull(f.vm.uiState.value.checkoutPrompt)
    }

    // MARK: - Remote rows and filter

    @Test
    fun uiState_splitsRemoteRows_andOpensTheSectionOnlyWhenToggled() = runTest {
        val f = newFixture(
            initialItems = listOf(
                branchOnly("main", isCurrent = true),
                remoteBranch("feature-x"),
                remoteBranch("main"),
            ),
        )
        advanceUntilIdle()
        val state = f.vm.uiState.value
        assertEquals(listOf("main"), state.localItems.map { it.branch })
        assertEquals(listOf("origin/feature-x", "origin/main"), state.remoteItems.map { it.branch })
        assertFalse(state.remoteOpen)
        assertFalse(state.filterable)
        assertFalse("remote rows are never deletable", state.hasDeletableItems)

        f.vm.toggleRemote()
        assertTrue(f.vm.uiState.value.remoteOpen)
        f.vm.toggleRemote()
        assertFalse(f.vm.uiState.value.remoteOpen)
    }

    @Test
    fun filter_appearsAtEightRows_narrowsBothSections_andOpensTheRemoteOne() = runTest {
        val f = newFixture(
            initialItems = (1..6).map { branchOnly("feature-$it") } +
                listOf(remoteBranch("feature-1"), remoteBranch("main")),
        )
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.filterable)

        f.vm.setFilter(" FEATURE-1 ")
        val state = f.vm.uiState.value
        assertEquals(listOf("feature-1"), state.localItems.map { it.branch })
        assertEquals(listOf("origin/feature-1"), state.remoteItems.map { it.branch })
        assertTrue(state.remoteOpen)

        f.vm.setFilter("   ")
        assertEquals(6, f.vm.uiState.value.localItems.size)
        assertFalse(f.vm.uiState.value.remoteOpen)
    }

    @Test
    fun filter_isIgnoredBelowEightRows() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.setFilter("nothing-matches")
        assertFalse(f.vm.uiState.value.filterable)
        assertEquals(4, f.vm.uiState.value.localItems.size)
    }

    // MARK: - Remote delete, Remove worktree, folders

    /** [branchesFixture]'s rows with `delete_remote` advertised (or not). */
    private fun remoteDeleteFixture(deleteRemote: Boolean = true): Fixture = newFixture(
        initialItems = listOf(
            branchOnly("main", isCurrent = true),
            branchOnly("stale"),
            branchWithWorktree("topic", "/repo/wt/topic"),
            remoteBranch("feature-x"),
        ),
        capabilities = GitCapabilities(branches = true, deleteRemote = deleteRemote),
    )

    private val worktreeOnlyTopic =
        GitDeleteItem(kind = GitItem.KIND_ORPHAN_WORKTREE, worktree_path = "/repo/wt/topic")

    @Test
    fun deleteItem_remoteRow_confirmsBeforeTheUndoWindow_thenSendsTheRemoteQualifiedName() = runTest {
        val f = remoteDeleteFixture()
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.deleteRemoteEnabled)
        val payload = slot<List<GitDeleteItem>>()
        coEvery { f.repo.deleteGitItems(eq("alpha"), capture(payload)) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_REMOTE_BRANCH, "origin/feature-x")),
        )

        f.vm.deleteItem(remoteId)

        var state = f.vm.uiState.value
        assertEquals(listOf("origin/feature-x"), state.remoteDeletePrompt?.map { it.branch })
        assertNull("nothing is queued before the confirmation", state.undoPrompt)
        assertTrue(state.pendingDeleteIds.isEmpty())

        f.vm.confirmRemoteDelete()

        state = f.vm.uiState.value
        assertNull(state.remoteDeletePrompt)
        assertEquals(setOf(remoteId), state.pendingDeleteIds)
        assertEquals(1, state.undoPrompt?.count)
        advanceTimeBy(4_000)
        runCurrent()
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }

        advanceUntilIdle()
        assertEquals(
            listOf(GitDeleteItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/feature-x")),
            payload.captured,
        )
    }

    @Test
    fun dismissRemoteDeletePrompt_queuesNothing_andSendsNothing() = runTest {
        val f = remoteDeleteFixture()
        advanceUntilIdle()
        f.vm.deleteItem(remoteId)

        f.vm.dismissRemoteDeletePrompt()
        advanceUntilIdle()

        val state = f.vm.uiState.value
        assertNull(state.remoteDeletePrompt)
        assertNull(state.undoPrompt)
        assertTrue(state.visibleItems.any { it.id == remoteId })
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun requestDelete_mixedSelection_isConfirmedWhole_andSentInOneBatch() = runTest {
        val f = remoteDeleteFixture()
        advanceUntilIdle()
        val payload = slot<List<GitDeleteItem>>()
        coEvery { f.repo.deleteGitItems(eq("alpha"), capture(payload)) } returns GitDeleteResponse(
            results = listOf(
                okResult(GitItem.KIND_BRANCH_ONLY, "stale"),
                okResult(GitItem.KIND_REMOTE_BRANCH, "origin/feature-x"),
            ),
        )
        f.vm.enterSelection(staleId)
        f.vm.toggle(remoteId)

        f.vm.requestDelete()

        var state = f.vm.uiState.value
        assertEquals(listOf("stale", "origin/feature-x"), state.remoteDeletePrompt?.map { it.branch })
        assertTrue(state.pendingDeleteIds.isEmpty())
        assertTrue("the selection waits on the confirmation", state.inSelectionMode)
        assertEquals(setOf(staleId, remoteId), state.selected)

        f.vm.confirmRemoteDelete()

        state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
        assertEquals(setOf(staleId, remoteId), state.pendingDeleteIds)
        advanceUntilIdle()

        assertEquals(listOf("stale", "origin/feature-x"), payload.captured.map { it.branch })
    }

    @Test
    fun dismissRemoteDeletePrompt_afterABatchDelete_returnsToTheSelection() = runTest {
        val f = remoteDeleteFixture()
        advanceUntilIdle()
        f.vm.enterSelection(staleId)
        f.vm.toggle(remoteId)
        f.vm.requestDelete()

        f.vm.dismissRemoteDeletePrompt()
        advanceUntilIdle()

        val state = f.vm.uiState.value
        assertNull(state.remoteDeletePrompt)
        assertTrue(state.inSelectionMode)
        assertEquals(setOf(staleId, remoteId), state.selected)
        assertTrue(state.pendingDeleteIds.isEmpty())
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }

        f.vm.requestDelete()

        assertEquals(
            listOf("stale", "origin/feature-x"),
            f.vm.uiState.value.remoteDeletePrompt?.map { it.branch },
        )
    }

    @Test
    fun remoteRow_withoutTheCapability_canBeNeitherSelectedNorDeleted() = runTest {
        val f = remoteDeleteFixture(deleteRemote = false)
        advanceUntilIdle()
        assertFalse(f.vm.uiState.value.deleteRemoteEnabled)

        f.vm.enterSelection(remoteId)
        assertFalse(f.vm.uiState.value.inSelectionMode)
        f.vm.deleteItem(remoteId)
        advanceUntilIdle()

        val state = f.vm.uiState.value
        assertNull(state.remoteDeletePrompt)
        assertTrue(state.pendingDeleteIds.isEmpty())
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun removeWorktree_hidesTheRowForTheUndoWindow_thenSendsAWorktreeOnlyRemoval() = runTest {
        val f = remoteDeleteFixture()
        advanceUntilIdle()
        val payload = slot<List<GitDeleteItem>>()
        coEvery { f.repo.deleteGitItems(eq("alpha"), capture(payload)) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_ORPHAN_WORKTREE, "/repo/wt/topic")),
        )

        f.vm.removeWorktree(topicId)

        val state = f.vm.uiState.value
        assertNull("a worktree removal needs no remote confirmation", state.remoteDeletePrompt)
        assertEquals(setOf(topicId), state.pendingDeleteIds)
        assertEquals(1, state.undoPrompt?.count)

        advanceUntilIdle()
        assertEquals(listOf(worktreeOnlyTopic), payload.captured)
    }

    @Test
    fun removeWorktree_isANoOpOnRowsWithoutARemovableWorktree() = runTest {
        val f = newFixture(
            initialItems = listOf(
                branchWithWorktree("main", "/repo", isCurrent = true),
                branchOnly("stale"),
                orphanWorktree("/repo/wt/orphan"),
            ),
        )
        advanceUntilIdle()

        f.vm.removeWorktree("${GitItem.KIND_BRANCH_WITH_WORKTREE}:main")
        f.vm.removeWorktree(staleId)
        f.vm.removeWorktree(orphanId)
        advanceUntilIdle()

        assertTrue(f.vm.uiState.value.pendingDeleteIds.isEmpty())
        assertNull(f.vm.uiState.value.undoPrompt)
        coVerify(exactly = 0) { f.repo.deleteGitItems(any(), any()) }
    }

    @Test
    fun removeWorktree_dirty_promptsWithThePath_thenReissuesOnlyTheRemovalWithForce() = runTest {
        val f = remoteDeleteFixture()
        advanceUntilIdle()
        coEvery { f.repo.deleteGitItems("alpha", listOf(worktreeOnlyTopic)) } returns GitDeleteResponse(
            results = listOf(
                failedResult(GitItem.KIND_ORPHAN_WORKTREE, "/repo/wt/topic", "force_required"),
            ),
        )
        f.vm.removeWorktree(topicId)
        advanceUntilIdle()

        assertEquals(listOf("/repo/wt/topic"), f.vm.uiState.value.forceDeletePrompt?.refs)

        val forced = listOf(worktreeOnlyTopic.copy(force = true))
        coEvery { f.repo.deleteGitItems("alpha", forced) } returns GitDeleteResponse(
            results = listOf(okResult(GitItem.KIND_ORPHAN_WORKTREE, "/repo/wt/topic")),
        )
        f.vm.confirmForceDelete()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", forced) }
        assertNull(f.vm.uiState.value.forceDeletePrompt)
    }

    @Test
    fun toggleFolder_opensAndClosesALocalFolder() = runTest {
        val f = newFixture(
            initialItems = listOf(branchOnly("feature/a"), branchOnly("feature/b"), branchOnly("main")),
        )
        advanceUntilIdle()
        val folder = f.vm.uiState.value.localRows.first() as GitTreeRow.Folder
        assertEquals("dir:local:feature", folder.id)
        assertFalse(folder.expanded)
        assertEquals(2, f.vm.uiState.value.localRows.size)

        f.vm.toggleFolder(folder.id)

        assertEquals(setOf("dir:local:feature"), f.vm.uiState.value.toggledFolders)
        assertEquals(4, f.vm.uiState.value.localRows.size)

        f.vm.toggleFolder(folder.id)

        assertTrue(f.vm.uiState.value.toggledFolders.isEmpty())
        assertEquals(2, f.vm.uiState.value.localRows.size)
    }

    @Test
    fun toggleFolder_isIgnoredWhileAFilterHoldsTheFoldersOpen_butNotForStaleTextBelowTheThreshold() = runTest {
        val f = newFixture(initialItems = (1..7).map { branchOnly("feature/$it") } + branchOnly("main"))
        advanceUntilIdle()
        f.vm.setFilter("feature/3")
        assertTrue(f.vm.uiState.value.filterActive)
        assertEquals(
            listOf(true),
            f.vm.uiState.value.localRows.filterIsInstance<GitTreeRow.Folder>().map { it.expanded },
        )

        f.vm.toggleFolder("dir:local:feature")

        assertTrue(f.vm.uiState.value.toggledFolders.isEmpty())

        val small = newFixture(initialItems = listOf(branchOnly("feature/a"), branchOnly("main")))
        advanceUntilIdle()
        small.vm.setFilter("feature")
        assertFalse("the field is hidden below eight rows", small.vm.uiState.value.filterActive)
        small.vm.toggleFolder("dir:local:feature")
        assertEquals(setOf("dir:local:feature"), small.vm.uiState.value.toggledFolders)
    }

    @Test
    fun deleteFailureReason_givesAuthRequiredItsOwnCopy_andKeepsTheRemoteReasonOtherwise() {
        assertEquals(
            "No credentials for this remote. Configure a credential helper or SSH agent on the agent host.",
            deleteFailureReason(
                GitDeleteResultItem(
                    kind = GitItem.KIND_REMOTE_BRANCH,
                    target = "origin/feature-x",
                    ok = false,
                    error = "auth_required",
                    error_detail = "fatal: could not read Username for 'https://example.com': terminal prompts disabled",
                ),
            ),
        )
        assertEquals(
            "[remote rejected] (deletion of the current branch prohibited)",
            deleteFailureReason(
                GitDeleteResultItem(
                    kind = GitItem.KIND_REMOTE_BRANCH,
                    target = "origin/main",
                    ok = false,
                    error = "rejected",
                    error_detail = "[remote rejected] (deletion of the current branch prohibited)",
                ),
            ),
        )
    }

    @Test
    fun remoteDeleteMessage_namesOneBranch_andCountsSeveral() {
        assertEquals(
            "Delete origin/a on the remote? This removes it for everyone with access to the remote.",
            remoteDeleteMessage(listOf("origin/a")),
        )
        assertEquals(
            "Delete 2 branches on the remote? This removes them for everyone with access to the remote.",
            remoteDeleteMessage(listOf("origin/a", "origin/b")),
        )
    }

    @Test
    fun gitItem_deletableAndRemovableRows() {
        val remote = remoteBranch("feature-x")
        assertTrue(remote.isDeletable(deleteRemote = true))
        assertFalse(remote.isDeletable(deleteRemote = false))
        assertFalse(branchOnly("main", isCurrent = true).isDeletable(deleteRemote = true))
        assertTrue(branchOnly("stale").isDeletable(deleteRemote = false))

        assertTrue(branchWithWorktree("topic", "/wt/topic").canRemoveWorktree)
        assertFalse(branchWithWorktree("main", "/repo", isCurrent = true).canRemoveWorktree)
        assertFalse(branchWithWorktree("topic", "").canRemoveWorktree)
        assertFalse(branchOnly("stale").canRemoveWorktree)
        assertFalse(orphanWorktree("/wt/x").canRemoveWorktree)
        assertFalse(remote.canRemoveWorktree)
    }

    // MARK: - Row and refusal copy

    @Test
    fun tipLine_joinsSubjectAndDay_orShowsWhicheverThereIs() {
        val row = branchOnly("b")
        assertEquals(
            "Fix login · 2026-09-16",
            tipLine(row.copy(subject = "  Fix login ", committed_at = "2026-09-16T10:20:30+02:00")),
        )
        assertEquals("Fix login", tipLine(row.copy(subject = "Fix login")))
        assertEquals("2026-09-16", tipLine(row.copy(committed_at = "2026-09-16T10:20:30+02:00")))
        assertEquals("2026-09-16", shortDate("2026-09-16"))
        assertNull(tipLine(row.copy(subject = "   ")))
        assertNull(shortDate(""))
    }

    @Test
    fun matchesFilter_isCaseInsensitive_onTheLabelAndTheWorktreePath() {
        assertTrue(matchesFilter(branchOnly("Feature/Login"), " login "))
        assertTrue(matchesFilter(remoteBranch("feature-x"), "ORIGIN/"))
        assertTrue(matchesFilter(branchWithWorktree("b", "/wt/Alpha"), "alpha"))
        assertTrue(matchesFilter(orphanWorktree("/wt/x"), "wt/x"))
        assertTrue(matchesFilter(branchOnly("b"), ""))
        assertFalse(matchesFilter(branchOnly("b"), "main"))
    }

    @Test
    fun checkoutErrorCopy_wordsEachRefusal_andFallsBackToTheSessionCopy() {
        assertEquals(dirtyCopy, checkoutErrorCopy(gitError("dirty", "error: Your local changes"), "b"))
        assertEquals(
            "feature-a is already checked out in another worktree (/wt/topic).",
            checkoutErrorCopy(gitError("checked_out_elsewhere", "/wt/topic"), "feature-a"),
        )
        assertEquals(
            "feature-a is already checked out in another worktree.",
            checkoutErrorCopy(gitError("checked_out_elsewhere"), "feature-a"),
        )
        assertEquals(
            "A merge, rebase or cherry-pick is in progress in the project folder. Finish or abort it first.",
            checkoutErrorCopy(gitError("operation_in_progress", "merging"), "feature-a"),
        )
        assertEquals(
            "A fetch, pull or push is still running in the project folder.",
            checkoutErrorCopy(gitError("job_running", "job-1"), "feature-a"),
        )
        assertEquals(
            "Branch not found — refresh the list.",
            checkoutErrorCopy(gitError("ref_not_found"), "feature-a"),
        )
        assertEquals(
            "git failed: fatal: boom",
            checkoutErrorCopy(gitError("git_error", "fatal: boom"), "feature-a"),
        )
        assertEquals("offline", checkoutErrorCopy(RuntimeException("offline"), "feature-a"))
    }

    @Test
    fun createBranchErrorCopy_namesTheClash_theBadName_andTheMissingStartPoint() {
        assertEquals(
            "A branch named feature-a already exists.",
            createBranchErrorCopy(gitError("exists", "feature-a"), "feature-a"),
        )
        assertEquals("Not a valid branch name.", createBranchErrorCopy(gitError("bad_branch_name"), "-x"))
        assertEquals(
            "Start point nope was not found.",
            createBranchErrorCopy(gitError("unknown_start_point", "nope"), "feature-a"),
        )
        assertEquals(
            "The start point was not found.",
            createBranchErrorCopy(gitError("unknown_start_point"), "feature-a"),
        )
        assertEquals(
            "feature-a is already checked out in another worktree (/wt).",
            createBranchErrorCopy(gitError("checked_out_elsewhere", "/wt"), "feature-a"),
        )
    }

    @Test
    fun branchMessages_nameTheBranchTheAgentTouched() {
        assertEquals(
            "Check out feature-a in the project folder of alpha? " +
                "Sessions started in that folder afterwards use this branch.",
            checkoutConfirmMessage("feature-a", "alpha"),
        )
        assertEquals("Checked out feature-a", checkedOutMessage(GitBranchResponse("feature-a", true, false)))
        assertEquals("Created feature-a", branchCreatedMessage(GitBranchResponse("feature-a", false, true)))
        assertEquals(
            "Created and checked out feature-a",
            branchCreatedMessage(GitBranchResponse("feature-a", true, true)),
        )
    }
}

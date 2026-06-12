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
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.advanceUntilIdle
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
 * advances only on explicit `advanceUntilIdle()`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

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

    @Test
    fun enterSelection_onDeletableItem_entersSelectionMode_andSelects() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val staleId = "${GitItem.KIND_BRANCH_ONLY}:stale"
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
        val mainId = "${GitItem.KIND_BRANCH_ONLY}:main"
        f.vm.enterSelection(mainId)
        val state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun toggle_addsThenRemoves_andExitsSelectionWhenLastUnchecked() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val staleId = "${GitItem.KIND_BRANCH_ONLY}:stale"
        f.vm.enterSelection(staleId)
        // Toggle off → no items selected → must exit selection mode.
        f.vm.toggle(staleId)
        val state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun toggle_currentBranch_isNoOp() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val staleId = "${GitItem.KIND_BRANCH_ONLY}:stale"
        f.vm.enterSelection(staleId)  // already in selection mode
        val mainId = "${GitItem.KIND_BRANCH_ONLY}:main"
        f.vm.toggle(mainId)
        // Selection unchanged.
        assertEquals(setOf(staleId), f.vm.uiState.value.selected)
    }

    @Test
    fun exitSelection_clearsSelectedAndMode() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.enterSelection("${GitItem.KIND_BRANCH_ONLY}:stale")
        f.vm.exitSelection()
        val state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun requestDelete_emitsDeletedWithUndo_andClearsSelection() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val staleId = "${GitItem.KIND_BRANCH_ONLY}:stale"
        f.vm.enterSelection(staleId)

        f.vm.events.test {
            f.vm.requestDelete()
            val event = awaitItem()
            assertTrue(event is GitUiEvent.DeletedWithUndo)
            val items = (event as GitUiEvent.DeletedWithUndo).requestItems
            assertEquals(1, items.size)
            assertEquals(GitItem.KIND_BRANCH_ONLY, items[0].kind)
            assertEquals("stale", items[0].branch)
            assertFalse("force must default to false", items[0].force)
            cancelAndIgnoreRemainingEvents()
        }
        // Selection must clear so the screen leaves selection mode.
        val state = f.vm.uiState.value
        assertFalse(state.inSelectionMode)
        assertTrue(state.selected.isEmpty())
    }

    @Test
    fun requestDelete_skipsWhenNothingSelected() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.events.test {
            f.vm.requestDelete()
            advanceUntilIdle()
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun requestDelete_buildsItemsForBranchWithWorktree_andOrphanWorktree() = runTest {
        // The wire payload must echo `kind`, `branch`, and `worktree_path` so the
        // server receives enough info to dispatch each row correctly.
        val f = newFixture()
        advanceUntilIdle()
        val topicId = "${GitItem.KIND_BRANCH_WITH_WORKTREE}:topic"
        val orphanId = "${GitItem.KIND_ORPHAN_WORKTREE}:/repo/wt/orphan"
        f.vm.enterSelection(topicId)
        f.vm.toggle(orphanId)

        f.vm.events.test {
            f.vm.requestDelete()
            val event = awaitItem() as GitUiEvent.DeletedWithUndo
            val byKind = event.requestItems.associateBy { it.kind }
            assertEquals("topic", byKind[GitItem.KIND_BRANCH_WITH_WORKTREE]?.branch)
            assertEquals(
                "/repo/wt/topic",
                byKind[GitItem.KIND_BRANCH_WITH_WORKTREE]?.worktree_path,
            )
            assertEquals(
                "/repo/wt/orphan",
                byKind[GitItem.KIND_ORPHAN_WORKTREE]?.worktree_path,
            )
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun confirmDelete_callsRepoAndEmitsDeleteResults() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"))
        coEvery { f.repo.deleteGitItems("alpha", items) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_BRANCH_ONLY, target = "stale", ok = true,
                ),
            ),
        )

        f.vm.events.test {
            f.vm.confirmDelete(items)
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is GitUiEvent.DeleteResults)
            val results = (event as GitUiEvent.DeleteResults).results
            assertEquals(1, results.size)
            assertTrue(results[0].ok)
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", items) }
        assertFalse(f.vm.uiState.value.deleting)
    }

    @Test
    fun confirmDelete_forceRequired_populatesForceDeletePrompt() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"))
        coEvery { f.repo.deleteGitItems("alpha", items) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_BRANCH_ONLY,
                    target = "stale",
                    ok = false,
                    error = "force_required",
                ),
            ),
        )
        f.vm.confirmDelete(items)
        advanceUntilIdle()
        val prompt = f.vm.uiState.value.forceDeletePrompt
        assertNotNull("force_required result must populate forceDeletePrompt", prompt)
        assertEquals(listOf("stale"), prompt!!.refs)
        assertEquals(1, prompt.items.size)
        // Items in the prompt are the original (force=false) request — caller
        // must opt in by hitting Confirm.
        assertFalse(prompt.items[0].force)
    }

    @Test
    fun confirmForceDelete_reissuesWithForceTrue() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"))
        coEvery { f.repo.deleteGitItems("alpha", items) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_BRANCH_ONLY,
                    target = "stale",
                    ok = false,
                    error = "force_required",
                ),
            ),
        )
        f.vm.confirmDelete(items)
        advanceUntilIdle()

        // Stub the second (force=true) call — must succeed.
        val forced = items.map { it.copy(force = true) }
        coEvery { f.repo.deleteGitItems("alpha", forced) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_BRANCH_ONLY, target = "stale", ok = true,
                ),
            ),
        )

        f.vm.confirmForceDelete()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", forced) }
        assertNull(
            "forceDeletePrompt must clear after the user confirmed",
            f.vm.uiState.value.forceDeletePrompt,
        )
    }

    @Test
    fun confirmDelete_dirtyWorktree_forceRequired_promptsThenReissuesWithForce() = runTest {
        // A dirty worktree comes back as force_required just like an unmerged
        // branch; the prompt must surface the worktree path (no branch ref) and
        // confirming must re-issue that item with force=true.
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(
            GitDeleteItem(
                kind = GitItem.KIND_ORPHAN_WORKTREE,
                worktree_path = "/repo/wt/orphan",
            ),
        )
        coEvery { f.repo.deleteGitItems("alpha", items) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_ORPHAN_WORKTREE,
                    target = "/repo/wt/orphan",
                    ok = false,
                    error = "force_required",
                ),
            ),
        )
        f.vm.confirmDelete(items)
        advanceUntilIdle()

        val prompt = f.vm.uiState.value.forceDeletePrompt
        assertNotNull("dirty worktree force_required must populate the prompt", prompt)
        // refs falls back to worktree_path when the item has no branch.
        assertEquals(listOf("/repo/wt/orphan"), prompt!!.refs)

        val forced = items.map { it.copy(force = true) }
        coEvery { f.repo.deleteGitItems("alpha", forced) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_ORPHAN_WORKTREE,
                    target = "/repo/wt/orphan",
                    ok = true,
                ),
            ),
        )
        f.vm.confirmForceDelete()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.repo.deleteGitItems("alpha", forced) }
        assertNull(f.vm.uiState.value.forceDeletePrompt)
    }

    @Test
    fun confirmDelete_busyException_setsBusyDialog_noEvent() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(
            kind = GitItem.KIND_BRANCH_WITH_WORKTREE,
            branch = "topic",
            worktree_path = "/repo/wt/topic",
        ))
        val holders = listOf(
            BusyHolder(kind = "session", session_id = "s-1", cwd = "/repo/wt/topic"),
        )
        coEvery { f.repo.deleteGitItems("alpha", items) } throws GitBusyException(holders)

        f.vm.confirmDelete(items)
        advanceUntilIdle()
        val state = f.vm.uiState.value
        assertEquals(holders, state.busyDialog)
        assertFalse(state.deleting)
    }

    @Test
    fun confirmDelete_unknownException_emitsMessageEvent() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"))
        coEvery { f.repo.deleteGitItems("alpha", items) } throws RuntimeException("oops")

        f.vm.events.test {
            f.vm.confirmDelete(items)
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is GitUiEvent.Message)
            assertTrue((event as GitUiEvent.Message).text.contains("oops"))
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(f.vm.uiState.value.deleting)
    }

    @Test
    fun dismissBusyDialog_clearsBusyState() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(
            kind = GitItem.KIND_ORPHAN_WORKTREE,
            worktree_path = "/repo/wt/orphan",
        ))
        coEvery { f.repo.deleteGitItems("alpha", items) } throws
            GitBusyException(listOf(BusyHolder(kind = "auto_scan", project_name = "alpha")))

        f.vm.confirmDelete(items)
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.busyDialog)
        f.vm.dismissBusyDialog()
        assertNull(f.vm.uiState.value.busyDialog)
    }

    @Test
    fun dismissForceDeletePrompt_clearsPromptState() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val items = listOf(GitDeleteItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "stale"))
        coEvery { f.repo.deleteGitItems("alpha", items) } returns GitDeleteResponse(
            results = listOf(
                GitDeleteResultItem(
                    kind = GitItem.KIND_BRANCH_ONLY,
                    target = "stale",
                    ok = false,
                    error = "force_required",
                ),
            ),
        )
        f.vm.confirmDelete(items)
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.forceDeletePrompt)
        f.vm.dismissForceDeletePrompt()
        assertNull(f.vm.uiState.value.forceDeletePrompt)
    }

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
        val staleId = "${GitItem.KIND_BRANCH_ONLY}:stale"
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
    fun refresh_re_fetchesViaRepository() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.refresh()
        advanceUntilIdle()
        coVerify(exactly = 2) { f.repo.listGitItems("alpha") }
    }
}

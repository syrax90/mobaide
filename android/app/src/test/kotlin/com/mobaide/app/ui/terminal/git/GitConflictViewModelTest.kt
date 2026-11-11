package com.mobaide.app.ui.terminal.git

import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.text.TextRange
import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictRestoreRequest
import com.mobaide.app.data.network.dto.GitConflictSide
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.IOException

/**
 * Unit tests for [GitConflictViewModel]: the file seeds the editor once, the
 * marker scan follows the text after its debounce, a write carries the token
 * the text was read under, and the agent's `file_changed` / `not_conflicted`
 * refusals never cost the user the draft.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitConflictViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val path = "src/login.py"
    private val conflicted = "a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9 (Fix)\nz\n"

    private fun conflict(
        content: String? = conflicted,
        token: String? = "t1",
        kind: String = GitConflictResponse.KIND_TEXT,
    ) = GitConflictResponse(
        path = path, code = "UU", state = GitRepoInfo.STATE_REBASING, kind = kind,
        content = content, token = token, markers = 1,
        ours = GitConflictSide("main", true), theirs = GitConflictSide("fedcba9 Fix", true),
    )

    private fun gitError(code: String, message: String? = null) =
        SessionGitException.GitError(code, message, ApiException(409, ""))

    private fun repoServing(vararg files: GitConflictResponse): GitRepository {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionConflict("s1", path) } returnsMany files.toList()
        return repo
    }

    private fun TestScope.newViewModel(repo: GitRepository): GitConflictViewModel {
        val vm = GitConflictViewModel(
            SavedStateHandle(mapOf("sessionId" to "s1", "path" to path)),
            repo,
            ForegroundSessionTracker(),
        )
        runCurrent()
        return vm
    }

    private fun ready(vm: GitConflictViewModel): GitConflictUiState.Ready =
        vm.uiState.value as GitConflictUiState.Ready

    /** Type over the whole field and let the scan's debounce run out. */
    private fun TestScope.type(vm: GitConflictViewModel, text: String) {
        vm.editor.edit { replace(0, length, text) }
        // no composition here to apply the snapshot write `snapshotFlow` waits on
        Snapshot.sendApplyNotifications()
        advanceTimeBy(151)
        runCurrent()
    }

    @Test
    fun loadsTheFile_intoTheEditor_withItsMarkersCounted() = runTest {
        val vm = newViewModel(repoServing(conflict()))

        val ready = ready(vm)
        assertEquals(conflicted, vm.editor.text.toString())
        assertEquals(TextRange.Zero, vm.editor.selection)
        assertEquals(ConflictScan(conflicts = 1, hasMarkers = true), ready.scan)
        assertFalse(ready.dirty)
        assertFalse(ready.busy)
        assertFalse(ready.canApply)
    }

    @Test
    fun typing_settlesAfterTheDebounce_intoADirtyRescannedDraft() = runTest {
        val vm = newViewModel(repoServing(conflict()))

        vm.editor.edit { replace(0, length, "a\nmerged\nz\n") }
        Snapshot.sendApplyNotifications()
        advanceTimeBy(100)
        runCurrent()
        val midway = ready(vm)
        advanceTimeBy(51)
        runCurrent()

        assertTrue(midway.scan.hasMarkers)
        assertEquals(ConflictScan(), ready(vm).scan)
        assertTrue(ready(vm).dirty)
        assertTrue(ready(vm).canApply)
    }

    @Test
    fun typingTheLoadedTextBack_isCleanAgain() = runTest {
        val vm = newViewModel(repoServing(conflict()))

        type(vm, "changed")
        type(vm, conflicted)

        assertFalse(ready(vm).dirty)
        assertFalse(ready(vm).canSaveDraft)
    }

    @Test
    fun aTextOverTheFilesByteLimit_offersNeitherSave_untilItIsCutBackDown() = runTest {
        val vm = newViewModel(repoServing(conflict().copy(byte_limit = 1024)))

        // 600 chars in the field, 1 200 bytes on the wire
        type(vm, "é".repeat(600))
        val oversized = ready(vm)
        type(vm, "é".repeat(512))

        assertEquals(ConflictScan(tooLarge = true), oversized.scan)
        assertTrue(oversized.dirty)
        assertFalse(oversized.canApply)
        assertFalse(oversized.canSaveDraft)
        assertEquals(ConflictScan(), ready(vm).scan)
        assertTrue(ready(vm).canApply)
        assertTrue(ready(vm).canSaveDraft)
    }

    @Test
    fun apply_sendsTheTextUnderTheTokenItWasReadWith_andClosesWithTheOutcome() = runTest {
        val repo = repoServing(conflict())
        val vm = newViewModel(repo)
        type(vm, "a\nmerged\nz\n")

        vm.events.test {
            vm.apply()
            runCurrent()

            assertEquals(GitConflictEvent.Close("Resolved src/login.py."), awaitItem())
        }
        coVerify(exactly = 1) { repo.resolveSessionConflict("s1", path, "t1", "a\nmerged\nz\n", true) }
        coVerify(exactly = 1) { repo.sessionConflict("s1", path) }
        assertFalse(ready(vm).busy)
    }

    @Test
    fun saveDraft_keepsTheText_adoptsTheFilesNewToken_andSaysSo() = runTest {
        val draft = "a\n<<<<<<< HEAD\nhalf\n=======\ntopic\n>>>>>>> x\nz\n"
        val repo = repoServing(conflict(), conflict(content = draft, token = "t2"))
        val vm = newViewModel(repo)
        type(vm, draft)

        vm.events.test {
            vm.saveDraft()
            runCurrent()

            assertEquals(
                GitConflictEvent.ShowMessage("Saved src/login.py — the conflict is still open."),
                awaitItem(),
            )
        }

        coVerify(exactly = 1) { repo.resolveSessionConflict("s1", path, "t1", draft, false) }
        coVerify(exactly = 2) { repo.sessionConflict("s1", path) }
        assertEquals(draft, vm.editor.text.toString())
        assertFalse(ready(vm).dirty)
        assertFalse(ready(vm).canSaveDraft)
        assertFalse(ready(vm).overwriteWarning)

        type(vm, "a\nmerged\nz\n")
        vm.apply()
        runCurrent()

        coVerify(exactly = 1) { repo.resolveSessionConflict("s1", path, "t2", "a\nmerged\nz\n", true) }
    }

    @Test
    fun saveDraft_whenTheFileReadBackIsNotTheDraft_keepsTheDraft_andWarnsBeforeApplyOverwrites() = runTest {
        val draft = "a\n<<<<<<< HEAD\nhalf\n=======\ntopic\n>>>>>>> x\nz\n"
        // Someone wrote the file between the save and the read that follows it.
        val repo = repoServing(conflict(), conflict(content = "a\nsomeone else\nz\n", token = "t2"))
        val vm = newViewModel(repo)
        type(vm, draft)

        vm.saveDraft()
        runCurrent()

        val saved = ready(vm)
        assertEquals(draft, vm.editor.text.toString())
        assertTrue(saved.overwriteWarning)
        assertFalse(saved.stale)
        assertFalse(saved.dirty)

        type(vm, "a\nmerged\nz\n")
        vm.apply()
        runCurrent()

        coVerify(exactly = 1) { repo.resolveSessionConflict("s1", path, "t2", "a\nmerged\nz\n", true) }
    }

    @Test
    fun aFileThatMovedOn_raisesTheStaleFlag_andKeepsTheDraft() = runTest {
        val repo = repoServing(conflict())
        coEvery { repo.resolveSessionConflict(any(), any(), any(), any(), any()) } throws
            gitError(SessionGitException.CODE_FILE_CHANGED, path)
        val vm = newViewModel(repo)
        type(vm, "mine\n")

        vm.events.test {
            vm.apply()
            runCurrent()

            expectNoEvents()
        }
        assertTrue(ready(vm).stale)
        assertFalse(ready(vm).busy)
        assertEquals("mine\n", vm.editor.text.toString())
    }

    @Test
    fun reloadFile_replacesTheDraftWithTheFileAsItIsNow() = runTest {
        val repo = repoServing(conflict(), conflict(content = "theirs now\n", token = "t2"))
        coEvery { repo.resolveSessionConflict("s1", path, "t1", any(), any()) } throws
            gitError(SessionGitException.CODE_FILE_CHANGED, path)
        val vm = newViewModel(repo)
        type(vm, "mine\n")
        vm.apply()
        runCurrent()

        vm.reloadFile()
        runCurrent()

        val ready = ready(vm)
        assertEquals("theirs now\n", vm.editor.text.toString())
        assertFalse(ready.stale)
        assertFalse(ready.dirty)
        assertFalse(ready.overwriteWarning)
        assertEquals(ConflictScan(), ready.scan)
    }

    @Test
    fun keepMyEdits_keepsTheDraft_warns_andAppliesOverTheNewerFile() = runTest {
        val repo = repoServing(conflict(), conflict(content = "theirs now\n", token = "t2"))
        coEvery { repo.resolveSessionConflict("s1", path, "t1", any(), any()) } throws
            gitError(SessionGitException.CODE_FILE_CHANGED, path)
        val vm = newViewModel(repo)
        type(vm, "mine\n")
        vm.apply()
        runCurrent()

        vm.keepMyEdits()
        runCurrent()

        val kept = ready(vm)
        assertEquals("mine\n", vm.editor.text.toString())
        assertFalse(kept.stale)
        assertTrue(kept.overwriteWarning)
        assertTrue(kept.dirty)

        vm.apply()
        runCurrent()

        coVerify(exactly = 1) { repo.resolveSessionConflict("s1", path, "t2", "mine\n", true) }
    }

    @Test
    fun aFileNoLongerInConflict_stopsTheSaves_butKeepsTheDraft() = runTest {
        val repo = repoServing(conflict())
        coEvery { repo.resolveSessionConflict(any(), any(), any(), any(), any()) } throws
            gitError(SessionGitException.CODE_NOT_CONFLICTED, path)
        val vm = newViewModel(repo)
        type(vm, "mine\n")

        vm.events.test {
            vm.apply()
            runCurrent()

            expectNoEvents()
        }
        val gone = ready(vm)
        assertTrue(gone.gone)
        assertFalse(gone.canApply)
        assertFalse(gone.canSaveDraft)
        assertEquals("mine\n", vm.editor.text.toString())
    }

    @Test
    fun anyOtherRefusal_isAMessage_andTheDraftStays() = runTest {
        val repo = repoServing(conflict())
        coEvery { repo.resolveSessionConflict(any(), any(), any(), any(), any()) } throws
            gitError(SessionGitException.CODE_TOO_LARGE)
        val vm = newViewModel(repo)
        type(vm, "mine\n")

        vm.events.test {
            vm.apply()
            runCurrent()

            assertEquals(
                GitConflictEvent.ShowMessage("The text is too large to save from here."),
                awaitItem(),
            )
        }
        val after = ready(vm)
        assertFalse(after.stale)
        assertFalse(after.gone)
        assertFalse(after.busy)
        assertEquals("mine\n", vm.editor.text.toString())
    }

    @Test
    fun aSecondWrite_whileOneIsInFlight_isIgnored() = runTest {
        val repo = repoServing(conflict())
        val reply = CompletableDeferred<Unit>()
        coEvery { repo.resolveSessionConflict(any(), any(), any(), any(), any()) } coAnswers { reply.await() }
        val vm = newViewModel(repo)
        type(vm, "mine\n")

        vm.apply()
        runCurrent()
        val inFlight = ready(vm)
        vm.apply()
        vm.saveDraft()
        runCurrent()
        reply.complete(Unit)
        runCurrent()

        assertTrue(inFlight.busy)
        assertFalse(inFlight.canApply)
        coVerify(exactly = 1) { repo.resolveSessionConflict(any(), any(), any(), any(), any()) }
    }

    @Test
    fun take_sendsTheChosenSide_andClosesWithTheOutcome() = runTest {
        val repo = repoServing(conflict())
        val vm = newViewModel(repo)
        val theirs = takeChoices(ready(vm).conflict).single { it.side == GitConflictResponse.SIDE_THEIRS }

        vm.events.test {
            vm.take(theirs)
            runCurrent()

            assertEquals(GitConflictEvent.Close("Resolved src/login.py."), awaitItem())
        }
        coVerify(exactly = 1) { repo.takeSessionConflict("s1", path, "theirs") }
    }

    @Test
    fun startOver_rebuildsUnderTheOpenFilesToken_andReseedsTheEditor() = runTest {
        val rebuilt = "a\n<<<<<<< main\nmain\n||||||| base\norig\n=======\ntopic\n>>>>>>> fedcba9 Fix\nz\n"
        val repo = repoServing(conflict(), conflict(content = rebuilt, token = "t2"))
        val vm = newViewModel(repo)
        type(vm, "half done\n")

        vm.startOver(withOriginalLines = true)
        runCurrent()

        coVerify(exactly = 1) {
            repo.restoreSessionConflict("s1", path, GitConflictRestoreRequest.STYLE_DIFF3, "t1")
        }
        val ready = ready(vm)
        assertEquals(rebuilt, vm.editor.text.toString())
        assertEquals(ConflictScan(conflicts = 1, hasMarkers = true, hasBase = true), ready.scan)
        assertFalse(ready.dirty)
        assertFalse(ready.busy)
    }

    @Test
    fun startOver_withoutOriginalLines_asksForTheMergeStyle() = runTest {
        val repo = repoServing(conflict(), conflict())
        val vm = newViewModel(repo)

        vm.startOver(withOriginalLines = false)
        runCurrent()

        coVerify(exactly = 1) {
            repo.restoreSessionConflict("s1", path, GitConflictRestoreRequest.STYLE_MERGE, "t1")
        }
    }

    @Test
    fun startOver_refusedBecauseTheFileMovedOn_keepsTheDraftAndRaisesTheStaleFlag() = runTest {
        val repo = repoServing(conflict())
        coEvery { repo.restoreSessionConflict(any(), any(), any(), any()) } throws
            gitError(SessionGitException.CODE_FILE_CHANGED, path)
        val vm = newViewModel(repo)
        type(vm, "half done\n")

        vm.startOver(withOriginalLines = false)
        runCurrent()

        assertTrue(ready(vm).stale)
        assertEquals("half done\n", vm.editor.text.toString())
        coVerify(exactly = 1) { repo.sessionConflict("s1", path) }
    }

    @Test
    fun aFileThatIsNotText_isReadyWithoutAnEditorOrASave() = runTest {
        val vm = newViewModel(
            repoServing(conflict(content = null, token = null, kind = GitConflictResponse.KIND_BINARY)),
        )

        val ready = ready(vm)
        assertFalse(ready.editable)
        assertFalse(ready.canApply)
        assertEquals("", vm.editor.text.toString())

        vm.apply()
        runCurrent()
        assertFalse(ready(vm).busy)
    }

    @Test
    fun loadFailures_mapToTheirStates() = runTest {
        val outdated = mockk<GitRepository>(relaxed = true)
        coEvery { outdated.sessionConflict("s1", path) } throws
            SessionGitException.AgentOutdated(ApiException(404, ""))
        val gone = mockk<GitRepository>(relaxed = true)
        coEvery { gone.sessionConflict("s1", path) } throws
            SessionGitException.SessionGone(ApiException(404, ""))
        val offline = mockk<GitRepository>(relaxed = true)
        coEvery { offline.sessionConflict("s1", path) } throws IOException("down")

        assertEquals(GitConflictUiState.AgentOutdated, newViewModel(outdated).uiState.value)
        assertEquals(GitConflictUiState.SessionGone, newViewModel(gone).uiState.value)
        assertEquals(
            GitConflictUiState.Error(sessionGitErrorCopy(IOException("down"))),
            newViewModel(offline).uiState.value,
        )
    }

    @Test
    fun retry_afterAFailedLoad_seedsTheEditor() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionConflict("s1", path) } throws IOException("down") andThen conflict()
        val vm = newViewModel(repo)

        vm.retry()
        runCurrent()

        assertEquals(conflicted, vm.editor.text.toString())
        assertFalse(ready(vm).busy)
    }

    @Test
    fun aFileResolvedBeforeItCouldBeRead_closesWithTheReason() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionConflict("s1", path) } throws
            gitError(SessionGitException.CODE_NOT_CONFLICTED, path)
        val vm = GitConflictViewModel(
            SavedStateHandle(mapOf("sessionId" to "s1", "path" to path)), repo, ForegroundSessionTracker(),
        )

        vm.events.test {
            assertEquals(GitConflictEvent.Close("login.py is no longer in conflict."), awaitItem())
        }
    }

    @Test
    fun aFailedReload_neverCostsTheDraft() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionConflict("s1", path) } returns conflict() andThenThrows IOException("down")
        val vm = newViewModel(repo)
        type(vm, "mine\n")

        vm.events.test {
            vm.keepMyEdits()
            runCurrent()

            assertEquals(GitConflictEvent.ShowMessage(sessionGitErrorCopy(IOException("down"))), awaitItem())
        }
        assertEquals("mine\n", vm.editor.text.toString())
        assertTrue(ready(vm).dirty)
        assertFalse(ready(vm).busy)
    }

    @Test
    fun aSessionThatEndedUnderTheDraft_isAMessage_whicheverReloadFindsOut() = runTest {
        val reloads = listOf<(GitConflictViewModel) -> Unit>({ it.keepMyEdits() }, { it.reloadFile() })
        for (reload in reloads) {
            val ended = SessionGitException.SessionGone(ApiException(404, ""))
            val repo = mockk<GitRepository>(relaxed = true)
            coEvery { repo.sessionConflict("s1", path) } returns conflict() andThenThrows ended
            val vm = newViewModel(repo)
            type(vm, "mine\n")

            vm.events.test {
                reload(vm)
                runCurrent()

                assertEquals(GitConflictEvent.ShowMessage(sessionGitErrorCopy(ended)), awaitItem())
            }
            assertEquals("mine\n", vm.editor.text.toString())
            assertTrue(ready(vm).dirty)
            assertFalse(ready(vm).busy)
        }
    }

    @Test
    fun aReloadThatFindsTheConflictGone_keepsTheDraftAndSaysSo() = runTest {
        val repo = mockk<GitRepository>(relaxed = true)
        coEvery { repo.sessionConflict("s1", path) } returns conflict() andThenThrows
            gitError(SessionGitException.CODE_NOT_CONFLICTED, path)
        val vm = newViewModel(repo)
        type(vm, "mine\n")

        vm.reloadFile()
        runCurrent()

        assertTrue(ready(vm).gone)
        assertEquals("mine\n", vm.editor.text.toString())
    }
}

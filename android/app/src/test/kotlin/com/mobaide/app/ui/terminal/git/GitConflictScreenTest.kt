package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictSide
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders [GitConflictContent] in every [GitConflictUiState] and checks what
 * the user can see and tap. Buttons are found by tag: their labels go through
 * the pixel theme's casing.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitConflictScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val path = "src/login.py"
    private val conflicted = "a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9 (Fix)\n"

    private fun conflict(
        code: String = "UU",
        kind: String = GitConflictResponse.KIND_TEXT,
        content: String? = conflicted,
        ours: GitConflictSide = GitConflictSide("origin/main", true),
        theirs: GitConflictSide = GitConflictSide("fedcba9 Fix the login redirect", true),
    ) = GitConflictResponse(
        path = path, code = code, state = GitRepoInfo.STATE_REBASING, kind = kind,
        content = content, token = "t1", markers = 1, ours = ours, theirs = theirs,
    )

    private fun ready(
        conflict: GitConflictResponse = conflict(),
        scan: ConflictScan = scanConflictText(conflict.content.orEmpty()),
        dirty: Boolean = false,
        busy: Boolean = false,
        stale: Boolean = false,
        overwriteWarning: Boolean = false,
        gone: Boolean = false,
    ) = GitConflictUiState.Ready(conflict, scan, dirty, busy, stale, overwriteWarning, gone)

    private class Taps {
        val calls = mutableListOf<String>()
        val taken = mutableListOf<TakeChoice>()
    }

    private fun setContent(state: GitConflictUiState, text: String = conflicted): Taps {
        val taps = Taps()
        val editor = TextFieldState(text)
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitConflictContent(
                    state = state,
                    path = path,
                    editor = editor,
                    onRetry = { taps.calls += "retry" },
                    onApply = { taps.calls += "apply" },
                    onSaveDraft = { taps.calls += "draft" },
                    onNextConflict = { taps.calls += "next" },
                    onReloadFile = { taps.calls += "reload" },
                    onKeepMyEdits = { taps.calls += "keep" },
                    onTake = { taps.taken += it },
                    onClose = { taps.calls += "close" },
                )
            }
        }
        return taps
    }

    @Test
    fun aConflictedFile_showsItsTextAndSides_holdsApply_andJumpsToTheNextConflict() {
        val taps = setContent(ready())

        composeRule.onNodeWithText("Both sides changed this file.").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CONFLICT_EDITOR_TAG).assertTextContains(conflicted)
        composeRule.onNodeWithTag(GIT_CONFLICT_COUNT_TAG).assertTextContains("1 conflict left")
        composeRule.onNodeWithTag(GIT_CONFLICT_APPLY_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CONFLICT_DRAFT_TAG).assertIsNotEnabled()

        composeRule.onNodeWithTag(GIT_CONFLICT_NEXT_TAG).assertIsEnabled().performClick()

        assertEquals(listOf("next"), taps.calls)
    }

    @Test
    fun anEditedFileWithoutMarkers_offersApplyAndTheDraft_andHasNoNextConflict() {
        val taps = setContent(ready(scan = ConflictScan(), dirty = true), text = "a\nmerged\n")

        composeRule.onNodeWithTag(GIT_CONFLICT_COUNT_TAG).assertTextContains("No conflict markers left")
        composeRule.onNodeWithTag(GIT_CONFLICT_NEXT_TAG).assertIsNotEnabled()

        composeRule.onNodeWithTag(GIT_CONFLICT_APPLY_TAG).assertIsEnabled().performClick()
        composeRule.onNodeWithTag(GIT_CONFLICT_DRAFT_TAG).assertIsEnabled().performClick()

        assertEquals(listOf("apply", "draft"), taps.calls)
    }

    @Test
    fun theLegend_opensOnDemand_andNamesTheSidesByLabelAndRole() {
        setContent(ready())
        composeRule.onAllNodesWithTag(GIT_CONFLICT_LEGEND_TAG).assertCountEquals(0)

        composeRule.onNodeWithTag(GIT_CONFLICT_LEGEND_TOGGLE_TAG).performClick()

        composeRule.onNodeWithTag(GIT_CONFLICT_LEGEND_TAG).assertIsDisplayed()
        composeRule.onNodeWithText(
            "Between <<<<<<< and =======: origin/main — the branch you are rebasing onto, " +
                "plus your commits already replayed.",
        ).assertIsDisplayed()
        composeRule.onNodeWithText(
            "Between ======= and >>>>>>>: fedcba9 Fix the login redirect — your commit being replayed.",
        ).assertIsDisplayed()
    }

    @Test
    fun aFileThatMovedOn_offersTheReloadOrTheDraft() {
        val taps = setContent(ready(scan = ConflictScan(), dirty = true, stale = true))

        composeRule.onNodeWithTag(GIT_CONFLICT_STALE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("login.py changed on the agent machine since you opened it.")
            .assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CONFLICT_RELOAD_TAG).performClick()
        composeRule.onNodeWithTag(GIT_CONFLICT_KEEP_EDITS_TAG).performClick()

        assertEquals(listOf("reload", "keep"), taps.calls)
    }

    @Test
    fun whileBusy_theStripsChoicesAndBothSavesWait() {
        setContent(ready(scan = ConflictScan(), dirty = true, stale = true, busy = true))

        composeRule.onNodeWithTag(GIT_CONFLICT_RELOAD_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CONFLICT_KEEP_EDITS_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CONFLICT_APPLY_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CONFLICT_DRAFT_TAG).assertIsNotEnabled()
    }

    @Test
    fun keepingTheEdits_warnsThatApplyOverwritesTheOtherChange() {
        setContent(ready(scan = ConflictScan(), dirty = true, overwriteWarning = true))

        composeRule.onNodeWithText("Applying will overwrite the other change.").assertIsDisplayed()
        composeRule.onAllNodesWithTag(GIT_CONFLICT_STALE_TAG).assertCountEquals(0)
    }

    @Test
    fun aTextTooLargeToSend_saysSoWithTheFilesLimit_andHoldsBothSaves() {
        val taps = setContent(
            ready(
                conflict = conflict().copy(byte_limit = 1024),
                scan = ConflictScan(tooLarge = true),
                dirty = true,
            ),
            text = "a\nmerged\n",
        )

        composeRule.onNodeWithTag(GIT_CONFLICT_TOO_LARGE_TAG)
            .assertIsDisplayed()
            .assertTextContains("Too large to save here (over 1 KB).")
        composeRule.onNodeWithTag(GIT_CONFLICT_COUNT_TAG).assertTextContains("No conflict markers left")
        composeRule.onNodeWithTag(GIT_CONFLICT_APPLY_TAG).assertIsNotEnabled().performClick()
        composeRule.onNodeWithTag(GIT_CONFLICT_DRAFT_TAG).assertIsNotEnabled().performClick()

        assertEquals(emptyList<String>(), taps.calls)
    }

    @Test
    fun aTextWithinTheLimit_showsNoSizeNotice() {
        setContent(ready(scan = ConflictScan(), dirty = true), text = "a\nmerged\n")

        composeRule.onAllNodesWithTag(GIT_CONFLICT_TOO_LARGE_TAG).assertCountEquals(0)
    }

    @Test
    fun aFileNoLongerInConflict_saysSo_stopsTheSaves_andOffersClose() {
        val taps = setContent(ready(scan = ConflictScan(), dirty = true, gone = true))

        composeRule.onNodeWithTag(GIT_CONFLICT_GONE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("login.py is no longer in conflict.").assertIsDisplayed()
        composeRule.onNodeWithTag(GIT_CONFLICT_APPLY_TAG).assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_CONFLICT_DRAFT_TAG).assertIsNotEnabled()

        composeRule.onNodeWithText("Close", ignoreCase = true).performClick()

        assertEquals(listOf("close"), taps.calls)
    }

    @Test
    fun aBinaryFile_showsWhy_andOnlyTheWholeFilePicks() {
        val binary = conflict(kind = GitConflictResponse.KIND_BINARY, content = null)
        val taps = setContent(ready(binary), text = "")

        composeRule.onNodeWithTag(GIT_CONFLICT_BLOCKED_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("This is a binary file — pick a side.").assertIsDisplayed()
        composeRule.onAllNodesWithTag(GIT_CONFLICT_EDITOR_TAG).assertCountEquals(0)
        composeRule.onAllNodesWithTag(GIT_CONFLICT_APPLY_TAG).assertCountEquals(0)

        composeRule.onNodeWithTag(gitConflictTakeTag("theirs")).performClick()

        assertEquals(listOf(takeChoices(binary)[1]), taps.taken)
        assertEquals("theirs", taps.taken.single().side)
    }

    @Test
    fun aDeletedSide_offersOneWayToKeepAndOneToDelete() {
        val deletedByUs = conflict(
            code = "DU", kind = GitConflictResponse.KIND_TOO_LARGE, content = null,
            ours = GitConflictSide("origin/main", false),
        )
        val taps = setContent(ready(deletedByUs), text = "")

        composeRule.onNodeWithText("origin/main deleted this file; fedcba9 Fix the login redirect changed it.")
            .assertIsDisplayed()
        composeRule.onNodeWithTag(gitConflictTakeTag("ours")).performClick()

        assertEquals(listOf("Delete file"), taps.taken.map { it.confirm })
        assertEquals(listOf(true), taps.taken.map { it.danger })
    }

    @Test
    fun aSubmodule_pointsAtTheTerminal_withNoPickAtAll() {
        setContent(ready(conflict(kind = GitConflictResponse.KIND_SUBMODULE, content = null)), text = "")

        composeRule.onNodeWithText("Submodule conflicts are resolved in the terminal.").assertIsDisplayed()
        composeRule.onAllNodesWithTag(gitConflictTakeTag("ours")).assertCountEquals(0)
        composeRule.onAllNodesWithTag(gitConflictTakeTag("theirs")).assertCountEquals(0)
    }

    @Test
    fun whileBusy_orOnceGone_theWholeFilePicksWait() {
        val binary = conflict(kind = GitConflictResponse.KIND_BINARY, content = null)
        setContent(ready(binary, busy = true), text = "")

        composeRule.onNodeWithTag(gitConflictTakeTag("ours")).assertIsNotEnabled()
        composeRule.onNodeWithTag(gitConflictTakeTag("theirs")).assertIsNotEnabled()
    }

    @Test
    fun loading_outdated_andSessionGone_eachHaveTheirOwnState() {
        var state: GitConflictUiState by mutableStateOf(GitConflictUiState.Loading)
        val editor = TextFieldState()
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitConflictContent(
                    state = state, path = path, editor = editor,
                    onRetry = {}, onApply = {}, onSaveDraft = {}, onNextConflict = {},
                    onReloadFile = {}, onKeepMyEdits = {}, onTake = {}, onClose = {},
                )
            }
        }

        composeRule.onNodeWithTag(GIT_CONFLICT_LOADING_TAG).assertIsDisplayed()

        state = GitConflictUiState.AgentOutdated
        composeRule.onNodeWithTag(GIT_CONFLICT_OUTDATED_TAG).assertIsDisplayed()
        composeRule.onNodeWithText(
            "This agent cannot resolve conflicts yet. Update it, or resolve them in the terminal.",
        ).assertIsDisplayed()

        state = GitConflictUiState.SessionGone
        composeRule.onNodeWithTag(GIT_CONFLICT_SESSION_GONE_TAG).assertIsDisplayed()
    }

    @Test
    fun aLoadError_showsItsCopy_andRetries() {
        val taps = setContent(GitConflictUiState.Error("git took too long to answer. Try again."))

        composeRule.onNodeWithTag(GIT_CONFLICT_ERROR_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("git took too long to answer. Try again.").assertIsDisplayed()

        composeRule.onNodeWithText("Retry", ignoreCase = true).performClick()

        assertEquals(listOf("retry"), taps.calls)
    }
}

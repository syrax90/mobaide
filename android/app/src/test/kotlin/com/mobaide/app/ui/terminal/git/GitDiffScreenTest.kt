package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.network.dto.GitDiffHunk
import com.mobaide.app.data.network.dto.GitDiffLine
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders [GitDiffContent] in its Ready state. The enormous-line case is a
 * layout regression guard: every diff row is pinned to one measured width,
 * and a single minified line used to push that width past what Compose
 * `Constraints` can represent, so the list threw before it drew anything.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitDiffScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun diff(lineText: String): GitDiffResponse = GitDiffResponse(
        path = "src/minified.js",
        scope = GitDiffResponse.SCOPE_UNSTAGED,
        kind = GitDiffResponse.KIND_TEXT,
        change = GitDiffResponse.CHANGE_MODIFIED,
        additions = 1,
        hunks = listOf(
            GitDiffHunk(
                old_start = 0, old_lines = 0, new_start = 1, new_lines = 1,
                lines = listOf(GitDiffLine(GitDiffLine.T_ADD, null, 1, lineText)),
            ),
        ),
    )

    private fun ready(diff: GitDiffResponse, context: Int = DIFF_CONTEXT_DEFAULT): GitDiffUiState.Ready {
        val rows = buildDiffRows(diff)
        return GitDiffUiState.Ready(diff, rows, gutterDigits(diff), widthCandidates(rows), context)
    }

    private fun setContent(
        state: GitDiffUiState,
        onSetContext: (Int) -> Unit = {},
        onRetry: () -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitDiffContent(state = state, onSetContext = onSetContext, onRetry = onRetry)
            }
        }
    }

    @Test
    fun aSingleEnormousLine_stillLaysOut() {
        setContent(ready(diff("x".repeat(300_000))))

        composeRule.onNodeWithTag(GIT_DIFF_LIST_TAG).assertIsDisplayed()
    }

    @Test
    fun theContextChips_showTheSelection_andReportATap() {
        val chosen = mutableListOf<Int>()
        setContent(ready(diff("short"), context = 3), onSetContext = { chosen += it })

        composeRule.onNodeWithTag(gitDiffContextChipTag(3)).assertIsDisplayed().assertIsSelected()
        composeRule.onNodeWithTag(gitDiffContextChipTag(10)).assertIsDisplayed().assertIsNotSelected()
        composeRule.onNodeWithTag(gitDiffContextChipTag(25)).assertIsDisplayed().assertIsNotSelected()

        composeRule.onNodeWithTag(gitDiffContextChipTag(10)).performClick()

        assertEquals(listOf(10), chosen)
    }
}

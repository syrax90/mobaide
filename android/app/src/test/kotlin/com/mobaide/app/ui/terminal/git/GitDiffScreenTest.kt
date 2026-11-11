package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
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

    private fun diff(
        vararg lineTexts: String,
        change: String = GitDiffResponse.CHANGE_MODIFIED,
        truncated: Boolean = false,
        totalLines: Int = 0,
    ): GitDiffResponse = GitDiffResponse(
        path = "src/minified.js",
        scope = GitDiffResponse.SCOPE_UNSTAGED,
        kind = GitDiffResponse.KIND_TEXT,
        change = change,
        additions = lineTexts.size,
        hunks = listOf(
            GitDiffHunk(
                old_start = 0, old_lines = 0, new_start = 1, new_lines = lineTexts.size,
                lines = lineTexts.mapIndexed { i, text -> GitDiffLine(GitDiffLine.T_ADD, null, i + 1, text) },
            ),
        ),
        truncated = truncated,
        total_lines = totalLines,
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
        setContent(ready(diff("short"), context = DIFF_CONTEXT_DEFAULT), onSetContext = { chosen += it })

        composeRule.onNodeWithTag(gitDiffContextChipTag(3)).assertIsDisplayed().assertIsSelected()
        composeRule.onNodeWithTag(gitDiffContextChipTag(5_000)).assertIsDisplayed().assertIsNotSelected()
        composeRule.onNodeWithText("CHANGES").assertIsDisplayed()
        composeRule.onNodeWithText("FULL FILE").assertIsDisplayed()

        composeRule.onNodeWithTag(gitDiffContextChipTag(5_000)).performClick()

        assertEquals(listOf(5_000), chosen)
    }

    @Test
    fun aTruncatedDiff_whoseCountStoppedAtTheCut_onlySaysItIsLonger() {
        // The byte cap can cut git off before `total_lines` gets past what was
        // shown, so the footer must not claim "3 of at least 3".
        setContent(ready(diff("a", "b", "c", truncated = true, totalLines = 3)))

        composeRule.onNodeWithText("Truncated after 3 lines — the full diff is longer").assertIsDisplayed()
    }
}

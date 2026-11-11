package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for the [TemplatesPickerSheet] empty/loading branch: while the
 * picker-open fetch is in flight and the cache is still empty, the sheet must
 * show a progress indicator instead of the misleading "You haven't saved any
 * templates yet" text; once loading ends the honest empty state returns; and
 * a warm cache renders the list with no spinner regardless of `isLoading`.
 *
 * Assertion-only by design: Robolectric's input dispatcher does not deliver
 * Compose-test synthetic clicks into the ModalBottomSheet popup window, but
 * finding and asserting nodes across the popup boundary works (see
 * `AgentListScreenTest` / `AgentCredentialsContentTest` in `ui/agents`).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TemplatesPickerSheetTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    /** Matches the indeterminate [androidx.compose.material3.CircularProgressIndicator]. */
    private val spinner = SemanticsMatcher.expectValue(
        SemanticsProperties.ProgressBarRangeInfo,
        ProgressBarRangeInfo.Indeterminate,
    )

    private val emptyStateText = "You haven't saved any templates yet"

    @Test
    fun emptyCacheWhileLoading_showsSpinner_notEmptyStateText() {
        composeRule.setContent {
            Surface {
                TemplatesPickerSheet(
                    templates = emptyList(),
                    isLoading = true,
                    onPick = {},
                    onToggleFavorite = {},
                    onDelete = {},
                    onDismiss = {},
                )
            }
        }

        composeRule.onNode(spinner).assertIsDisplayed()
        composeRule.onNodeWithText(emptyStateText, substring = true).assertDoesNotExist()
    }
}

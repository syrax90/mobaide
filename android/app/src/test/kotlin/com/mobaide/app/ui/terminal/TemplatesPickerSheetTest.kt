package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import com.mobaide.app.data.network.dto.TemplateDto
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

    private fun template(id: String, name: String) = TemplateDto(
        template_id = id,
        name = name,
        body = "deploy the thing",
        is_favorite = false,
        last_used_at = null,
        created_at = "2026-01-01T00:00:00Z",
        updated_at = "2026-01-01T00:00:00Z",
        version = 1,
    )

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

    @Test
    fun emptyCacheNotLoading_showsEmptyStateText_noSpinner() {
        // `isLoading` deliberately omitted: the default must be false so the
        // pre-existing empty state is unchanged for call sites that never load.
        composeRule.setContent {
            Surface {
                TemplatesPickerSheet(
                    templates = emptyList(),
                    onPick = {},
                    onToggleFavorite = {},
                    onDelete = {},
                    onDismiss = {},
                )
            }
        }

        composeRule.onNodeWithText(emptyStateText, substring = true).assertIsDisplayed()
        composeRule.onAllNodes(spinner).assertCountEquals(0)
    }

    @Test
    fun cachedTemplatesWhileLoading_showsList_noSpinner() {
        composeRule.setContent {
            Surface {
                TemplatesPickerSheet(
                    templates = listOf(template("t1", "Deploy prod")),
                    isLoading = true,
                    onPick = {},
                    onToggleFavorite = {},
                    onDelete = {},
                    onDismiss = {},
                )
            }
        }

        composeRule.onNodeWithText("Deploy prod").assertIsDisplayed()
        composeRule.onAllNodes(spinner).assertCountEquals(0)
    }
}

package com.mobaide.app.ui.sessions

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for the recent-path chips in [CustomPathPanel]. Forgetting a recent
 * path is destructive and has no confirmation step, and the trailing ✕ carries
 * a 48dp touch target that overlaps the label region, so a tap on the label
 * must pick and only the ✕ may remove.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class CustomPathPanelTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    /** Deliberately short and unlike each other: a mis-routed tap names the
     *  wrong path unambiguously. */
    private val pathA = "/a/one"
    private val pathB = "/b/two"

    private fun setContent(
        recents: List<String> = listOf(pathA, pathB),
        onPickRecent: (String) -> Unit = {},
        onRemoveRecent: (String) -> Unit = {},
    ) {
        composeRule.setContent {
            Surface {
                // The panel is emitted into the wizard's scrollable Column.
                Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                    CustomPathPanel(
                        state = CreateSessionUiState(recentCustomPaths = recents),
                        onDraftChange = {},
                        onPickRecent = onPickRecent,
                        onRemoveRecent = onRemoveRecent,
                    )
                }
            }
        }
    }

    /** Tap the chip body with a real coordinate click on the label text node
     *  (unmerged tree: the merged chip node also spans the trailing ✕, so its
     *  center could land inside the remove target). */
    private fun tapChipBody(path: String) {
        composeRule.onNodeWithText(path, useUnmergedTree = true)
            .performScrollTo()
            .performClick()
    }

    @Test
    fun recentChip_labelTap_landsOnPick_neverOnRemove() {
        val picked = mutableListOf<String>()
        val removed = mutableListOf<String>()
        setContent(onPickRecent = { picked += it }, onRemoveRecent = { removed += it })

        composeRule.onNodeWithText("Recent paths").assertExists()
        tapChipBody(pathA)

        assertEquals(listOf(pathA), picked)
        assertEquals(
            "a real tap on the label must never fall into the ✕ remove target",
            emptyList<String>(),
            removed,
        )
    }

    @Test
    fun recentChip_removeIcon_firesRemove_notPick() {
        val picked = mutableListOf<String>()
        val removed = mutableListOf<String>()
        setContent(onPickRecent = { picked += it }, onRemoveRecent = { removed += it })

        // The second chip: each ✕ must carry its own path, not the first one.
        composeRule.onNodeWithContentDescription("Remove $pathB from recent paths")
            .performScrollTo()
            .performClick()

        assertEquals(listOf(pathB), removed)
        assertEquals(
            "the ✕ must remove, never also pick the path",
            emptyList<String>(),
            picked,
        )
    }

    @Test
    fun emptyRecents_omitsRecentPathsSection() {
        setContent(recents = emptyList())

        composeRule.onNodeWithText("Recent paths").assertDoesNotExist()
        composeRule.onNodeWithTag("recent_custom_path_chip").assertDoesNotExist()
    }
}

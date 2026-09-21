package com.mobaide.app.ui.projects.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders one [GitItemRow] the way the branch tree places it: the row shows
 * only its leaf name, so the full branch name has to reach TalkBack apart
 * from the visible text.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitItemRowTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun aTreeLeaf_showsItsLastSegment_andNamesTheFullBranchForTalkBack() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitItemRow(
                    item = GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "feature/login/form"),
                    selected = false,
                    inSelectionMode = false,
                    onTap = {},
                    onLongPress = {},
                    label = "form",
                    indent = TREE_INDENT * 2,
                )
            }
        }

        composeRule
            .onNodeWithContentDescription("feature/login/form", useUnmergedTree = true)
            .assertTextEquals("form")
    }
}

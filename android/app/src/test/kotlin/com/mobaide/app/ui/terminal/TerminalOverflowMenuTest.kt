package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The terminal's `⋯` menu. Assertion-only past the popup boundary: Robolectric
 * does not deliver Compose-test synthetic clicks into a `DropdownMenu`'s popup
 * window, so the entry's `onClick` is not exercised here — only that opening
 * the menu reveals the Git section and its entry.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class TerminalOverflowMenuTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun moreOptions_opensAGitSectionWithTheChangesEntry() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                TerminalOverflowMenu(onOpenGitChanges = {})
            }
        }

        composeRule.onNodeWithText("Changes…").assertDoesNotExist()

        composeRule.onNodeWithContentDescription("More options")
            .assertIsDisplayed()
            .performClick()

        composeRule.onNodeWithText("GIT").assertIsDisplayed()
        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_CHANGES_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Changes…").assertIsDisplayed()
    }

    @Test
    fun commit_isAbsent_untilTheAgentAdvertisesIt() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                TerminalOverflowMenu(onOpenGitChanges = {}, commitAvailable = false)
            }
        }

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_CHANGES_TAG).assertIsDisplayed()
        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_COMMIT_TAG).assertDoesNotExist()
        composeRule.onNodeWithText("Commit…").assertDoesNotExist()
    }

    @Test
    fun commit_followsChanges_onAnAgentThatAdvertisesIt() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                TerminalOverflowMenu(onOpenGitChanges = {}, onOpenGitCommit = {}, commitAvailable = true)
            }
        }

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_COMMIT_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Commit…").assertIsDisplayed()
    }
}

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
}

/** The *Fetch / Pull / Push…* entry, gated on any of the three job capabilities. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class TerminalOverflowMenuSyncTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun sync_isAbsent_untilTheAgentAdvertisesAJob() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                TerminalOverflowMenu(onOpenGitChanges = {}, commitAvailable = true, syncAvailable = false)
            }
        }

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_COMMIT_TAG).assertIsDisplayed()
        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_SYNC_TAG).assertDoesNotExist()
        composeRule.onNodeWithText("Fetch / Pull / Push…").assertDoesNotExist()
    }
}

/** The *Rebase…* entry, gated on `capabilities.git.rebase`. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class TerminalOverflowMenuRebaseTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun rebase_isAbsent_untilTheAgentAdvertisesIt() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                TerminalOverflowMenu(
                    onOpenGitChanges = {},
                    branchesAvailable = true,
                    rebaseAvailable = false,
                )
            }
        }

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_BRANCHES_TAG).assertIsDisplayed()
        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_REBASE_TAG).assertDoesNotExist()
        composeRule.onNodeWithText("Rebase…").assertDoesNotExist()
    }

    @Test
    fun rebase_isOffered_onAnAgentThatAdvertisesIt_evenWithoutAProjectForBranches() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                TerminalOverflowMenu(
                    onOpenGitChanges = {},
                    onOpenGitRebase = {},
                    branchesAvailable = false,
                    rebaseAvailable = true,
                )
            }
        }

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_REBASE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Rebase…").assertIsDisplayed()
        composeRule.onNodeWithTag(TERMINAL_MENU_GIT_BRANCHES_TAG).assertDoesNotExist()
    }
}

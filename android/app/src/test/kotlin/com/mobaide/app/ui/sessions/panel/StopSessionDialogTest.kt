package com.mobaide.app.ui.sessions.panel

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The worktree removal offer of the stop confirmation: shown only for a
 * session in a worktree, never pre-checked, and passed to `onStop` only when
 * the user checks it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class StopSessionDialogTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun setDialog(
        worktreeBranch: String?,
        worktreeInUse: Boolean = false,
    ): MutableList<Pair<Boolean, Boolean>> {
        val stops = mutableListOf<Pair<Boolean, Boolean>>()
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                StopSessionDialog(
                    label = "Claude Code · app",
                    onStop = { force, removeWorktree -> stops += force to removeWorktree },
                    onDismiss = {},
                    worktreeBranch = worktreeBranch,
                    worktreeInUse = worktreeInUse,
                )
            }
        }
        return stops
    }

    @Test
    fun sessionWithoutWorktree_hasNoOffer() {
        val stops = setDialog(worktreeBranch = null)

        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_FORCE_TAG).performClick()

        assertEquals(listOf(true to false), stops)
    }

    @Test
    fun worktreeOffer_isUncheckedAndStopKeepsTheWorktree() {
        val stops = setDialog(worktreeBranch = "feature/x")

        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG).assertIsOff()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_STOP_TAG).performClick()

        assertEquals(listOf(false to false), stops)
    }

    @Test
    fun checkedWorktreeOffer_isPassedToStop() {
        val stops = setDialog(worktreeBranch = "feature/x")

        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG).performClick()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_FORCE_TAG).performClick()

        assertEquals(listOf(true to true), stops)
    }

    @Test
    fun tappingTheLabel_checksTheOffer() {
        val stops = setDialog(worktreeBranch = "feature/x")

        composeRule.onNodeWithText(worktreeRemovalLabel("feature/x")).performClick()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG).assertIsOn()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_STOP_TAG).performClick()

        assertEquals(listOf(false to true), stops)
    }

    @Test
    fun worktreeInUse_disablesTheOfferAndStopKeepsTheWorktree() {
        val stops = setDialog(worktreeBranch = "feature/x", worktreeInUse = true)

        composeRule.onNodeWithText(WORKTREE_IN_USE_HINT).assertExists()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG)
            .assertIsNotEnabled()
            .assertIsOff()
            .performClick()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_FORCE_TAG).performClick()

        assertEquals(listOf(true to false), stops)
    }
}

package com.mobaide.app.ui.terminal.git

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.mobaide.app.data.network.dto.GitCommitAuthor
import com.mobaide.app.data.network.dto.GitCommitHead
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders [GitCommitForm] (the sheet's content, without the sheet window)
 * and checks what the user can type, toggle and submit. The field text is
 * the form's own state; only the submit reaches the caller.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitCommitFormTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private data class Submit(
        val summary: String,
        val description: String,
        val amend: Boolean,
        val noVerify: Boolean,
    )

    private val author = GitCommitAuthor("Ada Lovelace", "ada@example.com")
    private val head = GitCommitHead(
        oid = "4890423520f4dcd158b85374f44fc8819a813ac2",
        short_oid = "4890423",
        subject = "Fix the login redirect",
        body = "The callback URL was built before the port was known.",
    )

    private fun ready(author: GitCommitAuthor? = this.author, head: GitCommitHead? = this.head) =
        CommitSheetState(context = CommitContextState.Ready(author, head))

    private fun setContent(
        state: CommitSheetState,
        stagedCount: Int = 1,
        onDismiss: () -> Unit = {},
        onCommit: (Submit) -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitCommitForm(
                    state = state,
                    stagedCount = stagedCount,
                    onDismiss = onDismiss,
                    onCommit = { summary, description, amend, noVerify ->
                        onCommit(Submit(summary, description, amend, noVerify))
                    },
                )
            }
        }
    }

    private fun submit() = composeRule.onNodeWithTag(GIT_COMMIT_SUBMIT_TAG)
    private fun summary() = composeRule.onNodeWithTag(GIT_COMMIT_SUMMARY_TAG)
    private fun description() = composeRule.onNodeWithTag(GIT_COMMIT_DESCRIPTION_TAG)

    @Test
    fun theSubmitButton_waitsForASummary_thenHandsOverEveryField() {
        val submits = mutableListOf<Submit>()
        setContent(ready(), onCommit = { submits += it })

        composeRule.onNodeWithText("Committing as Ada Lovelace <ada@example.com>").assertIsDisplayed()
        submit().assertIsNotEnabled()

        summary().performTextInput("Add a")
        description().performTextInput("Why.")
        composeRule.onNodeWithTag(GIT_COMMIT_NO_VERIFY_TAG).performClick()
        submit().assertIsEnabled().performClick()

        assertEquals(listOf(Submit("Add a", "Why.", amend = false, noVerify = true)), submits)
    }

    @Test
    fun amend_prefillsFromThePreviousCommit_andTakesThePrefillBackWhenUntoggledUntouched() {
        val submits = mutableListOf<Submit>()
        setContent(ready(), stagedCount = 0, onCommit = { submits += it })
        composeRule.onNodeWithText("Nothing is staged. Stage files first, or amend the previous commit.")
            .assertIsDisplayed()
        submit().assertIsNotEnabled()

        composeRule.onNodeWithTag(GIT_COMMIT_AMEND_TAG).performClick()

        summary().assertTextContains("Fix the login redirect")
        description().assertTextContains("The callback URL was built before the port was known.")
        submit().assertIsEnabled().assertTextContains("Amend commit", ignoreCase = true).performClick()
        assertEquals(
            listOf(
                Submit(
                    "Fix the login redirect",
                    "The callback URL was built before the port was known.",
                    amend = true,
                    noVerify = false,
                ),
            ),
            submits,
        )

        composeRule.onNodeWithTag(GIT_COMMIT_AMEND_TAG).performClick()

        // Emptied again, so with nothing staged there is nothing to submit.
        submit().assertIsNotEnabled()
    }

    @Test
    fun amend_isNotOffered_withoutAPreviousCommit() {
        setContent(ready(head = null))

        composeRule.onNodeWithTag(GIT_COMMIT_AMEND_TAG).assertDoesNotExist()
        composeRule.onNodeWithTag(GIT_COMMIT_NO_VERIFY_TAG).assertIsDisplayed()
    }

    @Test
    fun withoutAnIdentity_theHostSideFixIsShown_andNothingCanBeSubmitted() {
        setContent(ready(author = null))

        composeRule.onNodeWithText(IDENTITY_FIX_COMMANDS.joinToString("\n")).assertIsDisplayed()
        summary().performTextInput("Who am I")

        submit().assertIsNotEnabled()
    }

    @Test
    fun whileCommitting_theFieldsAndButtonsAreLocked() {
        var dismissed = 0
        setContent(ready().copy(committing = true), onDismiss = { dismissed++ })

        submit().assertTextContains("Committing…", ignoreCase = true).assertIsNotEnabled()
        summary().assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_COMMIT_AMEND_TAG).assertIsNotEnabled()
        composeRule.onNodeWithText("Cancel", ignoreCase = true).assertIsNotEnabled().performClick()
        assertEquals(0, dismissed)
    }

    @Test
    fun aLoadingOrFailedContext_leavesNothingToSubmit() {
        var dismissed = 0
        setContent(CommitSheetState(), onDismiss = { dismissed++ })
        summary().performTextInput("Add a")
        submit().assertIsNotEnabled()
        composeRule.onNodeWithText("Cancel", ignoreCase = true).performClick()
        assertEquals(1, dismissed)
    }
}

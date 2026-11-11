package com.mobaide.app.ui.projects.git

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
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Renders [GitNewBranchForm] (the sheet's content, without the sheet window)
 * and checks what the user can type, toggle and submit. The field text is
 * the form's own state; only the submit reaches the caller. The start-point
 * picker's popup cannot be driven under Robolectric, so its options are
 * checked through [startPointOptions] and its default through the field.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class GitNewBranchFormTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private data class Submit(val name: String, val startPoint: String?, val checkout: Boolean)

    private val items = listOf(
        GitItem(
            kind = GitItem.KIND_BRANCH_WITH_WORKTREE,
            branch = "main",
            worktree_path = "/repo",
            is_current = true,
        ),
        GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "feature-a"),
        GitItem(kind = GitItem.KIND_ORPHAN_WORKTREE, worktree_path = "/repo/wt/orphan"),
        GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/feature-x", remote = "origin"),
    )

    private fun setContent(
        state: NewBranchSheetState = NewBranchSheetState(),
        items: List<GitItem> = this.items,
        onDismiss: () -> Unit = {},
        onCreate: (Submit) -> Unit = {},
    ) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                GitNewBranchForm(
                    state = state,
                    items = items,
                    projectName = "alpha",
                    onDismiss = onDismiss,
                    onCreate = { name, startPoint, checkout ->
                        onCreate(Submit(name, startPoint, checkout))
                    },
                )
            }
        }
    }

    private fun submit() = composeRule.onNodeWithTag(GIT_NEW_BRANCH_SUBMIT_TAG)
    private fun name() = composeRule.onNodeWithTag(GIT_NEW_BRANCH_NAME_TAG)
    private fun startPoint() = composeRule.onNodeWithTag(GIT_NEW_BRANCH_START_TAG)

    @Test
    fun theSubmitButton_waitsForAName_thenHandsOverEveryField() {
        val submits = mutableListOf<Submit>()
        setContent(onCreate = { submits += it })

        composeRule.onNodeWithText("Check out in the project folder of alpha").assertIsDisplayed()
        startPoint().assertTextContains("main")
        submit().assertIsNotEnabled()

        name().performTextInput("feature/new")
        composeRule.onNodeWithTag(GIT_NEW_BRANCH_CHECKOUT_TAG).performClick()
        submit().assertIsEnabled().performClick()

        assertEquals(listOf(Submit("feature/new", "main", checkout = true)), submits)
    }

    @Test
    fun withoutACheckedOutBranch_theStartPointIsHead_andIsSentAsNull() {
        val submits = mutableListOf<Submit>()
        setContent(items = items.drop(1), onCreate = { submits += it })

        startPoint().assertTextContains("HEAD (current commit)")
        name().performTextInput("feature/new")
        submit().performClick()

        assertEquals(listOf(Submit("feature/new", null, checkout = false)), submits)
    }

    @Test
    fun aBlankName_cannotBeSubmitted() {
        setContent()

        name().performTextInput("   ")

        submit().assertIsNotEnabled()
    }

    @Test
    fun whileCreating_theFieldsAndButtonsAreLocked() {
        var dismissed = 0
        setContent(NewBranchSheetState(creating = true), onDismiss = { dismissed++ })

        submit().assertTextContains("Creating…", ignoreCase = true).assertIsNotEnabled()
        name().assertIsNotEnabled()
        startPoint().assertIsNotEnabled()
        composeRule.onNodeWithTag(GIT_NEW_BRANCH_CHECKOUT_TAG).assertIsNotEnabled()
        composeRule.onNodeWithText("Cancel", ignoreCase = true).assertIsNotEnabled().performClick()
        assertEquals(0, dismissed)
    }

    @Test
    fun cancel_dismisses_withoutSubmitting() {
        var dismissed = 0
        val submits = mutableListOf<Submit>()
        setContent(onDismiss = { dismissed++ }, onCreate = { submits += it })

        name().performTextInput("feature/new")
        composeRule.onNodeWithText("Cancel", ignoreCase = true).performClick()

        assertEquals(1, dismissed)
        assertEquals(emptyList<Submit>(), submits)
    }

    @Test
    fun startPointOptions_sortTheLocalAndTheRemoteRowsEachAToZ_ignoringCase() {
        val items = listOf(
            GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/Zed", remote = "origin"),
            GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "Zeta"),
            GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/apple", remote = "origin"),
            GitItem(kind = GitItem.KIND_BRANCH_WITH_WORKTREE, branch = "main", worktree_path = "/repo"),
            GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "alpha"),
        )

        assertEquals(
            listOf(
                "HEAD (current commit)",
                "alpha",
                "main",
                "Zeta",
                "origin/apple (remote)",
                "origin/Zed (remote)",
            ),
            startPointOptions(items).map { it.label },
        )
    }

    @Test
    fun startPointOptions_skipAnOrphanWorktree_evenWhenItStillNamesItsDeletedBranch() {
        val items = listOf(
            GitItem(
                kind = GitItem.KIND_ORPHAN_WORKTREE,
                branch = "gone",
                worktree_path = "/repo/wt/gone",
                detached = false,
            ),
            GitItem(kind = GitItem.KIND_BRANCH_ONLY, branch = "feature-a"),
            GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/feature-x", remote = "origin"),
        )

        assertEquals(
            listOf(
                null to "HEAD (current commit)",
                "feature-a" to "feature-a",
                "origin/feature-x" to "origin/feature-x (remote)",
            ),
            startPointOptions(items).map { it.value to it.label },
        )
    }
}

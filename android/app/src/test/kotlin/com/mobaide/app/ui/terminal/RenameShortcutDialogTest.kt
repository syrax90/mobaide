package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [RenameShortcutDialog] — the only naming surface for saved
 * shortcuts, reached from the picker's saved-hint row and from an InputBar
 * long-press.
 *
 * Covered contracts: the name field clamps to [MAX_SHORTCUT_NAME_LENGTH] so a
 * long paste cannot be rejected by the server with a 422 after the fact; the
 * dialog emits the field verbatim — an emptied field yields "", the sentinel
 * that drops the custom name back to the chord label, and whitespace is passed
 * through untrimmed because collapsing it is
 * [TerminalViewModel.renameShortcut]'s job, not the dialog's; and the dialog
 * shows the CHORD (not the button label) so the target is identifiable, with
 * the current name pre-filled so a straight confirm is a no-op rather than a
 * silent clear.
 *
 * Unlike the ModalBottomSheet pickers ([TemplatesPickerSheetTest],
 * [ShortcutPickerContentTest]), this dialog can be driven with real clicks:
 * Robolectric's input dispatcher does reach an AlertDialog's window, so these
 * are full interaction tests rather than assertion-only smoke tests.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class RenameShortcutDialogTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val unnamed = SavedShortcutUi(
        id = "s1",
        chord = KeyChord(ctrl = true, key = "x"),
        name = null,
        risky = false,
    )

    private val named = unnamed.copy(id = "s2", name = "Cut")

    private fun setContent(
        shortcut: SavedShortcutUi,
        onRename: (String) -> Unit = {},
        onDismiss: () -> Unit = {},
    ) {
        composeRule.setContent {
            RenameShortcutDialog(
                shortcut = shortcut,
                onRename = onRename,
                onDismiss = onDismiss,
            )
        }
    }

    /** The dialog holds exactly one text field, so no test tag is needed. */
    private fun nameField() = composeRule.onNode(hasSetTextAction())

    @Test
    fun longPaste_clampsToServerNameCap() {
        // The server rejects names over MAX_SHORTCUT_NAME_LENGTH, and the typed
        // text would be lost with the failed request — the field clamps up
        // front so the confirmed name is exactly the 100-char prefix.
        val renamed = mutableListOf<String>()
        setContent(unnamed, onRename = { renamed += it })

        nameField().performTextInput("n".repeat(MAX_SHORTCUT_NAME_LENGTH + 50))
        composeRule.onNodeWithText("Rename").performClick()

        assertEquals(listOf("n".repeat(MAX_SHORTCUT_NAME_LENGTH)), renamed)
    }

    @Test
    fun clearingTheName_confirmsWithEmptyStringSentinel() {
        val renamed = mutableListOf<String>()
        setContent(named, onRename = { renamed += it })

        nameField().performTextClearance()
        composeRule.onNodeWithText("Rename").performClick()

        assertEquals(
            "an emptied field must emit \"\" — the sentinel that clears the name",
            listOf(""),
            renamed,
        )
    }

    @Test
    fun whitespaceOnlyName_isEmittedUntrimmed_forTheViewModelToCollapse() {
        // The dialog is deliberately not the trimming layer: it hands the raw
        // field over and TerminalViewModel.renameShortcut collapses blank to "".
        val renamed = mutableListOf<String>()
        setContent(unnamed, onRename = { renamed += it })

        nameField().performTextInput("   ")
        composeRule.onNodeWithText("Rename").performClick()

        assertEquals(listOf("   "), renamed)
    }

    @Test
    fun showsChord_andCancelDismissesWithoutRenaming() {
        // The chord, not the label: with a custom name set, the chord is the
        // only thing identifying which button is being renamed.
        val renamed = mutableListOf<String>()
        var dismissed = 0
        setContent(named, onRename = { renamed += it }, onDismiss = { dismissed++ })

        composeRule.onNodeWithText("Ctrl+X").assertExists()

        composeRule.onNodeWithText("Cancel").performClick()

        assertEquals(1, dismissed)
        assertEquals(
            "cancelling must never write a name",
            emptyList<String>(),
            renamed,
        )
    }
}

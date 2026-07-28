package com.mobaide.app.ui.sessions

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [CustomAgentTypeEditorSheet]. Like the other ModalBottomSheet
 * surfaces ([TemplatesPickerSheetTest]), Robolectric's input dispatcher does
 * not reach the sheet's window, so these are assertion-only smoke tests over
 * the semantics tree: create-vs-edit titling and prefill, the blank-field /
 * saving Save-button gating, and the inline error slot. The save/trim/validate
 * behavior itself is covered in [CreateSessionViewModelTest].
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class CustomAgentTypeEditorSheetTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val target = CustomAgentTypeDto(
        id = "ab12cd34ef56",
        name = "Aider",
        command = "aider --model sonnet",
        created_at = "2026-07-28T12:00:00Z",
        updated_at = "2026-07-28T12:00:00Z",
    )

    private fun setContent(
        target: CustomAgentTypeDto? = null,
        error: String? = null,
        saving: Boolean = false,
    ) {
        composeRule.setContent {
            CustomAgentTypeEditorSheet(
                target = target,
                error = error,
                saving = saving,
                onSave = { _, _ -> },
                onDismiss = {},
            )
        }
    }

    @Test
    fun createMode_showsNewTitle_andDisablesSaveWhileFieldsBlank() {
        setContent(target = null)

        composeRule.onNodeWithText("New custom agent").assertExists()
        composeRule.onNodeWithTag("custom_type_name_field").assertExists()
        composeRule.onNodeWithTag("custom_type_command_field").assertExists()
        // Blank name + command -> Save must be gated off; the ViewModel-level
        // validation is only the second line of defense.
        composeRule.onNodeWithTag("custom_type_save_button").assertIsNotEnabled()
        composeRule.onNodeWithTag("custom_type_editor_error").assertDoesNotExist()
    }

    @Test
    fun editMode_prefillsTargetFields_andEnablesSave() {
        setContent(target = target)

        composeRule.onNodeWithText("Edit custom agent").assertExists()
        composeRule.onNodeWithTag("custom_type_name_field")
            .assertTextContains("Aider")
        composeRule.onNodeWithTag("custom_type_command_field")
            .assertTextContains("aider --model sonnet")
        composeRule.onNodeWithTag("custom_type_save_button").assertIsEnabled()
    }

    @Test
    fun serverError_rendersInline_inTheErrorSlot() {
        setContent(
            target = target,
            error = "A custom agent type with this name already exists",
        )

        composeRule.onNodeWithTag("custom_type_editor_error")
            .assertTextContains("A custom agent type with this name already exists")
    }

    @Test
    fun saving_disablesSaveButton_andShowsProgressLabel() {
        setContent(target = target, saving = true)

        composeRule.onNodeWithTag("custom_type_save_button").assertIsNotEnabled()
        composeRule.onNodeWithText("Saving…").assertExists()
    }
}

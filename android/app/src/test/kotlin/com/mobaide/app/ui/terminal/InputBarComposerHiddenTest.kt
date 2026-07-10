package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.text.input.TextFieldValue
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [InputBar]'s user-controlled composer hide/reveal surface:
 *   - `composerVisible = false` removes the text field and every composer action
 *     button (Templates, Save as template, panel toggle, Expand editor, Send) while
 *     keeping the control-key bar mounted without needing the panel toggle.
 *   - A labeled reveal chip (`reveal_composer_chip`) is docked at the control-key
 *     bar whenever the composer is hidden, and routes taps to `onRevealComposer`.
 *   - A "Show keyboard" icon button (`raise_terminal_keyboard_button`) sits beside
 *     it and routes taps to `onRaiseTerminalKeyboard` (re-raises the terminal IME).
 *   - Both affordances are absent while the composer is visible.
 *   - A pending `requestFocus` is deferred while the composer is hidden and consumed
 *     on the frame that mounts the field.
 *
 * Robolectric SDK pinned to 33 per project precedent; portrait qualifiers.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class InputBarComposerHiddenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun setInputBar(
        composerVisible: Boolean,
        enabled: Boolean = true,
        onRevealComposer: () -> Unit = {},
        onRaiseTerminalKeyboard: () -> Unit = {},
    ) {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                    enabled = enabled,
                    composerVisible = composerVisible,
                    onRevealComposer = onRevealComposer,
                    onRaiseTerminalKeyboard = onRaiseTerminalKeyboard,
                )
            }
        }
    }

    @Test
    fun composerHidden_hidesTextFieldAndAllActionButtons() {
        setInputBar(composerVisible = false)

        composeRule.onNodeWithText("Type command...").assertDoesNotExist()
        listOf(
            "Hide command field", "Templates", "Save as template", "Show keys",
            "Expand editor", "Send",
        ).forEach { composeRule.onNodeWithContentDescription(it).assertDoesNotExist() }
    }

    @Test
    fun composerHidden_controlKeysVisible_withoutToggling() {
        setInputBar(composerVisible = false)

        // The panel toggle lives among the hidden action buttons, so the bar must be
        // force-mounted: Esc (text key) and Backspace (icon key) both present.
        composeRule.onNodeWithText("Esc").assertExists()
        composeRule.onNodeWithContentDescription("Backspace").assertExists()
    }

    @Test
    fun composerHidden_showsRevealChip_tapInvokesOnRevealComposer() {
        var reveals = 0
        setInputBar(composerVisible = false, onRevealComposer = { reveals++ })

        composeRule.onNodeWithTag("reveal_composer_chip")
            .assertExists()
            .assertHasClickAction()
            .performClick()

        assertEquals(1, reveals)
    }

    @Test
    fun composerHidden_showsKeyboardButton_tapInvokesOnRaiseTerminalKeyboard() {
        var raises = 0
        setInputBar(composerVisible = false, onRaiseTerminalKeyboard = { raises++ })

        composeRule.onNodeWithTag("raise_terminal_keyboard_button")
            .assertExists()
            .assertHasClickAction()
            .performClick()

        assertEquals(1, raises)
    }

    @Test
    fun composerHidden_keyboardButtonDisabled_dropsClicks() {
        var raises = 0
        setInputBar(
            composerVisible = false,
            enabled = false,
            onRaiseTerminalKeyboard = { raises++ },
        )

        // Same connectivity gate as the composer: while the session cannot accept
        // input, the button must not raise an IME whose keystrokes would be dropped.
        composeRule.onNodeWithTag("raise_terminal_keyboard_button")
            .assertIsNotEnabled()
            .performClick()

        assertEquals(0, raises)
    }

    @Test
    fun composerVisible_hidesRevealChipAndKeyboardButton() {
        setInputBar(composerVisible = true)

        // Even with the control panel opened, both affordances only exist while hidden.
        composeRule.onNodeWithContentDescription("Show keys").performClick()
        composeRule.onNodeWithTag("reveal_composer_chip").assertDoesNotExist()
        composeRule.onNodeWithTag("raise_terminal_keyboard_button").assertDoesNotExist()
    }

    @Test
    fun focusRequest_deferredWhileHidden_consumedOnReveal() {
        var focusHandled = 0
        val composerVisible = mutableStateOf(false)
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                    requestFocus = true,
                    onFocusHandled = { focusHandled++ },
                    composerVisible = composerVisible.value,
                )
            }
        }

        composeRule.waitForIdle()
        // The field is not composed while hidden: the request must wait for the frame
        // that mounts it (an ungated FocusRequester.requestFocus() here would throw).
        assertEquals(0, focusHandled)

        composerVisible.value = true
        composeRule.waitForIdle()

        assertEquals(1, focusHandled)
    }
}

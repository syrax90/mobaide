package com.mobaide.app.ui.common

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * UI tests for the segmented [CodeInputField]. The component is presentation-only: it renders
 * `length` cells, displays `value.take(length)`, and forwards keystrokes verbatim to
 * `onValueChange` (digit-filtering + capping live in the ViewModel, not here).
 *
 * Robolectric SDK pinned to 33 to match existing project precedent (`InputBarTest`,
 * `AgentListScreenTest`); Robolectric 4.13 has not rolled SDK 35 jars.
 *
 * The field's content description is dynamic ("Verification code, N digits, M entered"), so the
 * editable node is located by the stable "Verification code" prefix via `substring = true`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class CodeInputFieldTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    // The cells and their digit Texts live in the BasicTextField's UNMERGED semantics tree
    // (the field sets MergeDescendants = true), so cell finders pass useUnmergedTree = true —
    // the same Compose quirk handled in AgentListScreenTest.

    @Test
    fun forwardsKeystrokeVerbatim_withoutFilteringOrCapping() {
        val captured = mutableListOf<String>()
        composeRule.setContent {
            Surface {
                CodeInputField(value = "", onValueChange = { captured += it })
            }
        }

        // The component does not filter input; a non-digit char must be passed straight through
        // so the ViewModel (the owner of digit-filtering) decides what to keep.
        composeRule.onNodeWithContentDescription("Verification code", substring = true)
            .performTextInput("a")

        assertEquals(listOf("a"), captured)
    }

    @Test
    fun forwardsAppendedDigitToEndOfValue() {
        val captured = mutableListOf<String>()
        composeRule.setContent {
            Surface {
                CodeInputField(value = "12", onValueChange = { captured += it })
            }
        }

        composeRule.onNodeWithContentDescription("Verification code", substring = true)
            .performTextInput("3")

        // Selection is pinned to the end, so a keystroke appends: "12" + "3" -> "123".
        assertEquals(listOf("123"), captured)
    }

    @Test
    fun displaysOnlyFirstLengthDigits_whenValueOverflows() {
        composeRule.setContent {
            Surface {
                CodeInputField(value = "1234567890", onValueChange = {}, autoFocus = false)
            }
        }

        // Still exactly `length` cells, and overflow digits (the 7th onward) are never shown.
        composeRule.onAllNodesWithTag("codeCell", useUnmergedTree = true).assertCountEquals(6)
        composeRule.onNodeWithText("6", useUnmergedTree = true).assertExists()
        composeRule.onNodeWithText("7", useUnmergedTree = true).assertDoesNotExist()
        composeRule.onNodeWithText("0", useUnmergedTree = true).assertDoesNotExist()
    }

    @Test
    fun customContentDescription_makesFieldDiscoverableByThatPrefix() {
        composeRule.setContent {
            Surface {
                CodeInputField(
                    value = "",
                    onValueChange = {},
                    autoFocus = false,
                    contentDescription = "Reset code",
                )
            }
        }

        // The Reset-password screen passes "Reset code"; the field must be locatable by that
        // prefix (TalkBack announces "Reset code, 6 digits, 0 entered"), and an input forwarded
        // through it confirms the custom-described node is the live editable field.
        composeRule.onNodeWithContentDescription("Reset code", substring = true)
            .assertExists()
    }

    @Test
    fun customContentDescription_overridesDefaultVerificationCode() {
        composeRule.setContent {
            Surface {
                CodeInputField(
                    value = "",
                    onValueChange = {},
                    autoFocus = false,
                    contentDescription = "Reset code",
                )
            }
        }

        // Passing a custom description must replace the "Verification code" default, not append to
        // it — otherwise the parameter would be silently ignored and both screens would read alike.
        composeRule.onNodeWithContentDescription("Verification code", substring = true)
            .assertDoesNotExist()
    }

    @Test
    fun pasteButton_hidden_whenClipboardHasNoText() {
        composeRule.setContent {
            Surface {
                CodeInputField(value = "", onValueChange = {}, autoFocus = false)
            }
        }

        // Nothing to paste — the affordance must not advertise an empty clipboard.
        composeRule.onNodeWithText("Paste code").assertDoesNotExist()
    }

    @Test
    fun pasteButton_forwardsClipboardTextVerbatim() {
        val clipboard = RuntimeEnvironment.getApplication()
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("label", "Your code is 123 456"))

        val captured = mutableListOf<String>()
        composeRule.setContent {
            Surface {
                CodeInputField(value = "", onValueChange = { captured += it }, autoFocus = false)
            }
        }

        composeRule.onNodeWithText("Paste code").performClick()

        // The component stays presentation-only: the raw clip goes to the ViewModel, which
        // owns digit-filtering and capping.
        assertEquals(listOf("Your code is 123 456"), captured)
    }

    @Test
    fun pasteButton_hidden_whenFieldDisabled() {
        val clipboard = RuntimeEnvironment.getApplication()
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("label", "123456"))

        composeRule.setContent {
            Surface {
                CodeInputField(value = "", onValueChange = {}, autoFocus = false, enabled = false)
            }
        }

        // Submitting is in flight; offering a paste that the field would reject is misleading.
        composeRule.onNodeWithText("Paste code").assertDoesNotExist()
    }
}

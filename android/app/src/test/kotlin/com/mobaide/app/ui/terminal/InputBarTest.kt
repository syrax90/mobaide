package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.text.input.TextFieldValue
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [InputBar] covering the buttons-refactor:
 *   - Orientation-aware layout (portrait stacks; landscape inline).
 *   - The single horizontally scrollable control-key panel exposes exactly
 *     the 9 keys `Esc, Tab, S+Tab, Enter, Ctrl+C, ↑, ↓, ←, →` — with
 *     `Ctrl+L`/`Ctrl-D` removed and the `Ctrl-C`/`S-Tab` labels renamed.
 *   - Byte payloads for renamed keys are unchanged (display-only rename).
 *
 * Robolectric SDK pinned to 33 to match existing project precedent
 * (`AgentListScreenTest`). The `qualifiers` differ per test class to
 * drive the `LocalConfiguration.current.orientation` branch in `InputBar`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class InputBarPortraitTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun textField_andAllFiveActionIcons_areDisplayedInPortrait() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue("hi"),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithText("hi").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Templates").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Save as template").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Show keys").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Expand editor").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Send").assertIsDisplayed()
    }

    @Test
    fun sendIcon_routesClickToOnSendCallback_withTrimmedText() {
        val captured = mutableListOf<String>()
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue("ls -la\n"),
                    onTextChange = {},
                    onSend = { captured += it },
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription("Send")
            .assertHasClickAction()
            .performClick()

        assertEquals(listOf("ls -la"), captured)
    }

    @Test
    fun controlKeyPanel_togglesOpenAndExposesExactly9Keys_inOrder() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithText("Esc").assertDoesNotExist()
        composeRule.onNodeWithContentDescription("Show keys").performClick()

        // `assertExists` (not `assertIsDisplayed`) because the keys live in a
        // horizontalScroll Row — at narrow portrait widths the rightmost
        // arrow keys are intentionally off-viewport and scrolled into view.
        // Existence in the semantics tree is the right "all 9 keys present"
        // assertion; viewport visibility is the user's scroll concern.
        listOf("Esc", "Tab", "S+Tab", "Enter", "Ctrl+C", "↑", "↓", "←", "→")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
    }

    @Test
    fun controlKeyPanel_doesNotExposeRemovedOrOldLabels() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()

        listOf("Ctrl+L", "Ctrl-D", "Ctrl-C", "S-Tab")
            .forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
    }

    @Test
    fun ctrlPlusC_sendsSigintByte_0x03_unchangedByLabelRename() {
        val captured = mutableListOf<ByteArray>()
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = { captured += it },
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()
        composeRule.onNodeWithText("Ctrl+C").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x03), captured.single())
    }

    @Test
    fun sPlusTab_sendsCsiZ_escBracketZ_unchangedByLabelRename() {
        val captured = mutableListOf<ByteArray>()
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = { captured += it },
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()
        composeRule.onNodeWithText("S+Tab").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(
            byteArrayOf(0x1b, '['.code.toByte(), 'Z'.code.toByte()),
            captured.single(),
        )
    }

    @Test
    fun showKeys_descriptionFlipsToHideKeys_whenPanelExpanded() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()
        composeRule.onNodeWithContentDescription("Hide keys").assertIsDisplayed()
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w800dp-h360dp-land-xxhdpi")
class InputBarLandscapeTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun landscape_rendersTextField_andAllFiveActionIcons() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue("ok"),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithText("ok").assertIsDisplayed()
        listOf("Templates", "Save as template", "Show keys", "Expand editor", "Send")
            .forEach { composeRule.onNodeWithContentDescription(it).assertIsDisplayed() }
    }

    @Test
    fun landscape_controlKeyPanel_exposesSame9Keys_asPortrait() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()

        // Same `assertExists` rationale as portrait: the row is horizontally
        // scrollable; we assert the 9 keys are present in the composition,
        // not that every one fits on screen at every width.
        listOf("Esc", "Tab", "S+Tab", "Enter", "Ctrl+C", "↑", "↓", "←", "→")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
    }
}

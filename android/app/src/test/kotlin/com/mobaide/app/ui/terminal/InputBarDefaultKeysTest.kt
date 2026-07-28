package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.text.input.TextFieldValue
import com.mobaide.app.data.network.dto.SessionKind
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Kind-neutral control-key set (proto-v7): a null `sessionKind` (kind not yet
 * resolved) and [SessionKind.Custom] must both render exactly the 9 default
 * keys `Enter, ↑, ↓, ←, →, Ctrl+C, Esc, Tab, Backspace` plus the custom-
 * shortcut "…" trigger. `Mode`/`Rewind`/`Ctrl+O`/`Ctrl+R`/`Ctrl+L` carry
 * Claude-TUI meanings an arbitrary CLI may bind differently and must never
 * leak in; neither may the Codex/Antigravity-only keys. [SessionKind.Shell]
 * deliberately keeps the Claude set (known oddity) — pinned here so a silent
 * change surfaces.
 *
 * Mirrors [InputBarCodexTest]: Robolectric SDK 33, portrait qualifiers.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class InputBarDefaultKeysTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val claudeOnlyKeys = listOf("Mode", "Rewind", "Ctrl+O", "Ctrl+R", "Ctrl+L")
    private val otherCliKeys = listOf("Approve", "Reject", "Ctrl+K", "Ctrl+G", "Ctrl+D")

    private fun openPanel(kind: SessionKind?) {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                    sessionKind = kind,
                )
            }
        }
        composeRule.onNodeWithContentDescription("Show keys").performClick()
    }

    private fun assertExactlyDefaultKeys() {
        listOf("Enter", "Ctrl+C", "Esc", "Tab")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        // Arrows and Backspace render as Material icons -> contentDescription.
        listOf("↑", "↓", "←", "→", "Backspace")
            .forEach { composeRule.onNodeWithContentDescription(it).assertExists() }
        (claudeOnlyKeys + otherCliKeys)
            .forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
        // The "…" saved-shortcut trigger is the escape hatch for everything
        // the neutral set omits — it must stay present.
        composeRule.onNodeWithContentDescription("Custom shortcut").assertExists()
    }

    @Test
    fun nullKind_rendersExactlyTheNineDefaultKeys_plusShortcutTrigger() {
        // While the kind is unresolved the bar must not guess Claude — its
        // Mode/Rewind escape codes are wrong input for other TUIs.
        openPanel(kind = null)
        assertExactlyDefaultKeys()
    }

    @Test
    fun customKind_rendersExactlyTheNineDefaultKeys_plusShortcutTrigger() {
        openPanel(kind = SessionKind.Custom("custom_ab12cd34ef56"))
        assertExactlyDefaultKeys()
    }

    @Test
    fun shellKind_deliberatelyKeepsClaudeSet_untilCla158() {
        openPanel(kind = SessionKind.Shell)

        composeRule.onNodeWithText("Mode").assertExists()
        composeRule.onNodeWithText("Ctrl+L").assertExists()
        otherCliKeys.forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
    }
}

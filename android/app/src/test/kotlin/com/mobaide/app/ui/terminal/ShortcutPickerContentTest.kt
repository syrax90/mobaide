package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [ShortcutPickerContent] — the sheet body is tested directly
 * because Robolectric's input dispatcher cannot deliver Compose-test synthetic
 * clicks into the ModalBottomSheet popup window (see [TemplatesPickerSheetTest]);
 * a single assertion-only smoke test covers the full [ShortcutPickerSheet].
 *
 * Covered contracts: recents chips send immediately and the section hides when
 * empty; Send stays disabled until a base key is selected (tap-again deselects);
 * the third modifier chip disables once two are on and re-enables on deselect;
 * the live preview shows the exact wire summary, the same-byte disclosure for
 * legacy collisions (Ctrl+I = Tab), and the risky hint only for CSI-u chords.
 *
 * Screen qualifiers are taller than the defaults so the pinned footer (preview,
 * hint, Send) lays out on-screen below the 420dp scrollable key area.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w480dp-h1200dp-port-xxhdpi")
class ShortcutPickerContentTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun setContent(
        recents: List<KeyChord> = emptyList(),
        onSendChord: (KeyChord) -> Unit = {},
    ) {
        composeRule.setContent {
            Surface {
                ShortcutPickerContent(
                    recents = recents,
                    onSendChord = onSendChord,
                    onClose = {},
                )
            }
        }
    }

    private fun toggleModifier(tag: String) {
        composeRule.onNodeWithTag(tag).performScrollTo().performClick()
    }

    private fun tapKey(label: String) {
        // The chip is matched by text AND click action: once a key is selected
        // the footer preview renders the same label as a plain text node, which
        // would make a text-only matcher ambiguous.
        composeRule.onNode(hasText(label) and hasClickAction())
            .performScrollTo()
            .performClick()
    }

    @Test
    fun recentsChip_tapSendsChordImmediately() {
        val captured = mutableListOf<KeyChord>()
        val recent = KeyChord(ctrl = true, key = "r")
        setContent(recents = listOf(recent), onSendChord = { captured += it })

        composeRule.onNodeWithText("Recents").assertExists()
        composeRule.onNodeWithText("Ctrl+R").performScrollTo().performClick()

        assertEquals(listOf(recent), captured)
    }

    @Test
    fun emptyRecents_omitsRecentsSection() {
        setContent(recents = emptyList())

        composeRule.onNodeWithText("Recents").assertDoesNotExist()
    }

    @Test
    fun sendButton_enabledOnlyWhileAKeyIsSelected() {
        setContent()

        composeRule.onNodeWithTag("shortcut_send_button").assertIsNotEnabled()
        composeRule.onNodeWithText("Pick a key").assertExists()

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_send_button").assertIsEnabled()

        // Tap-again deselects the key, so Send must disarm again.
        tapKey("X")
        composeRule.onNodeWithTag("shortcut_send_button").assertIsNotEnabled()
    }

    @Test
    fun csiUChord_showsWireSummary_riskyHint_andSendCapturesChord() {
        val captured = mutableListOf<KeyChord>()
        setContent(onSendChord = { captured += it })

        toggleModifier("shortcut_mod_ctrl")
        toggleModifier("shortcut_mod_shift")
        tapKey("I")

        composeRule.onNodeWithText("Sends: ESC [ 105 ; 6 u", substring = true).assertExists()
        composeRule.onNodeWithTag("shortcut_risky_hint").assertExists()

        composeRule.onNodeWithTag("shortcut_send_button").performClick()
        assertEquals(listOf(KeyChord(ctrl = true, shift = true, key = "i")), captured)
    }

    @Test
    fun legacyCollisionChord_showsSameByteDisclosure_withoutRiskyHint() {
        setContent()

        toggleModifier("shortcut_mod_ctrl")
        tapKey("I")

        composeRule.onNodeWithText("same byte as Tab", substring = true).assertExists()
        composeRule.onNodeWithTag("shortcut_risky_hint").assertDoesNotExist()
    }

    @Test
    fun thirdModifier_disablesAtTwo_andReenablesOnDeselect() {
        setContent()

        toggleModifier("shortcut_mod_ctrl")
        toggleModifier("shortcut_mod_alt")

        composeRule.onNodeWithTag("shortcut_mod_shift").assertIsNotEnabled()
        // Selected chips stay enabled so they can be toggled back off.
        composeRule.onNodeWithTag("shortcut_mod_ctrl").assertIsEnabled()
        composeRule.onNodeWithTag("shortcut_mod_alt").assertIsEnabled()

        toggleModifier("shortcut_mod_alt")
        composeRule.onNodeWithTag("shortcut_mod_shift").assertIsEnabled()
    }

    @Test
    fun fullSheet_rendersHeaderAcrossPopupBoundary() {
        composeRule.setContent {
            Surface {
                ShortcutPickerSheet(
                    recents = emptyList(),
                    onSendChord = {},
                    onDismiss = {},
                )
            }
        }

        composeRule.onNodeWithText("Keyboard shortcut").assertExists()
    }
}

/**
 * Short-screen counterpart to [ShortcutPickerContentTest], whose qualifiers are
 * deliberately tall. On a landscape-height viewport the scrollable key grid is
 * weighted with `fill = false` so the footer is measured before the grid;
 * without that weight the 420dp-capped grid consumed the entire sheet height
 * and the Send button was measured off-screen and unreachable.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w800dp-h360dp-land-xxhdpi")
class ShortcutPickerContentShortScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun shortLandscapeViewport_sendButtonRemainsDisplayed() {
        composeRule.setContent {
            Surface {
                ShortcutPickerContent(
                    recents = emptyList(),
                    onSendChord = {},
                    onClose = {},
                )
            }
        }

        composeRule.onNodeWithTag("shortcut_send_button").assertIsDisplayed()
    }
}

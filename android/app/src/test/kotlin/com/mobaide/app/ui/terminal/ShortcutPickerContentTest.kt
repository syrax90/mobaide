package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
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
 * legacy collisions (Ctrl+I = Tab), and the risky hint only for CSI-u chords;
 * with `enabled = false` (session gate) the send surfaces — recents chips and
 * Send — are inert while the modifier/key grid stays composable.
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
        enabled: Boolean = true,
    ) {
        composeRule.setContent {
            Surface {
                ShortcutPickerContent(
                    recents = recents,
                    onSendChord = onSendChord,
                    onClose = {},
                    enabled = enabled,
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
    fun disabledPicker_recentsChipIsInert() {
        val captured = mutableListOf<KeyChord>()
        val recent = KeyChord(ctrl = true, key = "r")
        setContent(recents = listOf(recent), onSendChord = { captured += it }, enabled = false)

        val chip = composeRule.onNodeWithText("Ctrl+R").performScrollTo()
        chip.assertIsNotEnabled()

        chip.performClick()
        assertEquals(emptyList<KeyChord>(), captured)
    }

    @Test
    fun disabledPicker_composingStaysLive_butSendStaysDisabled() {
        setContent(enabled = false)

        // The gate must not freeze composing: modifier and key chips still react.
        toggleModifier("shortcut_mod_ctrl")
        tapKey("X")
        composeRule.onNodeWithText("Sends: ", substring = true).assertExists()

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
    fun fullSheet_boxWrapper_keepsFooterOnScreen() {
        // The sheet body is wrapped in a Box (imePadding + overlaid
        // SnackbarHost). Box must forward the sheet's incoming max constraints
        // untouched, otherwise the footer-priority layout inside
        // ShortcutPickerContent measures Send off-screen — a regression the
        // content-level tests cannot see because they skip the wrapper.
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
        composeRule.onNodeWithTag("shortcut_send_button").assertIsDisplayed()
    }
}

/**
 * Short-screen counterpart to [ShortcutPickerContentTest], whose qualifiers are
 * deliberately tall. On a landscape-height viewport the scrollable key grid is
 * weighted with `fill = false` so the footer is measured before the grid;
 * without that weight the 420dp-capped grid consumed the entire sheet height
 * and the Send button was measured off-screen and unreachable. Asserted both
 * through the content directly and through the full [ShortcutPickerSheet],
 * whose Box + overlaid SnackbarHost wrapper sits between the sheet's
 * constraints and that layout.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w800dp-h360dp-land-xxhdpi")
class ShortcutPickerContentShortScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun shortLandscapeViewport_fullSheet_sendButtonRemainsDisplayed() {
        // Drives the full sheet, not just ShortcutPickerContent: the
        // sheet's Box(imePadding) + overlaid SnackbarHost wrapper is the one
        // layer that could swallow the incoming max constraints and push the
        // footer off a short viewport, where there is no slack to absorb it.
        composeRule.setContent {
            Surface {
                ShortcutPickerSheet(
                    recents = emptyList(),
                    onSendChord = {},
                    onDismiss = {},
                )
            }
        }

        composeRule.onNodeWithTag("shortcut_send_button").assertIsDisplayed()
    }
}

/**
 * Saved-shortcut surfaces inside the picker: the "Saved" InputChip section
 * below Recents (tap = resend, gated on the session; trailing ✕ = server-side
 * remove, never gated) and the footer save control — a two-state toggle whose
 * state is DERIVED from whether the composed chord already appears in
 * `savedShortcuts` ("Save" pins it, "Saved" unpins it), plus the hint row that
 * names the pinned button and routes naming to `onRenameSavedShortcut`.
 * Save/remove are server mutations, so they depend only on a key being picked —
 * not on the session-active `enabled` gate.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w480dp-h1200dp-port-xxhdpi")
class ShortcutPickerSavedSectionTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val savedRetry = SavedShortcutUi(
        id = "s1",
        chord = KeyChord(ctrl = true, key = "r"),
        name = "Retry",
        risky = false,
    )

    /** Named, and deliberately the same chord the tests compose (Ctrl+X). */
    private val savedCut = SavedShortcutUi(
        id = "s2",
        chord = KeyChord(ctrl = true, key = "x"),
        name = "Cut",
        risky = false,
    )

    /** Unnamed counterpart of [savedCut] — its label falls back to "Ctrl+X". */
    private val savedUnnamed = SavedShortcutUi(
        id = "s3",
        chord = KeyChord(ctrl = true, key = "x"),
        name = null,
        risky = false,
    )

    private fun setContent(
        savedShortcuts: List<SavedShortcutUi> = emptyList(),
        enabled: Boolean = true,
        onSendChord: (KeyChord) -> Unit = {},
        onSaveShortcut: (KeyChord) -> Unit = {},
        onRenameSavedShortcut: (SavedShortcutUi) -> Unit = {},
        onRemoveSavedShortcut: (SavedShortcutUi) -> Unit = {},
    ) {
        composeRule.setContent {
            Surface {
                ShortcutPickerContent(
                    recents = emptyList(),
                    onSendChord = onSendChord,
                    onClose = {},
                    enabled = enabled,
                    savedShortcuts = savedShortcuts,
                    onSaveShortcut = onSaveShortcut,
                    onRenameSavedShortcut = onRenameSavedShortcut,
                    onRemoveSavedShortcut = onRemoveSavedShortcut,
                )
            }
        }
    }

    private fun toggleModifier(tag: String) {
        composeRule.onNodeWithTag(tag).performScrollTo().performClick()
    }

    private fun tapKey(label: String) {
        composeRule.onNode(hasText(label) and hasClickAction())
            .performScrollTo()
            .performClick()
    }

    /** Tap the chip body with a real coordinate click on the label text node
     *  (unmerged tree: the merged chip node also spans the trailing ✕, so its
     *  center could land inside the remove target). Deliberately not a
     *  semantics-action shortcut — the real tap pins the layout contract that
     *  the ✕ owns actual 48dp bounds rather than an invisible touch-target
     *  expansion overlapping the label, which used to turn send-taps into
     *  silent destructive removes. */
    private fun tapChipBody(label: String) {
        composeRule.onNodeWithText(label, useUnmergedTree = true)
            .performScrollTo()
            .performClick()
    }

    @Test
    fun savedChip_tapIsInert_whenSessionDisabled() {
        val captured = mutableListOf<KeyChord>()
        setContent(
            savedShortcuts = listOf(savedRetry),
            enabled = false,
            onSendChord = { captured += it },
        )

        tapChipBody("Retry")

        assertEquals(emptyList<KeyChord>(), captured)
    }

    @Test
    fun savedChip_labelTap_landsOnSend_neverOnRemove() {
        val sent = mutableListOf<KeyChord>()
        val removed = mutableListOf<SavedShortcutUi>()
        setContent(
            savedShortcuts = listOf(savedRetry),
            onSendChord = { sent += it },
            onRemoveSavedShortcut = { removed += it },
        )

        tapChipBody("Retry")

        assertEquals(listOf(savedRetry.chord), sent)
        assertEquals(
            "a real tap on the label must never fall into the ✕ remove target",
            emptyList<SavedShortcutUi>(),
            removed,
        )
    }

    @Test
    fun savedChip_removeIcon_firesRemove_notSend() {
        val removed = mutableListOf<SavedShortcutUi>()
        val sent = mutableListOf<KeyChord>()
        setContent(
            savedShortcuts = listOf(savedRetry),
            onSendChord = { sent += it },
            onRemoveSavedShortcut = { removed += it },
        )

        composeRule.onNodeWithContentDescription("Remove Retry")
            .performScrollTo()
            .performClick()

        assertEquals(listOf(savedRetry), removed)
        assertEquals(
            "the ✕ must remove, never also resend the chord",
            emptyList<KeyChord>(),
            sent,
        )
    }

    @Test
    fun savedChip_removeIcon_staysActive_whenSessionDisabled() {
        // Removal is a server mutation, not a send — the session gate must
        // not block it.
        val removed = mutableListOf<SavedShortcutUi>()
        setContent(
            savedShortcuts = listOf(savedRetry),
            enabled = false,
            onRemoveSavedShortcut = { removed += it },
        )

        composeRule.onNodeWithContentDescription("Remove Retry")
            .performScrollTo()
            .performClick()

        assertEquals(listOf(savedRetry), removed)
    }

    @Test
    fun saveToggle_oneTap_savesChord_andEmitsNothingElse_evenWhenSessionDisabled() {
        // One tap is the whole save: no name gate, no confirm step. The
        // session gate must not reach it either — pinning a shortcut is a
        // server mutation, not a PTY send, so it stays live while stopped.
        val saved = mutableListOf<KeyChord>()
        val removed = mutableListOf<SavedShortcutUi>()
        val sent = mutableListOf<KeyChord>()
        setContent(
            enabled = false,
            onSendChord = { sent += it },
            onSaveShortcut = { saved += it },
            onRemoveSavedShortcut = { removed += it },
        )

        toggleModifier("shortcut_mod_ctrl")
        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()

        assertEquals(listOf(KeyChord(ctrl = true, key = "x")), saved)
        assertEquals(emptyList<SavedShortcutUi>(), removed)
        assertEquals(emptyList<KeyChord>(), sent)
    }

    @Test
    fun composedChordAlreadyPinned_showsSavedToggle_andHintNamingTheButton() {
        setContent(savedShortcuts = listOf(savedRetry, savedCut))

        composeRule.onNodeWithTag("shortcut_saved_hint").assertDoesNotExist()

        toggleModifier("shortcut_mod_ctrl")
        tapKey("X")

        composeRule.onNodeWithTag("shortcut_save_button").assertTextEquals("Saved")
        composeRule.onNodeWithTag("shortcut_saved_hint").assertExists()
        composeRule.onNodeWithText("Saved as \"Cut\"").assertExists()
    }

    @Test
    fun composedChordNotPinned_whileOtherShortcutsExist_staysInSaveState() {
        // The toggle is matched per-chord, not "the saved list is non-empty":
        // Ctrl+Y is unsaved even though two other shortcuts are.
        setContent(savedShortcuts = listOf(savedRetry, savedCut))

        toggleModifier("shortcut_mod_ctrl")
        tapKey("Y")

        composeRule.onNodeWithTag("shortcut_save_button").assertTextEquals("Save")
        composeRule.onNodeWithTag("shortcut_saved_hint").assertDoesNotExist()
    }

    @Test
    fun savedToggle_tap_unpinsTheMatchingShortcut_andNeverSaves() {
        val saved = mutableListOf<KeyChord>()
        val removed = mutableListOf<SavedShortcutUi>()
        setContent(
            savedShortcuts = listOf(savedRetry, savedCut),
            onSaveShortcut = { saved += it },
            onRemoveSavedShortcut = { removed += it },
        )

        toggleModifier("shortcut_mod_ctrl")
        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()

        assertEquals(listOf(savedCut), removed)
        assertEquals(
            "the toggle in its Saved state must never re-save the chord",
            emptyList<KeyChord>(),
            saved,
        )
    }

    @Test
    fun renameButton_unnamedShortcut_readsName_andEmitsTheMatchingShortcut() {
        val renamed = mutableListOf<SavedShortcutUi>()
        setContent(
            savedShortcuts = listOf(savedRetry, savedUnnamed),
            onRenameSavedShortcut = { renamed += it },
        )

        toggleModifier("shortcut_mod_ctrl")
        tapKey("X")

        // Unnamed buttons fall back to the chord label, so the action offers
        // to name rather than rename.
        composeRule.onNodeWithText("Saved as \"Ctrl+X\"").assertExists()
        composeRule.onNodeWithTag("shortcut_rename_button").assertTextEquals("Name")

        composeRule.onNodeWithTag("shortcut_rename_button").performClick()
        assertEquals(listOf(savedUnnamed), renamed)
    }

    @Test
    fun exactlyOneSaveLabelOnScreen_beforeAndAfterPickingAKey() {
        // The bug this replaced: pressing the footer "Save" only revealed a
        // second control also labelled "Save", so two identical Save buttons
        // were on screen and the footer one never saved. Exactly one node may
        // ever read "Save" — exact match, so the "Saved" state never counts.
        setContent()

        composeRule.onAllNodesWithText("Save").assertCountEquals(1)

        tapKey("X")

        composeRule.onAllNodesWithText("Save").assertCountEquals(1)
    }
}

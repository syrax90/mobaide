package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.remember
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
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

/**
 * Saved-shortcut surfaces inside the picker: the "Saved" InputChip section
 * below Recents (tap = resend, gated on the session; trailing ✕ = server-side
 * remove, never gated) and the Save footer flow.
 *
 * The footer is a two-state machine and the tests pin that only one "Save"
 * affordance is ever on screen: Save arms the naming state (name field +
 * Cancel + confirm) and hides both Send and itself; confirming emits
 * `onSaveShortcut(chord, name-or-null)`. Save is a server mutation, so it
 * depends only on a key being picked — not on the session-active `enabled`
 * gate. A chord that is already saved switches the surfaces to "Update name"
 * (the server upserts by chord), and the post-save notice renders only once
 * the chord actually appears in `savedShortcuts`.
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

    private fun setContent(
        savedShortcuts: List<SavedShortcutUi> = emptyList(),
        enabled: Boolean = true,
        onSendChord: (KeyChord) -> Unit = {},
        onSaveShortcut: (KeyChord, String?) -> Unit = { _, _ -> },
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

    @Test
    fun emptySaved_omitsSavedSection() {
        setContent(savedShortcuts = emptyList())

        composeRule.onNodeWithText("Saved").assertDoesNotExist()
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
    fun savedChip_tapResendsChord() {
        val captured = mutableListOf<KeyChord>()
        setContent(
            savedShortcuts = listOf(savedRetry),
            onSendChord = { captured += it },
        )

        composeRule.onNodeWithText("Saved").assertExists()
        tapChipBody("Retry")

        assertEquals(listOf(savedRetry.chord), captured)
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
    fun saveButton_enabledOnlyWhileAKeyIsSelected() {
        setContent()

        composeRule.onNodeWithTag("shortcut_save_button").assertIsNotEnabled()

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").assertIsEnabled()

        // Tap-again deselects the key, so Save must disarm again.
        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").assertIsNotEnabled()
    }

    @Test
    fun saveFlow_withTypedName_emitsChordAndTrimmedName_evenWhenSessionDisabled() {
        val captured = mutableListOf<Pair<KeyChord, String?>>()
        setContent(enabled = false, onSaveShortcut = { chord, name -> captured += chord to name })

        toggleModifier("shortcut_mod_ctrl")
        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_name_field").performTextInput("  Build  ")
        composeRule.onNodeWithTag("shortcut_save_confirm").performClick()

        assertEquals(
            listOf(KeyChord(ctrl = true, key = "x") to "Build"),
            captured,
        )
        // Confirming resets and hides the inline name row.
        composeRule.onNodeWithTag("shortcut_name_field").assertDoesNotExist()
    }

    @Test
    fun saveFlow_blankName_emitsNullName() {
        val captured = mutableListOf<Pair<KeyChord, String?>>()
        setContent(onSaveShortcut = { chord, name -> captured += chord to name })

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_save_confirm").performClick()

        assertEquals(listOf(KeyChord(key = "x") to null), captured)
    }

    @Test
    fun saveFlow_nameField_clampsLongPasteToServerCap() {
        // The server rejects names over 100 chars with a 422 (and the typed
        // text would be lost) — the field clamps a long paste up front, so
        // the confirmed name is exactly the 100-char prefix.
        val captured = mutableListOf<Pair<KeyChord, String?>>()
        setContent(onSaveShortcut = { chord, name -> captured += chord to name })

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_name_field").performTextInput("n".repeat(150))
        composeRule.onNodeWithTag("shortcut_save_confirm").performClick()

        assertEquals(listOf(KeyChord(key = "x") to "n".repeat(100)), captured)
    }

    @Test
    fun namingState_hidesSendAndTheArmingSave() {
        // The whole point of the two-state footer: while naming, the confirm is
        // the only "Save" on screen. The old inline row left the arming Save
        // sitting next to Send, where a second tap silently collapsed the row.
        setContent()

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()

        composeRule.onNodeWithTag("shortcut_save_button").assertDoesNotExist()
        composeRule.onNodeWithTag("shortcut_send_button").assertDoesNotExist()
        composeRule.onNodeWithTag("shortcut_save_confirm").assertIsDisplayed()
    }

    @Test
    fun namingState_cancel_restoresFooter_withoutSaving() {
        val captured = mutableListOf<Pair<KeyChord, String?>>()
        setContent(onSaveShortcut = { chord, name -> captured += chord to name })

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_name_field").performTextInput("Discarded")
        composeRule.onNodeWithTag("shortcut_save_cancel").performClick()

        assertEquals(emptyList<Pair<KeyChord, String?>>(), captured)
        composeRule.onNodeWithTag("shortcut_name_field").assertDoesNotExist()
        composeRule.onNodeWithTag("shortcut_send_button").assertIsDisplayed()
        composeRule.onNodeWithTag("shortcut_save_button").assertIsDisplayed()
    }

    @Test
    fun deselectingKey_whileNaming_collapsesBackToFooter() {
        setContent()

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        // Tap-again deselects the base key: the confirm would have nothing to
        // save, so the naming state must not linger.
        tapKey("X")

        composeRule.onNodeWithTag("shortcut_name_field").assertDoesNotExist()
        composeRule.onNodeWithTag("shortcut_send_button").assertIsDisplayed()
    }

    @Test
    fun alreadySavedChord_readsUpdateName_andPrefillsTheExistingName() {
        val captured = mutableListOf<Pair<KeyChord, String?>>()
        setContent(
            savedShortcuts = listOf(savedRetry),
            onSaveShortcut = { chord, name -> captured += chord to name },
        )

        toggleModifier("shortcut_mod_ctrl")
        tapKey("R")

        composeRule.onNodeWithTag("shortcut_save_button").assertTextEquals("Update name")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_name_field").assertTextContains("Retry")
        composeRule.onNodeWithTag("shortcut_save_confirm").performClick()

        assertEquals(listOf(savedRetry.chord to "Retry"), captured)
    }

    /** Drives `savedShortcuts` from a snapshot list the save callback appends
     *  to, standing in for the repository round-trip that feeds the real
     *  picker — the post-save notice is derived from that list, not from the
     *  tap. */
    private fun setContentWithLandingSaves(
        onSaveShortcut: (KeyChord, String?) -> Unit,
    ) {
        composeRule.setContent {
            val saved = remember { mutableStateListOf<SavedShortcutUi>() }
            Surface {
                ShortcutPickerContent(
                    recents = emptyList(),
                    onSendChord = {},
                    onClose = {},
                    savedShortcuts = saved,
                    onSaveShortcut = { chord, name ->
                        onSaveShortcut(chord, name)
                        saved += SavedShortcutUi(
                            id = "new-${chord.token()}",
                            chord = chord,
                            name = name,
                            risky = false,
                        )
                    },
                )
            }
        }
    }

    @Test
    fun confirmedSave_showsNotice_thenClearsOnANewChord() {
        setContentWithLandingSaves { _, _ -> }

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_name_field").performTextInput("Build")
        composeRule.onNodeWithTag("shortcut_save_confirm").performClick()

        composeRule.onNodeWithTag("shortcut_save_notice")
            .assertTextEquals("Saved — \"Build\" is now in your key bar")
        // The chord is now saved, so the arming button offers the upsert path.
        composeRule.onNodeWithTag("shortcut_save_button").assertTextEquals("Update name")

        tapKey("Y")
        composeRule.onNodeWithTag("shortcut_save_notice").assertDoesNotExist()
    }

    @Test
    fun failedSave_showsNoNotice() {
        // The notice tracks the saved list, not the tap: when the save never
        // lands (server error), the picker must not claim it did. The user
        // sees the ViewModel's failure snackbar once the sheet is dismissed.
        setContent(onSaveShortcut = { _, _ -> })

        tapKey("X")
        composeRule.onNodeWithTag("shortcut_save_button").performClick()
        composeRule.onNodeWithTag("shortcut_save_confirm").performClick()

        composeRule.onNodeWithTag("shortcut_save_notice").assertDoesNotExist()
    }
}

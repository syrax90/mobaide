package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.text.input.TextFieldValue
import com.mobaide.app.data.network.dto.SessionKind
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [InputBar] covering the CLI-aware control-key bar. Every kind renders a
 * subset of `CONTROL_KEY_ORDER`, so the shared prefix (`Enter, ↑, ↓, ←, →, Ctrl+C, Mode,
 * Ctrl+L, Esc`) keeps the same left-to-right order across kinds; only the trailing
 * kind-specific keys differ. These tests assert membership (which keys are present) plus the
 * raw-byte payloads — the exact order is pinned by the composition lists in `InputBar.kt`.
 *   - Orientation-aware layout (portrait stacks; landscape inline).
 *   - For the default/Claude set ([SessionKind.Claude]), the single horizontally
 *     scrollable control-key panel exposes exactly the 14 keys
 *     `Enter, ↑, ↓, ←, →, Ctrl+C, Mode, Ctrl+L, Esc, Rewind, Tab, Backspace, Ctrl+O, Ctrl+R`.
 *   - Byte payloads for renamed keys are unchanged (display-only rename):
 *     `S+Tab → Mode` and `Back/Edit → Rewind`; `Newline` was removed.
 *   - For [SessionKind.Codex] the panel adds the approval answers `Approve`/`Reject`
 *     after the shared prefix, with the correct raw-byte payloads; those Codex-only keys
 *     never leak into the Claude set ([InputBarCodexTest]).
 *   - For [SessionKind.Antigravity] the panel adds `Ctrl+K`, `Ctrl+G`, `Ctrl+D`
 *     after the shared prefix (no `Mode`/`Rewind`, no `Approve`/`Reject`),
 *     with the correct raw-byte payloads; Antigravity-only keys never leak into the
 *     Claude or Codex sets ([InputBarAntigravityTest]).
 *   - Every kind's row additionally ends with the custom-shortcut trigger
 *     (contentDescription `Custom shortcut`) — a non-ControlKey button that opens
 *     the chord-picker sheet via `onOpenShortcuts` and never sends bytes itself,
 *     so it is on top of (not part of) each kind's key count.
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
    fun controlKeyPanel_togglesOpenAndExposesExactly14Keys() {
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
        // horizontalScroll Row — at narrow portrait widths the rightmost keys
        // (Ctrl+O/Ctrl+R/Ctrl+L) are intentionally off-viewport and scrolled into
        // view. Existence in the semantics tree is the right "all 14 keys present"
        // assertion; viewport visibility is the user's scroll concern.
        // Arrows and Backspace render as Material icons, so they match on
        // contentDescription (below).
        listOf("Mode", "Rewind", "Esc", "Ctrl+C", "Enter", "Tab", "Ctrl+O", "Ctrl+R", "Ctrl+L")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        listOf("↑", "↓", "←", "→", "Backspace")
            .forEach { composeRule.onNodeWithContentDescription(it).assertExists() }
        // The trailing chord-picker trigger is not a ControlKey (it opens the
        // shortcut sheet instead of sending bytes) but must terminate the row.
        composeRule.onNodeWithContentDescription("Custom shortcut").assertExists()
    }

    @Test
    fun customShortcutTrigger_firesCallback_andSendsNoBytes() {
        var opens = 0
        val captured = mutableListOf<ByteArray>()
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = { captured += it },
                    onOpenShortcuts = { opens++ },
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()
        composeRule.onNodeWithContentDescription("Custom shortcut")
            .performScrollTo()
            .performClick()

        assertEquals(1, opens)
        assertTrue(
            "the trigger only opens the picker — it must never send bytes itself",
            captured.isEmpty(),
        )
    }

    @Test
    fun backspace_sendsDel_0x7f() {
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
        composeRule.onNodeWithContentDescription("Backspace").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x7f), captured.single())
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
    fun mode_sendsCsiZ_escBracketZ() {
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
        composeRule.onNodeWithText("Mode").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(
            byteArrayOf(0x1b, '['.code.toByte(), 'Z'.code.toByte()),
            captured.single(),
        )
    }

    @Test
    fun rewind_sendsEscEsc_0x1b_0x1b_inClaudeSet() {
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
        composeRule.onNodeWithText("Rewind").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x1b, 0x1b), captured.single())
    }

    @Test
    fun ctrlO_sendsShiftOut_0x0f_inClaudeSet() {
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
        composeRule.onNodeWithText("Ctrl+O").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0f), captured.single())
    }

    @Test
    fun ctrlR_sendsDeviceControl2_0x12_inClaudeSet() {
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
        composeRule.onNodeWithText("Ctrl+R").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x12), captured.single())
    }
}

/**
 * Codex control-key set: passing `sessionKind = SessionKind.Codex` adds the approval
 * answers `Approve`/`Reject` to the shared Claude set, positioned right after the fixed
 * prefix by `CONTROL_KEY_ORDER`. Verifies the Codex-only keys are present with the correct
 * raw byte payloads (sent via `onSendBytes`, the raw 'i'-frame path — never the submit
 * path), and that the Codex-only keys do not leak into the default/Claude set.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class InputBarCodexTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun openCodexPanel(onSendBytes: (ByteArray) -> Unit = {}) {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = onSendBytes,
                    sessionKind = SessionKind.Codex,
                )
            }
        }
        composeRule.onNodeWithContentDescription("Show keys").performClick()
    }

    @Test
    fun codexPanel_exposesCodexSpecificKeys_plusSharedKeys() {
        openCodexPanel()

        listOf("Approve", "Reject", "Ctrl+L")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        // Shared Claude tail; arrows and Backspace render as Material icons
        // (contentDescription below).
        listOf("Mode", "Rewind", "Esc", "Ctrl+C", "Enter", "Tab", "Ctrl+O", "Ctrl+R")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        listOf("↑", "↓", "←", "→", "Backspace")
            .forEach { composeRule.onNodeWithContentDescription(it).assertExists() }
        composeRule.onNodeWithContentDescription("Custom shortcut").assertExists()
    }

    @Test
    fun approve_sendsLiteralLowercaseY_0x79() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Approve").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf('y'.code.toByte()), captured.single())
    }

    @Test
    fun reject_sendsLiteralLowercaseN_0x6e() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Reject").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf('n'.code.toByte()), captured.single())
    }

    @Test
    fun ctrlL_sendsFormFeed_0x0c() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+L").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0c), captured.single())
    }

    @Test
    fun claudeSet_doesNotExposeOtherCliOnlyKeys() {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                    sessionKind = SessionKind.Claude,
                )
            }
        }

        composeRule.onNodeWithContentDescription("Show keys").performClick()

        listOf("Approve", "Reject", "Ctrl+K", "Ctrl+G", "Ctrl+D", "YOLO", "TODO")
            .forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
    }
}

/**
 * Antigravity control-key set: passing `sessionKind = SessionKind.Antigravity` adds
 * `Ctrl+K` (fast-approve pending action), `Ctrl+G` (tool confirmation view) and `Ctrl+D`
 * (exit) to the shared keys, positioned right after the fixed prefix by `CONTROL_KEY_ORDER`
 * (Antigravity has no `Mode`/`Rewind`). Verifies the Antigravity keys are present with the
 * correct raw byte payloads (sent via `onSendBytes`, the raw 'i'-frame path), that the
 * shared keys match the Claude/Codex bars, and that neither the Codex-only keys
 * (`Approve`/`Reject`) nor the Claude-only `Mode`/`Rewind` leak into the Antigravity set.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class InputBarAntigravityTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun openAntigravityPanel(onSendBytes: (ByteArray) -> Unit = {}) {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = onSendBytes,
                    sessionKind = SessionKind.Antigravity,
                )
            }
        }
        composeRule.onNodeWithContentDescription("Show keys").performClick()
    }

    @Test
    fun antigravityPanel_exposesAntigravitySpecificKeys_plusSharedKeys() {
        openAntigravityPanel()

        listOf("Ctrl+K", "Ctrl+G", "Ctrl+D", "Ctrl+L")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        // Shared nav/control tail; arrows and Backspace render as Material icons
        // (contentDescription below).
        listOf("Esc", "Ctrl+C", "Enter", "Tab", "Ctrl+O", "Ctrl+R")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        listOf("↑", "↓", "←", "→", "Backspace")
            .forEach { composeRule.onNodeWithContentDescription(it).assertExists() }
        composeRule.onNodeWithContentDescription("Custom shortcut").assertExists()
    }

    @Test
    fun ctrlK_sendsVerticalTab_0x0b() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+K").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0b), captured.single())
    }

    @Test
    fun ctrlG_sendsBell_0x07() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+G").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x07), captured.single())
    }

    @Test
    fun ctrlD_sendsEndOfTransmission_0x04() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+D").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x04), captured.single())
    }

    @Test
    fun antigravitySet_doesNotExposeCodexOrClaudeOnlyKeys() {
        openAntigravityPanel()

        listOf("Approve", "Reject", "Mode", "Rewind", "YOLO", "TODO")
            .forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
    }
}

/**
 * User-saved shortcut buttons: server-synced chords rendered after the ⌘ trigger
 * at the literal end of the control-key row. The button is a Surface with
 * [androidx.compose.foundation.combinedClickable], not a disabled-able
 * FilledTonalButton, so that long-press (Rename/Remove menu) stays reachable
 * while the session gate is down — the tap path no-ops instead.
 *
 * All tests render with `composerVisible = false`, which forces the control-key
 * panel visible without the "Show keys" toggle — that toggle is itself gated on
 * `enabled`, which would make the disabled-state tests unreachable through it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class InputBarSavedShortcutTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val savedCut = SavedShortcutUi(
        id = "s1",
        chord = KeyChord(ctrl = true, key = "x"),
        name = "Cut",
        risky = false,
    )
    private val savedUnnamed = SavedShortcutUi(
        id = "s2",
        chord = KeyChord(ctrl = true, key = "b"),
        name = null,
        risky = false,
    )

    private fun setBar(
        savedShortcuts: List<SavedShortcutUi>,
        enabled: Boolean = true,
        onSendSavedShortcut: (SavedShortcutUi) -> Unit = {},
        onRenameSavedShortcut: (SavedShortcutUi) -> Unit = {},
        onRemoveSavedShortcut: (SavedShortcutUi) -> Unit = {},
        onSendBytes: (ByteArray) -> Unit = {},
    ) {
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = onSendBytes,
                    enabled = enabled,
                    composerVisible = false,
                    savedShortcuts = savedShortcuts,
                    onSendSavedShortcut = onSendSavedShortcut,
                    onRenameSavedShortcut = onRenameSavedShortcut,
                    onRemoveSavedShortcut = onRemoveSavedShortcut,
                )
            }
        }
    }

    @Test
    fun savedButton_tapFiresOnSendSavedShortcut_andSendsNoBytesItself() {
        val sent = mutableListOf<SavedShortcutUi>()
        val bytes = mutableListOf<ByteArray>()
        setBar(
            listOf(savedCut),
            onSendSavedShortcut = { sent += it },
            onSendBytes = { bytes += it },
        )

        composeRule.onNodeWithTag("saved_shortcut_button")
            .performScrollTo()
            .performClick()

        assertEquals(listOf(savedCut), sent)
        assertTrue(
            "the button routes through onSendSavedShortcut (VM sendChord), never raw bytes",
            bytes.isEmpty(),
        )
    }

    @Test
    fun savedButton_tapIsInert_whileSessionGateDown() {
        val sent = mutableListOf<SavedShortcutUi>()
        setBar(listOf(savedCut), enabled = false, onSendSavedShortcut = { sent += it })

        composeRule.onNodeWithTag("saved_shortcut_button")
            .performScrollTo()
            .performClick()

        assertTrue(
            "tap must no-op while disabled — a dropped keystroke must not be pretended",
            sent.isEmpty(),
        )
    }

    @Test
    fun savedButton_longPressMenu_renameFires_evenWhileSessionGateDown() {
        // Rename/Remove are server mutations, not sends, so the long-press
        // menu must stay reachable while the session gate is down — the
        // reason the button is not simply `enabled = false`.
        val renamed = mutableListOf<SavedShortcutUi>()
        setBar(listOf(savedCut), enabled = false, onRenameSavedShortcut = { renamed += it })

        composeRule.onNodeWithTag("saved_shortcut_button")
            .performScrollTo()
            .performTouchInput { longClick() }
        composeRule.onNodeWithText("Rename").performClick()

        assertEquals(listOf(savedCut), renamed)
    }

    @Test
    fun savedButton_longPressMenu_removeFires() {
        val removed = mutableListOf<SavedShortcutUi>()
        val sent = mutableListOf<SavedShortcutUi>()
        setBar(
            listOf(savedCut),
            onSendSavedShortcut = { sent += it },
            onRemoveSavedShortcut = { removed += it },
        )

        composeRule.onNodeWithTag("saved_shortcut_button")
            .performScrollTo()
            .performTouchInput { longClick() }
        composeRule.onNodeWithText("Remove").performClick()

        assertEquals(listOf(savedCut), removed)
        assertTrue("long-press must never also fire the tap/send path", sent.isEmpty())
    }

    @Test
    fun savedButton_openMenu_staysOnItsShortcut_whenEarlierButtonRemoved() {
        // A live shortcut.deleted push can shift row positions under an open
        // Rename/Remove menu. key(shortcut.id) must keep the remembered menu
        // pinned to ITS shortcut — with positional memoization the removal
        // would hand the open menu (or none at all) to whichever shortcut
        // slides into that slot.
        val renamed = mutableListOf<SavedShortcutUi>()
        val shortcuts = mutableStateOf(listOf(savedCut, savedUnnamed))
        composeRule.setContent {
            Surface {
                InputBar(
                    text = TextFieldValue(""),
                    onTextChange = {},
                    onSend = {},
                    onSendBytes = {},
                    composerVisible = false,
                    savedShortcuts = shortcuts.value,
                    onRenameSavedShortcut = { renamed += it },
                )
            }
        }

        composeRule.onNodeWithContentDescription("Saved shortcut Ctrl+B")
            .performScrollTo()
            .performTouchInput { longClick() }
        composeRule.onNodeWithText("Rename").assertIsDisplayed()

        // Push removes the FIRST shortcut while Ctrl+B's menu is open.
        shortcuts.value = listOf(savedUnnamed)
        composeRule.waitForIdle()

        composeRule.onNodeWithText("Rename").performClick()
        assertEquals(listOf(savedUnnamed), renamed)
    }
}

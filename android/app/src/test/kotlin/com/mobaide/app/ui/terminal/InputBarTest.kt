package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.text.input.TextFieldValue
import com.mobaide.app.data.network.dto.SessionKind
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [InputBar] covering the CLI-aware control-key bar:
 *   - Orientation-aware layout (portrait stacks; landscape inline).
 *   - For the default/Claude set ([SessionKind.Claude]), the single horizontally
 *     scrollable control-key panel exposes exactly the 14 keys
 *     `Mode, Rewind, Esc, Ctrl+C, Enter, Tab, Backspace, ↑, ↓, ←, →, Ctrl+O, Ctrl+R, Ctrl+L`.
 *   - Byte payloads for renamed keys are unchanged (display-only rename):
 *     `S+Tab → Mode` and `Back/Edit → Rewind`; `Newline` was removed.
 *   - For [SessionKind.Codex] the panel prepends the approval answers `Approve`/`Reject`
 *     to the Claude set (which already closes with `Ctrl+L`), with the correct raw-byte
 *     payloads; those Codex-only keys never leak into the Claude set ([InputBarCodexTest]).
 *   - For [SessionKind.Antigravity] the panel leads with `Ctrl+K`, `Ctrl+G`, `Ctrl+D`
 *     and appends `Ctrl+L` to the shared tail (no `Mode`/`Rewind`, no `Approve`/`Reject`),
 *     with the correct raw-byte payloads; Antigravity-only keys never leak into the
 *     Claude or Codex sets ([InputBarAntigravityTest]).
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

        listOf("S+Tab", "Newline", "Back/Edit", "Ctrl-D", "Ctrl-C", "S-Tab")
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
        composeRule.onNodeWithText("Mode").performClick()

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
        composeRule.onNodeWithText("Rewind").performClick()

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

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w800dp-h360dp-land-xxhdpi")
class InputBarLandscapeTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun landscape_controlKeyPanel_exposesSame13Keys_asPortrait() {
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
        // scrollable; we assert the 13 keys are present in the composition,
        // not that every one fits on screen at every width. Arrows and Backspace
        // render as Material icons, so they match on contentDescription (below).
        listOf("Mode", "Rewind", "Esc", "Ctrl+C", "Enter", "Tab", "Ctrl+O", "Ctrl+R")
            .forEach { composeRule.onNodeWithText(it).assertExists() }
        listOf("↑", "↓", "←", "→", "Backspace")
            .forEach { composeRule.onNodeWithContentDescription(it).assertExists() }
    }
}

/**
 * Codex control-key set: passing `sessionKind = SessionKind.Codex` prepends the approval
 * answers `Approve`/`Reject` to the shared Claude set (which already closes with `Ctrl+L`).
 * Verifies the Codex-only keys are present with the correct raw byte payloads (sent via
 * `onSendBytes`, the raw 'i'-frame path — never the submit path), and that the Codex-only
 * keys do not leak into the default/Claude set.
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
    }

    @Test
    fun approve_sendsLiteralLowercaseY_0x79() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Approve").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf('y'.code.toByte()), captured.single())
    }

    @Test
    fun reject_sendsLiteralLowercaseN_0x6e() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Reject").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf('n'.code.toByte()), captured.single())
    }

    @Test
    fun rewind_sendsEscEsc_0x1b_0x1b() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Rewind").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x1b, 0x1b), captured.single())
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
    fun ctrlO_sendsShiftOut_0x0f() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+O").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0f), captured.single())
    }

    @Test
    fun ctrlR_sendsDeviceControl2_0x12() {
        val captured = mutableListOf<ByteArray>()
        openCodexPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+R").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x12), captured.single())
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

    @Test
    fun codexSet_doesNotExposeAntigravityOnlyKeys() {
        openCodexPanel()

        listOf("Ctrl+K", "Ctrl+G", "Ctrl+D")
            .forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
    }
}

/**
 * Antigravity control-key set: passing `sessionKind = SessionKind.Antigravity` leads with
 * `Ctrl+K` (fast-approve pending action), `Ctrl+G` (tool confirmation view) and `Ctrl+D`
 * (exit), reuses the shared nav/control tail, and appends `Ctrl+L`. Verifies the Antigravity
 * keys are present with the correct raw byte payloads (sent via `onSendBytes`, the raw
 * 'i'-frame path), that the shared tail matches the Claude/Codex bars, and that neither the
 * Codex-only keys (`Approve`/`Reject`) nor the Claude-only leads (`Mode`/`Rewind`) leak into
 * the Antigravity set.
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
    }

    @Test
    fun ctrlK_sendsVerticalTab_0x0b() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+K").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0b), captured.single())
    }

    @Test
    fun ctrlG_sendsBell_0x07() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+G").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x07), captured.single())
    }

    @Test
    fun ctrlD_sendsEndOfTransmission_0x04() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+D").performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x04), captured.single())
    }

    @Test
    fun ctrlL_sendsFormFeed_0x0c() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+L").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0c), captured.single())
    }

    @Test
    fun ctrlO_sendsShiftOut_0x0f() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+O").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x0f), captured.single())
    }

    @Test
    fun ctrlR_sendsDeviceControl2_0x12() {
        val captured = mutableListOf<ByteArray>()
        openAntigravityPanel(onSendBytes = { captured += it })

        composeRule.onNodeWithText("Ctrl+R").performScrollTo().performClick()

        assertEquals(1, captured.size)
        assertArrayEquals(byteArrayOf(0x12), captured.single())
    }

    @Test
    fun antigravitySet_doesNotExposeCodexOrClaudeOnlyKeys() {
        openAntigravityPanel()

        listOf("Approve", "Reject", "Mode", "Rewind", "YOLO", "TODO")
            .forEach { composeRule.onNodeWithText(it).assertDoesNotExist() }
    }
}

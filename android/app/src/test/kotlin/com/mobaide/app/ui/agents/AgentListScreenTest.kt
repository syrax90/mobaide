package com.mobaide.app.ui.agents

import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI smoke tests for the agent-list screen entry points. Composes the
 * extracted internal composables (`ScanQrFab`, `AgentListOverflowMenu`,
 * `EmptyAgentsState`, `InstallCommandsSheet`) directly so the tests stay
 * independent of the GMS code-scanner module (which is not available
 * under Robolectric).
 *
 * Robolectric SDK is pinned to 33 because Robolectric 4.13 has not yet
 * rolled SDK 35 jars; the project's `compileSdk = 35` is unaffected.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class AgentListScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun scanQrFab_rendersLabel_andRoutesClickToCallback() {
        var clickCount = 0
        composeRule.setContent {
            Surface { ScanQrFab(onClick = { clickCount += 1 }) }
        }

        // With composeBom 2024.12.01 ExtendedFloatingActionButton does NOT merge
        // its text label into the merged semantic node, so the text is only
        // visible in the unmerged tree. Use useUnmergedTree=true for the display
        // check. The click action lives on the merged FAB container node, which
        // is the only clickable in this composition; find it via hasClickAction().
        composeRule.onNodeWithText("Scan to add", useUnmergedTree = true).assertIsDisplayed()
        composeRule.onNode(hasClickAction()).assertHasClickAction().performClick()

        assertEquals(1, clickCount)
    }

    @Test
    fun overflowMenu_revealsAddManually_andRoutesClickToCallback() {
        var addManuallyCount = 0
        composeRule.setContent {
            Surface {
                AgentListOverflowMenu(
                    onShowInstall = {},
                    onAddManually = { addManuallyCount += 1 },
                )
            }
        }

        composeRule.onNodeWithText("Add manually...").assertDoesNotExist()

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithText("Add manually...")
            .assertIsDisplayed()
            .performClick()

        assertEquals(1, addManuallyCount)
    }

    @Test
    fun overflowMenu_revealsShowInstallCommands_andRoutesClickToCallback() {
        var installCount = 0
        composeRule.setContent {
            Surface {
                AgentListOverflowMenu(
                    onShowInstall = { installCount += 1 },
                    onAddManually = {},
                )
            }
        }

        composeRule.onNodeWithText("Show install commands").assertDoesNotExist()

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithText("Show install commands")
            .assertIsDisplayed()
            .performClick()

        assertEquals(1, installCount)
    }

    @Test
    fun emptyAgentsState_rendersCtas_andRoutesClicksToCallbacks() {
        var installClicks = 0
        var scanClicks = 0
        var addManuallyClicks = 0
        composeRule.setContent {
            Surface {
                EmptyAgentsState(
                    onShowInstallClick = { installClicks += 1 },
                    onScanClick = { scanClicks += 1 },
                    onAddManuallyClick = { addManuallyClicks += 1 },
                )
            }
        }

        composeRule.onNodeWithText("No agents yet").assertIsDisplayed()
        composeRule.onNodeWithText("1. Install on your host").assertIsDisplayed()
        composeRule.onNodeWithText("2. Scan the pairing QR shown after install")
            .assertIsDisplayed()

        composeRule.onNodeWithText("Show install commands")
            .assertIsDisplayed()
            .assertHasClickAction()
            .performClick()
        assertEquals(1, installClicks)

        composeRule.onNodeWithText("Scan QR code")
            .assertIsDisplayed()
            .assertHasClickAction()
            .performClick()
        assertEquals(1, scanClicks)

        composeRule.onNodeWithText("Add manually instead")
            .assertIsDisplayed()
            .assertHasClickAction()
            .performClick()
        assertEquals(1, addManuallyClicks)
    }

    @Test
    fun installCommandsSheet_rendersBothOsCommands() {
        composeRule.setContent {
            Surface { InstallCommandsSheet(onDismiss = {}) }
        }

        composeRule.onNodeWithText("Install mobaide-agent on a host").assertIsDisplayed()
        composeRule.onNodeWithText("Linux / macOS").assertIsDisplayed()
        composeRule.onNodeWithText("Windows").assertIsDisplayed()
        composeRule.onNodeWithText(INSTALL_COMMAND_LINUX_MACOS).assertIsDisplayed()
        composeRule.onNodeWithText(INSTALL_COMMAND_WINDOWS).assertIsDisplayed()
    }

    @Test
    fun installCommandCard_copyButton_writesNonSensitiveClipForLinuxMacos() {
        // Compose InstallCommandCard directly rather than via InstallCommandsSheet.
        // The sheet renders its content inside ModalBottomSheet's popup window,
        // and Robolectric's input dispatcher does not deliver Compose-test
        // synthetic clicks across popup boundaries — performClick() finds the
        // node (assertIsDisplayed succeeds) but the onClick lambda never runs.
        // The card uses the same copy code path that runs inside the sheet, so
        // this faithfully tests the end-to-end clipboard wire behavior. Reading
        // back primaryClip from the Activity's ClipboardManager and asserting
        // EXTRA_IS_SENSITIVE is absent proves `isSensitive = false`.
        composeRule.setContent {
            Surface {
                InstallCommandCard(
                    osLabel = "Linux / macOS",
                    command = INSTALL_COMMAND_LINUX_MACOS,
                )
            }
        }

        val clipboard = composeRule.activity
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.clearPrimaryClip()

        composeRule.onNodeWithContentDescription("Copy Linux / macOS install command")
            .assertIsDisplayed()
            .performClick()

        val clip = clipboard.primaryClip
        assertNotNull("primaryClip should be populated after copy click", clip)
        assertEquals("Mobaide install command", clip!!.description.label.toString())
        assertEquals(INSTALL_COMMAND_LINUX_MACOS, clip.getItemAt(0).text.toString())
        val flaggedSensitive = clip.description.extras
            ?.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE, false)
            ?: false
        assertFalse(
            "Install commands are public — EXTRA_IS_SENSITIVE must not be set",
            flaggedSensitive,
        )
    }
}

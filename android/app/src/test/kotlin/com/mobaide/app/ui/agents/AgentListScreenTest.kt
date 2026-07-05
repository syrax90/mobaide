package com.mobaide.app.ui.agents

import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
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

package com.mobaide.app.ui.agents

import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI smoke tests for the FAB-promotion change — verify the three new
 * entry points to the agent-creation flows render the right strings and
 * route taps to the right callbacks. Composes the extracted internal
 * composables (`ScanQrFab`, `AgentListOverflowMenu`, `EmptyAgentsState`)
 * directly so the tests stay independent of the GMS code-scanner module
 * (which is not available under Robolectric).
 *
 * Robolectric SDK is pinned to 33 because Robolectric 4.13 has not yet
 * rolled SDK 35 jars; the project's `compileSdk = 35` is unaffected.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AgentListScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

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
            Surface { AgentListOverflowMenu(onAddManually = { addManuallyCount += 1 }) }
        }

        composeRule.onNodeWithText("Add manually...").assertDoesNotExist()

        composeRule.onNodeWithContentDescription("More options").performClick()

        composeRule.onNodeWithText("Add manually...")
            .assertIsDisplayed()
            .performClick()

        assertEquals(1, addManuallyCount)
    }

    @Test
    fun emptyAgentsState_rendersCtas_andRoutesClicksToCallbacks() {
        var scanClicks = 0
        var addManuallyClicks = 0
        composeRule.setContent {
            Surface {
                EmptyAgentsState(
                    onScanClick = { scanClicks += 1 },
                    onAddManuallyClick = { addManuallyClicks += 1 },
                )
            }
        }

        composeRule.onNodeWithText("No agents yet").assertIsDisplayed()
        composeRule.onNodeWithText(
            "Scan the QR code shown by `mobaide-agent show-qr` on your host to add an agent.",
        ).assertIsDisplayed()

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
}

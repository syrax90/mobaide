package com.mobaide.app.ui.support

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
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
 * UI tests for the stateless [SupportContent] composable. Driving the
 * extracted content directly keeps the test free of Hilt / [SupportViewModel],
 * mirroring `AgentListScreenTest`.
 *
 * Robolectric SDK is pinned to 33 to match existing project precedent
 * (`AgentListScreenTest`, `CodeInputFieldTest`); Robolectric 4.13 has not
 * rolled SDK 35 jars.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class SupportScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun rendersBothChannelActions_withClickAction() {
        composeRule.setContent {
            Surface {
                SupportContent(
                    onAskCommunity = {},
                    onEmailSupport = {},
                    onBack = {},
                )
            }
        }

        composeRule.onNodeWithText("Ask the community")
            .assertIsDisplayed()
            .assertHasClickAction()
        composeRule.onNodeWithText("Email support")
            .assertIsDisplayed()
            .assertHasClickAction()
    }

    @Test
    fun askCommunity_routesClickToCallback() {
        var askClicks = 0
        var emailClicks = 0
        composeRule.setContent {
            Surface {
                SupportContent(
                    onAskCommunity = { askClicks += 1 },
                    onEmailSupport = { emailClicks += 1 },
                    onBack = {},
                )
            }
        }

        composeRule.onNodeWithText("Ask the community").performClick()

        assertEquals(1, askClicks)
        assertEquals(0, emailClicks)
    }

    @Test
    fun emailSupport_routesClickToCallback() {
        var askClicks = 0
        var emailClicks = 0
        composeRule.setContent {
            Surface {
                SupportContent(
                    onAskCommunity = { askClicks += 1 },
                    onEmailSupport = { emailClicks += 1 },
                    onBack = {},
                )
            }
        }

        composeRule.onNodeWithText("Email support").performClick()

        assertEquals(1, emailClicks)
        assertEquals(0, askClicks)
    }

    @Test
    fun backNavigationIcon_routesClickToOnBack() {
        var backClicks = 0
        composeRule.setContent {
            Surface {
                SupportContent(
                    onAskCommunity = {},
                    onEmailSupport = {},
                    onBack = { backClicks += 1 },
                )
            }
        }

        composeRule.onNodeWithContentDescription("Back")
            .assertHasClickAction()
            .performClick()

        assertEquals(1, backClicks)
    }
}

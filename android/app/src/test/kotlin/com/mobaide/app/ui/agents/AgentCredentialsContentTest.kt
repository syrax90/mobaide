package com.mobaide.app.ui.agents

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [AgentCredentialsContent] — the visual body of the
 * post-create / post-re-register credentials sheet. Composes the `internal`
 * content directly rather than [AgentCredentialsSheet]: Robolectric's input
 * dispatcher does not deliver synthetic clicks into [ModalBottomSheet]'s popup
 * window (same constraint exploited by `AgentListScreenTest` for
 * `InstallCommandCard`), so the OS selector and Done button are only
 * exercisable on the un-wrapped content.
 *
 * Robolectric SDK pinned to 33 to match existing precedent
 * (`AgentListScreenTest`, `CodeInputFieldTest`); Robolectric 4.13 has not
 * rolled SDK 35 jars.
 *
 * Code values render inside a `SelectionContainer` whose monospace `Text`
 * sits in the unmerged semantics tree, so content assertions use
 * `useUnmergedTree = true` with `substring = true`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class AgentCredentialsContentTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val agentId = "a1b2c3d4e5f6"
    private val token = "xVz9p-Q2r-EXAMPLE-token-not-real-0123456789"

    @Test
    fun selectingWindows_swapsPathAndRestartCommand() {
        composeRule.setContent {
            Surface {
                AgentCredentialsContent(
                    title = "Agent Created",
                    agentId = agentId,
                    token = token,
                    onDone = {},
                )
            }
        }

        composeRule.onNodeWithTag("os_Windows").performClick()

        composeRule.onNodeWithText(
            "C:\\Program Files\\MobaideAgent\\mobaide_agent\\.env",
            useUnmergedTree = true,
        ).assertExists()
        composeRule.onNodeWithText(
            "Stop-ScheduledTask MobaideAgent; Start-ScheduledTask MobaideAgent",
            useUnmergedTree = true,
        ).assertExists()
        // The previous (Linux) path must no longer be shown after the swap.
        composeRule.onNodeWithText("/opt/mobaide/mobaide_agent/.env", useUnmergedTree = true)
            .assertDoesNotExist()
    }
}

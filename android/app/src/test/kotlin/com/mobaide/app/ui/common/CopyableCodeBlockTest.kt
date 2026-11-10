package com.mobaide.app.ui.common

import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertWidthIsAtLeast
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * UI tests for [CopyableCodeBlock]. The component renders [label] + monospace
 * [code] with a copy button whose accessibility description is "Copy <label>"
 * and which writes [code] to the system clipboard via [copyToClipboard].
 *
 * Robolectric SDK pinned to 33 to match existing precedent
 * (`AgentListScreenTest`, `CodeInputFieldTest`) and so the API 33+
 * `EXTRA_IS_SENSITIVE` branch in [copyToClipboard] is exercised.
 *
 * Copy behaviour is verified end-to-end by reading `primaryClip` back from the
 * Activity's [ClipboardManager] — the same approach as
 * `AgentListScreenTest.installCommandCard_copyButton_*`. Unlike `InstallCommandCard`,
 * this component is composed directly (not inside a ModalBottomSheet), so the
 * copy click is delivered normally.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class CopyableCodeBlockTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun copyButton_writesCodeToClipboardUnderLabel() {
        composeRule.setContent {
            Surface { CopyableCodeBlock(label = "Agent ID", code = "a1b2c3d4e5f6") }
        }

        val clipboard = composeRule.activity
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.clearPrimaryClip()

        composeRule.onNodeWithContentDescription("Copy Agent ID")
            .assertIsDisplayed()
            .performClick()

        val clip = clipboard.primaryClip
        assertNotNull("primaryClip should be populated after copy click", clip)
        assertEquals("Agent ID", clip!!.description.label.toString())
        assertEquals("a1b2c3d4e5f6", clip.getItemAt(0).text.toString())
    }

    @Test
    fun nonSensitiveByDefault_doesNotFlagClipSensitive() {
        composeRule.setContent {
            Surface {
                CopyableCodeBlock(
                    label = "File location (default install)",
                    code = "/opt/mobaide/mobaide_agent/.env",
                )
            }
        }

        val clipboard = composeRule.activity
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.clearPrimaryClip()

        composeRule.onNodeWithContentDescription("Copy File location (default install)")
            .performClick()

        val clip = clipboard.primaryClip
        assertNotNull(clip)
        val flaggedSensitive = clip!!.description.extras
            ?.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE, false)
            ?: false
        assertFalse(
            "A non-sensitive payload must not set EXTRA_IS_SENSITIVE",
            flaggedSensitive,
        )
    }

    @Test
    fun sensitivePayload_flagsClipSensitiveOnApi33() {
        composeRule.setContent {
            Surface {
                CopyableCodeBlock(
                    label = "Token (shown once)",
                    code = "xVz9p-secret-token",
                    isSensitive = true,
                )
            }
        }

        val clipboard = composeRule.activity
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.clearPrimaryClip()

        composeRule.onNodeWithContentDescription("Copy Token (shown once)")
            .performClick()

        val clip = clipboard.primaryClip
        assertNotNull(clip)
        val flaggedSensitive = clip!!.description.extras
            ?.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE, false)
            ?: false
        assertTrue(
            "A token-bearing payload must set EXTRA_IS_SENSITIVE on API 33+",
            flaggedSensitive,
        )
    }

    /** Far wider than the 360dp screen in the monospace value font. The width
     *  tests run in NATIVE graphics mode: LEGACY measures text too narrow to
     *  overflow the row. */
    private val longPath =
        "/Users/developer/Documents/Projects/mobile-workspace/services/backend-api/src"

    /** A value wider than the row must not squeeze the copy button out of it. */
    private fun assertCopyButtonKeepsItsSize(label: String) {
        val rootRight = composeRule.onRoot().getBoundsInRoot().right
        val button = composeRule.onNodeWithContentDescription("Copy $label")
            .assertIsDisplayed()
            .assertWidthIsAtLeast(40.dp)
        val right = button.getUnclippedBoundsInRoot().right
        assertTrue("Copy $label ends at $right, past the screen edge $rootRight", right <= rootRight)
    }

    @Test
    @GraphicsMode(GraphicsMode.Mode.NATIVE)
    fun longSingleLineValue_keepsCopyButtonFullSize_andCopiesTheWholeValue() {
        composeRule.setContent {
            Surface { CopyableCodeBlock(label = "Folder", code = longPath) }
        }

        assertCopyButtonKeepsItsSize("Folder")

        val clipboard = composeRule.activity
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.clearPrimaryClip()
        composeRule.onNodeWithContentDescription("Copy Folder").performClick()

        assertEquals(longPath, clipboard.primaryClip!!.getItemAt(0).text.toString())
    }

    @Test
    @GraphicsMode(GraphicsMode.Mode.NATIVE)
    fun longWrappingValue_keepsCopyButtonFullSize() {
        composeRule.setContent {
            Surface {
                CopyableCodeBlock(
                    label = ".env",
                    code = "RELAY_AGENT_ID=$longPath\nRELAY_AGENT_TOKEN=$longPath",
                    singleLine = false,
                )
            }
        }

        assertCopyButtonKeepsItsSize(".env")
    }
}

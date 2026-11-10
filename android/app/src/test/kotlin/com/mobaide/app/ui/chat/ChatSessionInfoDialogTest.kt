package com.mobaide.app.ui.chat

import android.content.ClipboardManager
import android.content.Context
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertWidthIsAtLeast
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.isDialog
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.chat.ChatAgentInfo
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The folder path and ACP session id are wider than the dialog, so they must
 * scroll in their own slot and leave each copy button its full size. NATIVE
 * graphics: LEGACY measures text too narrow to overflow the row.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ChatSessionInfoDialogTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val agent = ChatAgentInfo(name = "claude-code", title = "Claude Code", version = "0.12.3")
    private val cwd = "/Users/developer/Documents/Projects/mobile-workspace/services/backend-api"
    private val acpSessionId = "0f8b6c2e-4a1d-4e57-9c3b-7d2a5e9f1b64"

    /** Copy-button label to the exact value it must put on the clipboard. */
    private val values = linkedMapOf(
        "Agent" to "Claude Code 0.12.3",
        "Folder" to cwd,
        "ACP session ID" to acpSessionId,
    )

    private fun setContent() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                ChatSessionInfoDialog(
                    agent = agent,
                    cwd = cwd,
                    acpSessionId = acpSessionId,
                    onDismiss = {},
                )
            }
        }
    }

    @Test
    fun longValues_leaveEveryCopyButtonFullSizeInsideTheDialog() {
        setContent()
        val dialog = composeRule.onNode(isDialog()).getBoundsInRoot()

        values.keys.forEach { label ->
            val bounds = composeRule.onNodeWithContentDescription("Copy $label")
                .assertIsDisplayed()
                .assertWidthIsAtLeast(40.dp)
                .getUnclippedBoundsInRoot()
            assertTrue(
                "Copy $label spans ${bounds.left}..${bounds.right}, outside the dialog " +
                    "${dialog.left}..${dialog.right}",
                bounds.left >= dialog.left && bounds.right <= dialog.right,
            )
        }
    }

    @Test
    fun everyCopyButton_putsItsFullValueOnTheClipboard() {
        setContent()
        val clipboard = composeRule.activity
            .getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager

        values.forEach { (label, value) ->
            clipboard.clearPrimaryClip()
            composeRule.onNodeWithContentDescription("Copy $label").performClick()

            val clip = clipboard.primaryClip
            assertEquals("Copy $label clip label", label, clip?.description?.label?.toString())
            assertEquals("Copy $label clip text", value, clip?.getItemAt(0)?.text?.toString())
        }
    }
}

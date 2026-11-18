package com.mobaide.app.ui.chat

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.theme.MobaideTheme

const val NEW_CONVERSATION_CONFIRM_TAG = "new_conversation_confirm"
const val NEW_CONVERSATION_CANCEL_TAG = "new_conversation_cancel"

/** Confirms `new_conversation`, as on iOS and web: the chat empties, the agent keeps the old one. */
@Composable
fun NewConversationDialog(
    agentName: String,
    running: Boolean,
    queued: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        modifier = modifier,
        title = { Text("Start a new conversation?") },
        text = { Text(newConversationMessage(agentName, running, queued)) },
        confirmButton = {
            PixelTextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(NEW_CONVERSATION_CONFIRM_TAG),
            ) {
                Text("New conversation")
            }
        },
        dismissButton = {
            PixelTextButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(NEW_CONVERSATION_CANCEL_TAG),
            ) {
                Text("Cancel")
            }
        },
    )
}

private fun newConversationMessage(agentName: String, running: Boolean, queued: Boolean): String {
    val stop = if (running) "The running turn stops. " else ""
    val dropped = if (queued) "Queued messages are removed. " else ""
    val agent = agentName.replaceFirstChar { it.uppercase() }
    return "${stop}${dropped}The chat starts empty, with the same folder and settings. " +
        "If $agent saved this conversation, open it again with Continue a session."
}

@Preview(name = "New conversation dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun NewConversationDialogPreviewDark() {
    MobaideTheme(darkTheme = true) {
        NewConversationDialog(agentName = "Claude Code", running = true, queued = true, onConfirm = {}, onDismiss = {})
    }
}

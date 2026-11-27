package com.mobaide.app.ui.chat

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.theme.MobaideTheme

const val FORK_CONFIRM_TAG = "fork_confirm"
const val FORK_CANCEL_TAG = "fork_cancel"

/** Confirms `fork`: a new chat with the conversation, this one unchanged, as on iOS and web. */
@Composable
fun ForkDialog(
    atAnswer: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        modifier = modifier,
        title = { Text(if (atAnswer) "Fork from here?" else "Fork this chat?") },
        text = { Text(forkMessage(atAnswer)) },
        confirmButton = {
            PixelTextButton(onClick = onConfirm, modifier = Modifier.testTag(FORK_CONFIRM_TAG)) {
                Text("Fork")
            }
        },
        dismissButton = {
            PixelTextButton(onClick = onDismiss, modifier = Modifier.testTag(FORK_CANCEL_TAG)) {
                Text("Cancel")
            }
        },
    )
}

private fun forkMessage(atAnswer: Boolean): String = if (atAnswer) {
    "Start a new chat with the conversation up to this answer? This chat stays as it is. " +
        "Files on your PC are not changed back."
} else {
    "Start a new chat with a copy of this conversation? This chat stays as it is. " +
        "Both chats work in the same folder."
}

@Preview(name = "Fork dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun ForkDialogPreviewLight() {
    MobaideTheme(darkTheme = false) {
        ForkDialog(atAnswer = false, onConfirm = {}, onDismiss = {})
    }
}

@Preview(name = "Fork dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun ForkDialogPreviewDark() {
    MobaideTheme(darkTheme = true) {
        ForkDialog(atAnswer = true, onConfirm = {}, onDismiss = {})
    }
}

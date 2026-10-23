package com.mobaide.app.ui.sessions.panel

import androidx.compose.foundation.layout.Row
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.theme.MobaideTheme

const val STOP_SESSION_DIALOG_STOP_TAG = "stop_session_dialog_stop"
const val STOP_SESSION_DIALOG_FORCE_TAG = "stop_session_dialog_force"
const val STOP_SESSION_DIALOG_CANCEL_TAG = "stop_session_dialog_cancel"

/**
 * The panel's stop confirmation, iOS's and web's Stop / Force Stop sheet. The
 * sessions list kills unconfirmed, but in the drawer the stop button sits
 * beside the row's switch target, where a slip is far more likely.
 */
@Composable
fun StopSessionDialog(
    label: String,
    onStop: (force: Boolean) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        modifier = modifier,
        title = { Text("Stop session?") },
        text = { Text("Stop \"$label\"? Force Stop kills the process immediately.") },
        confirmButton = {
            Row {
                PixelTextButton(
                    onClick = { onStop(false) },
                    modifier = Modifier.testTag(STOP_SESSION_DIALOG_STOP_TAG),
                ) {
                    Text("Stop", color = MaterialTheme.colorScheme.error)
                }
                PixelTextButton(
                    onClick = { onStop(true) },
                    modifier = Modifier.testTag(STOP_SESSION_DIALOG_FORCE_TAG),
                ) {
                    Text("Force Stop", color = MaterialTheme.colorScheme.error)
                }
            }
        },
        dismissButton = {
            PixelTextButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(STOP_SESSION_DIALOG_CANCEL_TAG),
            ) {
                Text("Cancel")
            }
        },
    )
}

@Preview(name = "Stop session dialog dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun StopSessionDialogPreviewDark() {
    MobaideTheme(darkTheme = true) {
        StopSessionDialog("Claude Code · app", {}, {})
    }
}

@Preview(name = "Stop session dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun StopSessionDialogPreviewLight() {
    MobaideTheme(darkTheme = false) {
        StopSessionDialog("Claude Code · app", {}, {})
    }
}

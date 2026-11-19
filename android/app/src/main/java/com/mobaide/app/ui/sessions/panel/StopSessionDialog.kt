package com.mobaide.app.ui.sessions.panel

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.theme.MobaideTheme

const val STOP_SESSION_DIALOG_STOP_TAG = "stop_session_dialog_stop"
const val STOP_SESSION_DIALOG_FORCE_TAG = "stop_session_dialog_force"
const val STOP_SESSION_DIALOG_CANCEL_TAG = "stop_session_dialog_cancel"
const val STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG = "stop_session_dialog_remove_worktree"

fun worktreeRemovalLabel(branch: String): String =
    "Also remove the worktree and local branch \"$branch\""

const val WORKTREE_REMOVAL_HINT =
    "Uncommitted changes and commits that are only on this branch are lost."

const val WORKTREE_IN_USE_HINT =
    "Another session runs in this worktree. Stop it first to remove the worktree."

/**
 * Stop / Force Stop confirmation. With [worktreeBranch] set it also offers
 * removing the worktree and branch, never pre-checked since that discards
 * uncommitted work. While [worktreeInUse] the agent refuses, so it is disabled.
 */
@Composable
fun StopSessionDialog(
    label: String,
    onStop: (force: Boolean, removeWorktree: Boolean) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
    worktreeBranch: String? = null,
    worktreeInUse: Boolean = false,
) {
    var removeWorktree by rememberSaveable(worktreeBranch) { mutableStateOf(false) }
    val offerRemoval = !worktreeBranch.isNullOrEmpty()
    val remove = offerRemoval && !worktreeInUse && removeWorktree
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        modifier = modifier,
        title = { Text("Stop session?") },
        text = {
            Column {
                Text("Stop \"$label\"? Force Stop kills the process immediately.")
                if (offerRemoval) {
                    Spacer(Modifier.height(12.dp))
                    // One toggleable row, so the label is a tap target too.
                    Row(
                        modifier = Modifier
                            .toggleable(
                                value = remove,
                                enabled = !worktreeInUse,
                                role = Role.Checkbox,
                                onValueChange = { removeWorktree = it },
                            )
                            .testTag(STOP_SESSION_DIALOG_REMOVE_WORKTREE_TAG),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        PixelCheckbox(
                            checked = remove,
                            onCheckedChange = null,
                            enabled = !worktreeInUse,
                        )
                        Spacer(Modifier.width(4.dp))
                        Column {
                            Text(
                                worktreeRemovalLabel(worktreeBranch.orEmpty()),
                                style = MaterialTheme.typography.bodyMedium,
                                color = if (worktreeInUse) {
                                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                                } else {
                                    Color.Unspecified
                                },
                            )
                            Text(
                                if (worktreeInUse) WORKTREE_IN_USE_HINT else WORKTREE_REMOVAL_HINT,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            Row {
                PixelTextButton(
                    onClick = { onStop(false, remove) },
                    modifier = Modifier.testTag(STOP_SESSION_DIALOG_STOP_TAG),
                ) {
                    Text("Stop", color = MaterialTheme.colorScheme.error)
                }
                PixelTextButton(
                    onClick = { onStop(true, remove) },
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
        StopSessionDialog("Claude Code · app", { _, _ -> }, {}, worktreeBranch = "fix-login")
    }
}

@Preview(name = "Stop session dialog light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun StopSessionDialogPreviewLight() {
    MobaideTheme(darkTheme = false) {
        StopSessionDialog("Claude Code · app", { _, _ -> }, {})
    }
}

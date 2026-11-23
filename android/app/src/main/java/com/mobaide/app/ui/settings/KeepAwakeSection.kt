package com.mobaide.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.KeepAwakeStatus
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.theme.MobaideTheme

/** Web `AgentSettingsDialog.tsx` and iOS `KeepAwakeCopy` hold the same text. */
object KeepAwakeCopy {
    const val TITLE = "Keep awake"

    const val NO_AGENT =
        "Stops your computer from going to sleep while it is idle, so your sessions stay reachable. " +
            "Connect to an agent to set it up."

    fun label(agentName: String) = "Keep $agentName awake"

    fun hint(agentName: String) =
        "Stops $agentName from going to sleep while it is idle, so your sessions stay reachable. " +
            "The screen can still turn off. Closing a laptop lid still puts it to sleep."

    fun checking(agentName: String) = "Checking $agentName…"

    fun unreachable(agentName: String) = "Couldn’t reach $agentName. Is it online?"

    fun outdated(agentName: String) =
        "$agentName runs a Mobaide agent without this setting. It updates itself; check again after its next update."

    fun saveFailed(agentName: String) = "Couldn’t save this setting on $agentName. Try again."

    fun unsupported(agentName: String, message: String?) = message ?: "$agentName can’t be kept awake."

    fun failed(agentName: String, message: String?) =
        "$agentName can’t stay awake: " + (message ?: "The computer did not say why.")
}

/**
 * Keep awake for the connected agent: a checkbox row, the whole row one
 * target, and a line that says why the computer is not kept awake when the
 * agent could not do it.
 */
@Composable
fun KeepAwakeSection(
    state: KeepAwakeUiState,
    onKeepAwakeChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val name = state.agentName
    Column(
        modifier = modifier
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .testTag("keep_awake_section"),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text = KeepAwakeCopy.TITLE, style = MaterialTheme.typography.titleMedium)
        val status = state.status
        when {
            state.availability == KeepAwakeAvailability.NoAgent -> Note(KeepAwakeCopy.NO_AGENT)
            state.availability == KeepAwakeAvailability.Checking -> Note(KeepAwakeCopy.checking(name))
            state.availability == KeepAwakeAvailability.Unreachable -> Note(KeepAwakeCopy.unreachable(name))
            state.availability == KeepAwakeAvailability.Outdated -> Note(KeepAwakeCopy.outdated(name))
            status == null -> Note(KeepAwakeCopy.checking(name))
            else -> KeepAwakeRow(state, status, onKeepAwakeChange)
        }
    }
}

@Composable
private fun KeepAwakeRow(
    state: KeepAwakeUiState,
    status: KeepAwakeStatus,
    onKeepAwakeChange: (Boolean) -> Unit,
) {
    val name = state.agentName
    val unsupported = status.state == KeepAwakeStatus.STATE_UNSUPPORTED
    val checked = state.saving ?: status.enabled
    val enabled = state.saving == null && !(unsupported && !status.enabled)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .defaultMinSize(minHeight = 48.dp)
            .toggleable(value = checked, enabled = enabled, role = Role.Checkbox, onValueChange = onKeepAwakeChange)
            .testTag("keep_awake_checkbox"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelCheckbox(checked = checked, onCheckedChange = null, enabled = enabled)
        Spacer(modifier = Modifier.width(8.dp))
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(KeepAwakeCopy.label(name), style = MaterialTheme.typography.bodyMedium)
            Text(
                text = KeepAwakeCopy.hint(name),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    when {
        state.saveFailed -> Note(KeepAwakeCopy.saveFailed(name), error = true)
        unsupported -> Note(KeepAwakeCopy.unsupported(name, status.message))
        state.saving == null && status.enabled && status.state != KeepAwakeStatus.STATE_ACTIVE ->
            Note(KeepAwakeCopy.failed(name, status.message), error = true)
    }
}

@Composable
private fun Note(text: String, error: Boolean = false) {
    Text(
        text = text,
        modifier = Modifier
            .semantics { liveRegion = LiveRegionMode.Polite }
            .testTag("keep_awake_note"),
        style = MaterialTheme.typography.bodySmall,
        color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

private val previewState = KeepAwakeUiState(
    agentName = "MacBook",
    availability = KeepAwakeAvailability.Available,
    status = KeepAwakeStatus(enabled = true, state = KeepAwakeStatus.STATE_FAILED, message = "systemd-inhibit: Access denied"),
)

@Preview(name = "Keep awake light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun KeepAwakeSectionPreviewLight() {
    MobaideTheme(darkTheme = false) { KeepAwakeSection(previewState, onKeepAwakeChange = {}) }
}

@Preview(name = "Keep awake dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun KeepAwakeSectionPreviewDark() {
    MobaideTheme(darkTheme = true) { KeepAwakeSection(previewState, onKeepAwakeChange = {}) }
}

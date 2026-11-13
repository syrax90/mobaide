package com.mobaide.app.ui.voice

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Downloading
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.MicNone
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceErrorInfo
import com.mobaide.app.data.network.dto.VoiceHostInfo
import com.mobaide.app.data.network.dto.VoiceHostReason
import com.mobaide.app.data.network.dto.VoiceModelInfo
import com.mobaide.app.data.network.dto.VoiceProgress
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.voice.DictationState
import com.mobaide.app.data.voice.InterruptReason
import com.mobaide.app.data.voice.VoiceCodes
import com.mobaide.app.data.voice.VoiceErrorCopy
import com.mobaide.app.data.voice.VoiceMicMode
import com.mobaide.app.data.voice.VoiceStatusView
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelProgressBar
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

/** The composer's mic. Every state has its own icon and label, so none relies on colour. */
@Composable
fun VoiceMicButton(
    mode: VoiceMicMode,
    status: VoiceStatusResponse?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val (icon, label) = when (mode) {
        VoiceMicMode.Ready -> Icons.Filled.Mic to "Dictate"
        VoiceMicMode.Off -> Icons.Outlined.MicNone to "Voice input is off. Set it up"
        VoiceMicMode.Preparing ->
            Icons.Outlined.Downloading to (status?.let(VoiceStatusView::preparingLabel) ?: "Preparing voice input")
        VoiceMicMode.Failed -> Icons.Outlined.ErrorOutline to "Voice input needs attention"
        VoiceMicMode.Hidden -> return
    }
    PixelIconButton(
        imageVector = icon,
        contentDescription = label,
        onClick = onClick,
        compact = true,
        modifier = modifier.testTag("voice_mic_button"),
    )
}

/**
 * Replaces the composer's action buttons while a dictation runs: cancel,
 * timer, level meter and stop while recording; progress while transcribing
 * and cleaning up; the reason with Retry after a failure. The status line is
 * a polite live region, so TalkBack announces "Recording" and "Transcribing".
 */
@Composable
fun VoiceDictationBar(
    state: DictationState,
    agentName: String,
    onStop: () -> Unit,
    onCancel: () -> Unit,
    onRetry: () -> Unit,
    onDismiss: () -> Unit,
    onRecordAgain: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var details by remember { mutableStateOf<String?>(null) }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .testTag("voice_dictation_bar"),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        when (state) {
            DictationState.Idle, is DictationState.Result -> Unit
            DictationState.Starting -> {
                StatusText("Opening the microphone…", Modifier.weight(1f))
                CancelButton(onCancel)
            }
            is DictationState.Recording -> {
                CancelButton(onCancel)
                Box(
                    modifier = Modifier
                        .size(10.dp)
                        .background(MaterialTheme.pixel.danger),
                )
                StatusText(
                    text = VoiceStatusView.formatDuration(state.elapsedMs),
                    spoken = "Recording",
                )
                LevelMeter(state.level, Modifier.weight(1f))
                when {
                    state.remainingSeconds != null -> Text(
                        text = "${state.remainingSeconds} s",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.pixel.warning,
                    )
                    state.lagging -> Text(
                        text = "Slow",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.pixel.warning,
                    )
                }
                PixelIconButton(
                    imageVector = Icons.Filled.Stop,
                    contentDescription = "Stop and transcribe",
                    onClick = onStop,
                    selected = true,
                    compact = true,
                    modifier = Modifier.testTag("voice_stop_button"),
                )
            }
            is DictationState.Transcribing -> {
                PixelBlinkCursor()
                StatusText(
                    text = transcribingLine(state, agentName),
                    modifier = Modifier.weight(1f),
                )
                CancelButton(onCancel)
            }
            // Part of transcribing to the user: the same bar, one line changed.
            is DictationState.Cleaning -> {
                PixelBlinkCursor()
                StatusText(text = "Cleaning up the text…", modifier = Modifier.weight(1f))
                CancelButton(onCancel)
            }
            DictationState.Empty -> {
                StatusText("No speech was recognised.", Modifier.weight(1f))
                PixelButton(text = "Again", onClick = onRecordAgain, compact = true)
                CloseButton(onDismiss)
            }
            is DictationState.Failed -> {
                StatusText(VoiceErrorCopy.dictation(state.code, agentName), Modifier.weight(1f))
                when {
                    state.code == VoiceCodes.MIC_PERMISSION_DENIED -> PixelButton(
                        text = "Settings",
                        onClick = { context.openAppSettings() },
                        compact = true,
                    )
                    state.retryable && state.hasAudio -> PixelIconButton(
                        imageVector = Icons.Filled.Refresh,
                        contentDescription = "Retry from the recording",
                        onClick = onRetry,
                        compact = true,
                        modifier = Modifier.testTag("voice_retry_button"),
                    )
                }
                if (!state.message.isNullOrBlank()) {
                    PixelTextButton(onClick = { details = state.message }) { Text("Details") }
                }
                CloseButton(onDismiss)
            }
        }
    }
    details?.let { message ->
        PixelAlertDialog(
            onDismissRequest = { details = null },
            confirmButton = { PixelTextButton(onClick = { details = null }) { Text("Close") } },
            title = { Text("Details") },
            text = { Text(message) },
        )
    }
}

private fun transcribingLine(state: DictationState.Transcribing, agentName: String): String =
    when {
        state.lagging -> "Waiting for the connection…"
        state.interruptedBy == InterruptReason.Background -> "Stopped when the app left the screen. Transcribing…"
        state.interruptedBy == InterruptReason.Call -> "Stopped for a call. Transcribing…"
        state.interruptedBy == InterruptReason.Device -> "The microphone went away. Transcribing…"
        state.interruptedBy == InterruptReason.Limit -> "Time limit reached. Transcribing…"
        else -> "Transcribing on $agentName…"
    }

@Composable
private fun StatusText(text: String, modifier: Modifier = Modifier, spoken: String = text) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        maxLines = 2,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier.semantics {
            liveRegion = LiveRegionMode.Polite
            contentDescription = spoken
        },
    )
}

@Composable
private fun LevelMeter(level: Float, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .height(8.dp)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .semantics { stateDescription = "Microphone level ${(level * 100).toInt()} percent" },
    ) {
        Box(
            modifier = Modifier
                .fillMaxHeight()
                .fillMaxWidth(level.coerceIn(0f, 1f))
                .background(MaterialTheme.colorScheme.primary),
        )
    }
}

@Composable
private fun CancelButton(onClick: () -> Unit) {
    PixelIconButton(
        imageVector = Icons.Filled.Close,
        contentDescription = "Cancel dictation",
        onClick = onClick,
        compact = true,
        modifier = Modifier.testTag("voice_cancel_button"),
    )
}

@Composable
private fun CloseButton(onClick: () -> Unit) {
    PixelIconButton(
        imageVector = Icons.Filled.Close,
        contentDescription = "Close",
        onClick = onClick,
        compact = true,
    )
}

/** What the mic opens while voice is not ready; follows the live status. Never opens on its own. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VoiceSetupSheet(
    state: VoiceUiState,
    onDismiss: () -> Unit,
    onEnable: () -> Unit,
    onCancelInstall: () -> Unit,
    onRetry: () -> Unit,
    onHideMic: () -> Unit,
    onCleanUpChange: (Boolean) -> Unit = {},
) {
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp)
                .testTag("voice_setup_sheet"),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "Voice input",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            VoicePanel(
                status = state.status,
                agentName = state.agentName,
                settings = false,
                action = state.action,
                micHidden = state.micHidden,
                cleanupAvailable = state.cleanupOffered,
                cleanUp = state.cleanUp,
                cleanupError = state.cleanupError,
                cleanupNoticeSeen = state.cleanupNoticeSeen,
                onEnable = onEnable,
                onCancelInstall = onCancelInstall,
                onRetry = onRetry,
                onNotNow = onDismiss,
                onHideMic = onHideMic,
                onCleanUpChange = onCleanUpChange,
            )
            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}

/**
 * Voice input for the connected agent in every state, with the real costs
 * the agent reports. [settings] adds remove, reinstall, the mic toggle and
 * the model's license (Settings → Voice input); without it, this is the
 * enable sheet.
 *
 * [cleanupAvailable] offers the transcript cleanup toggle: in Settings for
 * an agent that supports it, in the enable sheet once voice is enabled in a
 * session that can use it, so the first dictation is already cleaned up.
 */
@Composable
fun VoicePanel(
    status: VoiceStatusResponse?,
    agentName: String,
    settings: Boolean,
    action: VoiceActionState,
    micHidden: Boolean,
    modifier: Modifier = Modifier,
    cleanupAvailable: Boolean = false,
    cleanUp: Boolean = false,
    cleanupError: String? = null,
    cleanupNoticeSeen: Boolean = false,
    onEnable: () -> Unit = {},
    onCancelInstall: () -> Unit = {},
    onRetry: () -> Unit = {},
    onRemove: () -> Unit = {},
    onReinstall: () -> Unit = {},
    onNotNow: () -> Unit = {},
    onHideMic: () -> Unit = {},
    onShowMicChange: (Boolean) -> Unit = {},
    onCleanUpChange: (Boolean) -> Unit = {},
) {
    var confirmRemove by rememberSaveable { mutableStateOf(false) }
    var showLicense by rememberSaveable { mutableStateOf(false) }
    var details by remember { mutableStateOf<String?>(null) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (status == null) {
            Text("Checking voice input on $agentName…", style = MaterialTheme.typography.bodyMedium)
            return@Column
        }
        val lowResources = status.host.recommendation == VoiceStatusResponse.RECOMMENDATION_LOW_RESOURCES
        when (status.state) {
            VoiceStatusResponse.STATE_NOT_INSTALLED -> {
                Text(
                    text = "Voice input on $agentName downloads about " +
                        "${VoiceStatusView.formatBytes(status.model.download_bytes)} once, then uses about " +
                        "${VoiceStatusView.formatBytes(status.model.disk_bytes)} of disk and about " +
                        "${VoiceStatusView.formatBytes(status.model.ram_bytes)} of memory while you dictate.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (lowResources) LowResourcesWarning(status.host.reasons)
                Text(
                    text = "$agentName transcribes what you say. The audio goes to it through the " +
                        "Mobaide relay, or straight to it in direct-connection mode, and is never " +
                        "stored. The text lands in the command field for you to review and is never " +
                        "sent on its own.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PixelButton(
                        text = "Enable voice input",
                        onClick = onEnable,
                        style = PixelButtonStyle.Primary,
                        enabled = !action.busy,
                        modifier = Modifier.testTag("voice_enable_button"),
                    )
                    if (!settings) PixelButton(text = "Not now", onClick = onNotNow)
                }
                if (!settings) {
                    PixelTextButton(onClick = onHideMic) { Text("Hide the mic for this agent") }
                }
            }
            VoiceStatusResponse.STATE_QUEUED,
            VoiceStatusResponse.STATE_DOWNLOADING,
            VoiceStatusResponse.STATE_VERIFYING -> {
                val label = VoiceStatusView.preparingLabel(status)
                Text(
                    text = label,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
                PixelProgressBar(progress = VoiceStatusView.progressPercent(status)?.let { it / 100f })
                Text(
                    text = if (status.repairing) {
                        "Your voice model stays; only a small update is downloading."
                    } else {
                        "You can close this. The mic turns on by itself when voice input is ready."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                PixelButton(
                    text = if (status.repairing) "Stop" else "Cancel",
                    onClick = onCancelInstall,
                    enabled = !action.busy,
                )
            }
            VoiceStatusResponse.STATE_READY -> {
                Text("Voice input is ready on $agentName.", style = MaterialTheme.typography.bodyMedium)
                if (settings) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        PixelButton(text = "Reinstall", onClick = onReinstall, enabled = !action.busy)
                        PixelButton(
                            text = "Remove",
                            onClick = { confirmRemove = true },
                            enabled = !action.busy,
                        )
                    }
                }
            }
            VoiceStatusResponse.STATE_FAILED -> {
                Text(
                    text = VoiceErrorCopy.install(status.error?.code.orEmpty(), status.repairing),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
                // The agent's raw message can carry host details: behind Details only.
                status.error?.message?.takeIf { it.isNotBlank() }?.let { message ->
                    PixelTextButton(onClick = { details = message }) { Text("Details") }
                }
                if (lowResources) LowResourcesWarning(status.host.reasons)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PixelButton(
                        text = "Retry",
                        onClick = onRetry,
                        style = PixelButtonStyle.Primary,
                        enabled = !action.busy,
                    )
                    if (settings) {
                        PixelButton(
                            text = "Remove",
                            onClick = { confirmRemove = true },
                            enabled = !action.busy,
                        )
                    }
                }
            }
            else -> {
                Text("$agentName can't run voice input.", style = MaterialTheme.typography.bodyMedium)
                status.host.reasons.forEach { reason ->
                    Text("• ${reason.message}", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        action.error?.let { error ->
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        if (settings || status.state == VoiceStatusResponse.STATE_NOT_INSTALLED) {
            ModelFacts(status.model)
        }
        if (settings && status.state != VoiceStatusResponse.STATE_UNSUPPORTED) {
            // The whole row is one target, like the cleanup row below it.
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .toggleable(value = !micHidden, role = Role.Checkbox, onValueChange = onShowMicChange)
                    .testTag("voice_show_mic_checkbox"),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                PixelCheckbox(checked = !micHidden, onCheckedChange = null)
                Text("Show the mic in the command field", style = MaterialTheme.typography.bodyMedium)
            }
        }
        val cleanupToggleShown = cleanupAvailable && if (settings) {
            status.state != VoiceStatusResponse.STATE_UNSUPPORTED
        } else {
            status.state in CLEANUP_SETUP_STATES
        }
        if (cleanupToggleShown) {
            VoiceCleanupToggle(
                checked = cleanUp,
                noticeSeen = cleanupNoticeSeen,
                agentName = agentName,
                onCheckedChange = onCleanUpChange,
            )
            cleanupError?.let { error ->
                Text(
                    text = error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier
                        .semantics { liveRegion = LiveRegionMode.Polite }
                        .testTag("voice_cleanup_error"),
                )
            }
        }
        if (settings && !status.model.notice.isNullOrBlank()) {
            PixelTextButton(onClick = { showLicense = true }) { Text("Model license") }
        }
    }
    if (confirmRemove && status != null) {
        PixelAlertDialog(
            onDismissRequest = { confirmRemove = false },
            title = { Text("Remove voice input?") },
            text = {
                Text(
                    "This frees about ${VoiceStatusView.formatBytes(status.model.disk_bytes)} " +
                        "on $agentName. You can enable it again later.",
                )
            },
            confirmButton = {
                PixelButton(
                    text = "Remove",
                    style = PixelButtonStyle.Danger,
                    onClick = {
                        confirmRemove = false
                        onRemove()
                    },
                )
            },
            dismissButton = { PixelTextButton(onClick = { confirmRemove = false }) { Text("Keep") } },
        )
    }
    details?.let { message ->
        PixelAlertDialog(
            onDismissRequest = { details = null },
            confirmButton = { PixelTextButton(onClick = { details = null }) { Text("Close") } },
            title = { Text("Details") },
            text = { Text(message) },
        )
    }
    if (showLicense && status?.model?.notice != null) {
        PixelAlertDialog(
            onDismissRequest = { showLicense = false },
            title = { Text("Model license") },
            text = {
                Text(
                    text = status.model.notice,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                )
            },
            confirmButton = { PixelTextButton(onClick = { showLicense = false }) { Text("Close") } },
        )
    }
}

/** The enable sheet offers cleanup once voice is on its way or ready. */
private val CLEANUP_SETUP_STATES = setOf(
    VoiceStatusResponse.STATE_QUEUED,
    VoiceStatusResponse.STATE_DOWNLOADING,
    VoiceStatusResponse.STATE_VERIFYING,
    VoiceStatusResponse.STATE_READY,
)

/**
 * The transcript cleanup checkbox, the whole row one target. The first time
 * it is turned on, a notice says where the text goes, and [onCheckedChange]
 * gets `true` only after "Got it".
 */
@Composable
fun VoiceCleanupToggle(
    checked: Boolean,
    noticeSeen: Boolean,
    agentName: String,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    var noticeOpen by rememberSaveable { mutableStateOf(false) }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .toggleable(
                value = checked,
                role = Role.Checkbox,
                onValueChange = { on ->
                    when {
                        !on -> onCheckedChange(false)
                        noticeSeen -> onCheckedChange(true)
                        else -> noticeOpen = true
                    }
                },
            )
            .testTag("voice_cleanup_checkbox"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelCheckbox(checked = checked, onCheckedChange = null)
        Text(VoiceCleanupCopy.TOGGLE_LABEL, style = MaterialTheme.typography.bodyMedium)
    }
    if (noticeOpen) {
        PixelAlertDialog(
            onDismissRequest = { noticeOpen = false },
            title = { Text(VoiceCleanupCopy.NOTICE_TITLE) },
            text = {
                Text(
                    VoiceCleanupCopy.notice(agentName),
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                )
            },
            confirmButton = {
                PixelButton(
                    text = "Got it",
                    style = PixelButtonStyle.Primary,
                    onClick = {
                        noticeOpen = false
                        onCheckedChange(true)
                    },
                    modifier = Modifier.testTag("voice_cleanup_notice_confirm"),
                )
            },
            dismissButton = { PixelTextButton(onClick = { noticeOpen = false }) { Text("Cancel") } },
        )
    }
}

object VoiceCleanupCopy {
    const val TOGGLE_LABEL =
        "Clean up dictated text with the session's AI agent: remove filler words, fix punctuation " +
            "and technical terms"
    const val NOTICE_TITLE = "Clean up dictated text"

    fun notice(agentName: String): String =
        "The session's AI agent on $agentName cleans up each transcript with its cheapest model: " +
            "Claude Haiku for Claude Code, GPT Luna for Codex, Gemini Flash for Antigravity; " +
            "OpenCode, GitHub Copilot and Cursor use their default models. It runs under your own " +
            "account: the text goes to that agent's provider and counts toward your usage. " +
            "Mobaide doesn't store it."
}

@Composable
private fun LowResourcesWarning(reasons: List<VoiceHostReason>) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .pixelFrame(MaterialTheme.pixel.warning)
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            text = "This computer may struggle with voice input.",
            style = MaterialTheme.typography.labelLarge,
        )
        reasons.forEach { reason ->
            Text("• ${reason.message}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun ModelFacts(model: VoiceModelInfo) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            text = "Model: ${model.name}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = "Languages: ${VoiceStatusView.languageNames(model.languages).joinToString(", ")}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Settings → Voice input, for the connected agent. */
@Composable
fun VoiceSettingsSection(
    state: VoiceUiState,
    onEnable: () -> Unit,
    onCancelInstall: () -> Unit,
    onRetry: () -> Unit,
    onRemove: () -> Unit,
    onReinstall: () -> Unit,
    onShowMicChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    onCleanUpChange: (Boolean) -> Unit = {},
) {
    Column(
        modifier = modifier
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .testTag("voice_settings_section"),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text = "Voice input", style = MaterialTheme.typography.titleMedium)
        when {
            !state.capabilityKnown -> Text(
                text = "Checking the connected agent…",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            state.capability == null -> Text(
                text = "Dictate into the command field: your own computer transcribes what you say. " +
                    "Connect to an agent that supports voice input (it updates itself) to set it up.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            else -> VoicePanel(
                status = state.status,
                agentName = state.agentName,
                settings = true,
                action = state.action,
                micHidden = state.micHidden,
                cleanupAvailable = state.cleanupSupported,
                cleanUp = state.cleanUp,
                cleanupError = state.cleanupError,
                cleanupNoticeSeen = state.cleanupNoticeSeen,
                onEnable = onEnable,
                onCancelInstall = onCancelInstall,
                onRetry = onRetry,
                onRemove = onRemove,
                onReinstall = onReinstall,
                onShowMicChange = onShowMicChange,
                onCleanUpChange = onCleanUpChange,
            )
        }
    }
}

/**
 * The microphone permission, asked on the mic tap: straight to the system
 * prompt the first time, after a short rationale once the user has declined
 * before, and to the denied state (with a link to the app's settings) when
 * Android will no longer prompt.
 */
@Composable
fun rememberMicPermissionRequest(onGranted: () -> Unit, onDenied: () -> Unit): () -> Unit {
    val context = LocalContext.current
    var showRationale by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) onGranted() else onDenied()
    }
    if (showRationale) {
        PixelAlertDialog(
            onDismissRequest = { showRationale = false },
            title = { Text("Microphone") },
            text = {
                Text(
                    "Mobaide records only while you dictate. The audio goes to your own computer, " +
                        "which turns it into text for the command field.",
                )
            },
            confirmButton = {
                PixelTextButton(onClick = {
                    showRationale = false
                    launcher.launch(Manifest.permission.RECORD_AUDIO)
                }) { Text("Continue") }
            },
            dismissButton = { PixelTextButton(onClick = { showRationale = false }) { Text("Not now") } },
        )
    }
    return {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        val activity = context.findActivity()
        when {
            granted -> onGranted()
            activity != null &&
                ActivityCompat.shouldShowRequestPermissionRationale(activity, Manifest.permission.RECORD_AUDIO) ->
                showRationale = true
            else -> launcher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
}

internal fun Context.findActivity(): Activity? {
    var current: Context? = this
    while (current is ContextWrapper) {
        if (current is Activity) return current
        current = current.baseContext
    }
    return null
}

private fun Context.openAppSettings() {
    startActivity(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
    )
}

private val previewStatus = VoiceStatusResponse(
    state = VoiceStatusResponse.STATE_NOT_INSTALLED,
    enabled = false,
    model = VoiceModelInfo(
        id = "parakeet-tdt-0.6b-v3-int8",
        name = "NVIDIA Parakeet TDT 0.6B v3 (int8)",
        languages = listOf("de", "en", "fr", "uk"),
        download_bytes = 682_365_212,
        disk_bytes = 733_578_420,
        ram_bytes = 700_000_000,
        notice = "CC BY 4.0",
    ),
    host = VoiceHostInfo(
        ram_total_bytes = 8_589_934_592,
        recommendation = VoiceStatusResponse.RECOMMENDATION_LOW_RESOURCES,
        reasons = listOf(VoiceHostReason("low_ram", "This computer has 8.6 GB of memory.")),
    ),
)

@Composable
private fun VoicePreviewContent() {
    Column(
        modifier = Modifier
            .width(360.dp)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            VoiceMicButton(VoiceMicMode.Ready, null, onClick = {})
            VoiceMicButton(VoiceMicMode.Off, null, onClick = {})
            VoiceMicButton(
                VoiceMicMode.Preparing,
                previewStatus.copy(state = VoiceStatusResponse.STATE_DOWNLOADING, progress = VoiceProgress(300, 1000)),
                onClick = {},
            )
            VoiceMicButton(VoiceMicMode.Failed, null, onClick = {})
        }
        VoiceDictationBar(
            state = DictationState.Recording(elapsedMs = 12_000, level = 0.6f, remainingSeconds = null, lagging = false),
            agentName = "MacBook-Pro",
            onStop = {}, onCancel = {}, onRetry = {}, onDismiss = {}, onRecordAgain = {},
        )
        VoiceDictationBar(
            state = DictationState.Cleaning("um can you uh check the off service"),
            agentName = "MacBook-Pro",
            onStop = {}, onCancel = {}, onRetry = {}, onDismiss = {}, onRecordAgain = {},
        )
        VoiceDictationBar(
            state = DictationState.Failed(VoiceCodes.NETWORK, retryable = true, message = null, hasAudio = true),
            agentName = "MacBook-Pro",
            onStop = {}, onCancel = {}, onRetry = {}, onDismiss = {}, onRecordAgain = {},
        )
        VoicePanel(
            status = previewStatus,
            agentName = "MacBook-Pro",
            settings = false,
            action = VoiceActionState(),
            micHidden = false,
        )
        VoicePanel(
            status = previewStatus.copy(
                state = VoiceStatusResponse.STATE_FAILED,
                error = VoiceErrorInfo("network", "Connection reset"),
            ),
            agentName = "MacBook-Pro",
            settings = true,
            action = VoiceActionState(),
            micHidden = false,
        )
        VoiceSettingsSection(
            state = previewUiState.copy(
                status = previewStatus.copy(state = VoiceStatusResponse.STATE_READY),
                cleanupSupported = true,
                cleanUp = true,
            ),
            onEnable = {}, onCancelInstall = {}, onRetry = {}, onRemove = {}, onReinstall = {},
            onShowMicChange = {},
        )
    }
}

private val previewUiState = VoiceUiState(
    micMode = VoiceMicMode.Off,
    capabilityKnown = true,
    capability = VoiceCapability(sampleRate = 16_000, maxSeconds = 120, maxChunkBytes = 65_536, cleanup = true),
    status = previewStatus,
    agentName = "MacBook-Pro",
)

@Preview(name = "Voice setup sheet light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun VoiceSetupSheetPreviewLight() {
    MobaideTheme(darkTheme = false) {
        VoiceSetupSheet(previewUiState, onDismiss = {}, onEnable = {}, onCancelInstall = {}, onRetry = {}, onHideMic = {})
    }
}

@Preview(name = "Voice setup sheet dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun VoiceSetupSheetPreviewDark() {
    MobaideTheme(darkTheme = true) {
        VoiceSetupSheet(previewUiState, onDismiss = {}, onEnable = {}, onCancelInstall = {}, onRetry = {}, onHideMic = {})
    }
}

@Preview(name = "Voice light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun VoicePreviewLight() {
    MobaideTheme(darkTheme = false) { VoicePreviewContent() }
}

@Preview(name = "Voice dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun VoicePreviewDark() {
    MobaideTheme(darkTheme = true) { VoicePreviewContent() }
}

package com.mobaide.app.ui.voice

import android.text.format.DateFormat
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.BuildConfig
import com.mobaide.app.data.network.dto.CloudVoiceLimits
import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.CloudVoiceUsage
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.voice.CloudVoiceCopy
import com.mobaide.app.data.voice.VoiceRoute
import com.mobaide.app.data.voice.VoiceRouting
import com.mobaide.app.data.voice.VoiceStatusView
import com.mobaide.app.ui.chat.parseIsoMillis
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelRadio
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.util.openUrlInCustomTab
import java.util.Date

/** Why "On this computer" cannot be chosen, or null when it can. */
internal fun localBlocker(
    agentName: String,
    localSupported: Boolean,
    status: VoiceStatusResponse?,
    loadFailed: Boolean,
    /** The agent did not answer whether it has voice at all. */
    probeFailed: Boolean = false,
): String? = when {
    probeFailed -> "Couldn’t reach $agentName."
    !localSupported -> "$agentName runs a Mobaide agent without on-computer voice input."
    status == null -> if (loadFailed) "Couldn’t reach $agentName." else "Checking $agentName…"
    status.state == VoiceStatusResponse.STATE_UNSUPPORTED -> "$agentName can’t run on-computer voice input."
    else -> null
}

/**
 * The first mic tap on an agent with no engine choice: where the speech is turned
 * into text. Mobaide Cloud is preselected; nothing is recorded or sent before the
 * user confirms. [onContinue] gets the chosen engine; the caller owns the save,
 * because the saved choice replaces this chooser with the next step.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun VoiceEngineChooser(
    cloud: CloudVoiceStatus,
    agentName: String,
    /** The agent has voice input of its own (`capabilities.voice`). */
    localSupported: Boolean,
    /** The agent's voice status; null while loading or when it has none. */
    status: VoiceStatusResponse?,
    loadFailed: Boolean,
    saving: Boolean,
    saveError: String?,
    onContinue: (engine: String) -> Unit,
    onNotNow: () -> Unit,
    onHideMic: () -> Unit,
    modifier: Modifier = Modifier,
    /** The agent did not answer whether it has voice at all. */
    probeFailed: Boolean = false,
) {
    var picked by rememberSaveable { mutableStateOf(CloudVoiceStatus.ENGINE_CLOUD) }
    val blocker = localBlocker(agentName, localSupported, status, loadFailed, probeFailed)
    // "On this computer" can stop being possible while it is picked.
    val selected = if (blocker != null) CloudVoiceStatus.ENGINE_CLOUD else picked
    Column(
        modifier = modifier.testTag("voice_engine_chooser"),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            text = buildAnnotatedString {
                append("Choose where your speech is turned into text for ")
                withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(agentName) }
                append(".")
            },
            style = MaterialTheme.typography.bodyMedium,
        )
        Column(
            modifier = Modifier.selectableGroup(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            EngineCard(
                selected = selected == CloudVoiceStatus.ENGINE_CLOUD,
                enabled = true,
                onSelect = { picked = CloudVoiceStatus.ENGINE_CLOUD },
                modifier = Modifier.testTag("voice_engine_card_cloud"),
            ) {
                EngineTitle(CloudVoiceCopy.CLOUD_NAME, recommended = true)
                Text(CloudVoiceCopy.CLOUD_SUMMARY, style = MaterialTheme.typography.bodyMedium)
                CardNote(CloudVoiceCopy.cloudDetails(cloud))
                PrivacyPolicyLink()
            }
            EngineCard(
                selected = selected == CloudVoiceStatus.ENGINE_LOCAL,
                enabled = blocker == null,
                onSelect = { picked = CloudVoiceStatus.ENGINE_LOCAL },
                modifier = Modifier.testTag("voice_engine_card_local"),
            ) {
                EngineTitle(CloudVoiceCopy.LOCAL_NAME, recommended = false)
                if (blocker != null) {
                    CardNote(blocker)
                } else {
                    Text(CloudVoiceCopy.localSummary(agentName), style = MaterialTheme.typography.bodyMedium)
                    if (status != null) CardNote(localCosts(status, agentName))
                }
            }
        }
        Text(
            text = "${CloudVoiceCopy.REVIEW_NOTE} You can change this later in Settings → Voice input.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        saveError?.let { EngineSaveError(it) }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PixelButton(
                text = "Continue",
                onClick = { onContinue(selected) },
                style = PixelButtonStyle.Primary,
                enabled = !saving,
                modifier = Modifier.testTag("voice_engine_continue_button"),
            )
            PixelButton(text = "Not now", onClick = onNotNow)
        }
        PixelTextButton(onClick = onHideMic) { Text("Hide the mic for this agent") }
    }
}

/** The real costs of the computer's own voice, as the enable sheet states them. */
private fun localCosts(status: VoiceStatusResponse, agentName: String): String = buildString {
    if (status.state == VoiceStatusResponse.STATE_NOT_INSTALLED) {
        append(
            "A one-time download of about ${VoiceStatusView.formatBytes(status.model.download_bytes)}, " +
                "then about ${VoiceStatusView.formatBytes(status.model.disk_bytes)} of disk and about " +
                "${VoiceStatusView.formatBytes(status.model.ram_bytes)} of memory while you dictate. ",
        )
    }
    append("The audio travels through the Mobaide relay to $agentName and is never stored.")
    if (status.host.recommendation == VoiceStatusResponse.RECOMMENDATION_LOW_RESOURCES) {
        append(" This computer may struggle with it.")
    }
}

/**
 * One radio card of the chooser: the whole card is the target, announced as
 * a radio button with its text. A card that cannot be chosen says why.
 */
@Composable
private fun EngineCard(
    selected: Boolean,
    enabled: Boolean,
    onSelect: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(if (selected) MaterialTheme.colorScheme.surfaceContainerHighest else Color.Transparent)
            .pixelFrame(if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.colorScheme.outline)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onSelect)
            .padding(top = 4.dp, end = 12.dp, bottom = 10.dp),
    ) {
        PixelRadio(selected = selected, onClick = null, enabled = enabled)
        Column(
            modifier = Modifier
                .weight(1f)
                .padding(top = 12.dp)
                .alpha(if (enabled) 1f else 0.6f),
            verticalArrangement = Arrangement.spacedBy(4.dp),
            content = content,
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EngineTitle(name: String, recommended: Boolean) {
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            text = name,
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.align(Alignment.CenterVertically),
        )
        if (recommended) {
            PixelChip(
                text = CloudVoiceCopy.RECOMMENDED,
                tone = MaterialTheme.pixel.ok,
                modifier = Modifier.align(Alignment.CenterVertically),
            )
        }
    }
}

@Composable
private fun CardNote(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
internal fun EngineSaveError(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error,
        modifier = modifier
            .semantics { liveRegion = LiveRegionMode.Polite }
            .testTag("voice_engine_error"),
    )
}

@Composable
private fun PrivacyPolicyLink(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    PixelTextButton(
        onClick = { openUrlInCustomTab(context, BuildConfig.PRIVACY_POLICY_URL) },
        modifier = modifier.testTag("voice_privacy_policy_link"),
        contentPadding = PaddingValues(0.dp),
    ) {
        Text(CloudVoiceCopy.PRIVACY_POLICY)
    }
}

/**
 * Cloud voice input for the connected agent: ready, unavailable or out of today's
 * allowance. Without [settings] it is what the mic opens while cloud voice input
 * cannot record; with it, Settings → Voice input, which adds today's use, the
 * limits, the privacy note and the mic toggle.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun CloudVoicePanel(
    cloud: CloudVoiceStatus,
    agentName: String,
    settings: Boolean,
    /** A fresh status is on its way. */
    checking: Boolean,
    onRecheck: () -> Unit,
    modifier: Modifier = Modifier,
    /** Switch this agent to on-computer voice input; null when it has none. */
    onUseLocal: (() -> Unit)? = null,
    /** An engine choice is being saved. */
    saving: Boolean = false,
    /** The user's "Try again" got its answer, and the speech service is still down. */
    rechecked: Boolean = false,
    micHidden: Boolean = false,
    onShowMicChange: (Boolean) -> Unit = {},
) {
    val context = LocalContext.current
    val limit = VoiceRouting.formatAllowance(cloud.usage.limit_seconds)
    val meanwhile = if (onUseLocal != null) " You can dictate with $agentName itself meanwhile." else ""
    val useLocal: (@Composable () -> Unit)? = onUseLocal?.let { switch ->
        {
            PixelButton(
                text = "Use this computer instead",
                onClick = switch,
                enabled = !saving,
                modifier = Modifier.testTag("cloud_voice_use_local_button"),
            )
        }
    }
    Column(
        modifier = modifier.testTag("cloud_voice_panel"),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        when (cloud.state) {
            CloudVoiceStatus.STATE_UNAVAILABLE -> {
                Text(
                    text = "Cloud voice input is unavailable right now.",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
                // One slot for both lines: a note added under the buttons
                // would move them under the finger on every retry. A recheck
                // that finds the service still down says so here, so it does
                // not look like a tap that did nothing.
                PanelNote(
                    text = if (rechecked && !checking) {
                        "Still not answering. Try again in a few seconds."
                    } else {
                        "Mobaide’s speech service isn’t answering. This is usually short."
                    } + meanwhile,
                    modifier = Modifier
                        .semantics { liveRegion = LiveRegionMode.Polite }
                        .testTag("cloud_voice_unavailable_note"),
                )
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    PixelButton(
                        text = if (checking) "Checking…" else "Try again",
                        onClick = onRecheck,
                        style = PixelButtonStyle.Primary,
                        enabled = !checking,
                        modifier = Modifier.testTag("cloud_voice_recheck_button"),
                    )
                    useLocal?.invoke()
                }
            }
            CloudVoiceStatus.STATE_LIMIT_REACHED -> {
                // The device's own time format, as for a chat's usage-limit wait.
                val resetAt = remember(cloud.usage.resets_at, context) {
                    parseIsoMillis(cloud.usage.resets_at)?.let { DateFormat.getTimeFormat(context).format(Date(it)) }
                }
                Text(
                    text = "You’ve used today’s $limit of cloud voice input.",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
                PanelNote((if (resetAt == null) "It resets tomorrow." else "It resets at $resetAt.") + meanwhile)
                useLocal?.invoke()
            }
            else -> Text(
                text = if (settings) {
                    "${CloudVoiceCopy.CLOUD_NAME} turns your speech into text for $agentName."
                } else {
                    "Cloud voice input is ready. Close this and tap the mic to dictate."
                },
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        if (settings) {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (CloudVoiceCopy.hasDailyLimit(cloud)) {
                    PanelNote(
                        "Used today: " + VoiceRouting.formatUsage(cloud.usage.used_seconds, cloud.usage.limit_seconds),
                    )
                }
                PanelNote("Recording: Up to ${VoiceRouting.formatAllowance(cloud.limits.max_seconds)} each")
            }
            Column {
                PanelNote(
                    "Recordings go to Mobaide’s speech service over an encrypted connection and are " +
                        "not kept. The text is kept for a short time to prevent abuse.",
                )
                PrivacyPolicyLink()
            }
            ShowMicToggle(micHidden = micHidden, onShowMicChange = onShowMicChange)
        }
    }
}

@Composable
private fun PanelNote(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier,
    )
}

/**
 * "Speech recognition" in Settings → Voice input: Mobaide Cloud or the
 * computer. Stored on the server, so every device follows. [onEngineChange]
 * gets a `CloudVoiceStatus` engine, and only one that is not chosen already.
 */
@Composable
fun VoiceEngineSetting(
    route: VoiceRoute,
    /** Why "On this computer" cannot be chosen, or null when it can. */
    localBlocker: String?,
    saveError: String?,
    onEngineChange: (engine: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    // An agent already on its own voice keeps the option, whatever its state now.
    val localDisabled = localBlocker != null && route != VoiceRoute.Local
    Column(modifier = modifier.testTag("voice_engine_setting")) {
        Text("Speech recognition", style = MaterialTheme.typography.labelLarge)
        Column(modifier = Modifier.selectableGroup()) {
            EngineRow(
                name = CloudVoiceCopy.CLOUD_NAME,
                recommended = true,
                selected = route == VoiceRoute.Cloud,
                enabled = true,
                onSelect = { if (route != VoiceRoute.Cloud) onEngineChange(CloudVoiceStatus.ENGINE_CLOUD) },
                modifier = Modifier.testTag("voice_engine_option_cloud"),
            )
            EngineRow(
                name = CloudVoiceCopy.LOCAL_NAME,
                recommended = false,
                selected = route == VoiceRoute.Local,
                enabled = !localDisabled,
                onSelect = { if (route != VoiceRoute.Local) onEngineChange(CloudVoiceStatus.ENGINE_LOCAL) },
                modifier = Modifier.testTag("voice_engine_option_local"),
                // A choice that cannot be made says why, as its card in the chooser does.
                note = localBlocker.takeIf { localDisabled },
            )
        }
        saveError?.let { EngineSaveError(it) }
    }
}

@Composable
private fun EngineRow(
    name: String,
    recommended: Boolean,
    selected: Boolean,
    enabled: Boolean,
    onSelect: () -> Unit,
    modifier: Modifier = Modifier,
    note: String? = null,
) {
    // The note is inside the option, so TalkBack reads it with the disabled radio button.
    Column(
        modifier = modifier
            .fillMaxWidth()
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onSelect),
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            PixelRadio(selected = selected, onClick = null, enabled = enabled)
            Text(
                text = name,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier
                    .weight(1f, fill = false)
                    .alpha(if (enabled) 1f else 0.6f),
            )
            if (recommended) PixelChip(CloudVoiceCopy.RECOMMENDED, tone = MaterialTheme.pixel.ok)
        }
        if (note != null) {
            Text(
                text = note,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .padding(start = ENGINE_NOTE_INSET, bottom = 8.dp)
                    .testTag("voice_engine_option_local_note"),
            )
        }
    }
}

/** Under the option's name: past the radio's 48dp target and the 8dp gap. */
private val ENGINE_NOTE_INSET = 56.dp

internal val previewCloudStatus = CloudVoiceStatus(
    state = CloudVoiceStatus.STATE_READY,
    limits = CloudVoiceLimits(sample_rate = 16_000, max_seconds = 120, max_chunk_bytes = 65_536),
    usage = CloudVoiceUsage(used_seconds = 742, limit_seconds = 1_800, resets_at = "2026-11-23T00:00:00Z"),
    engines = emptyMap(),
)

@Composable
private fun CloudVoicePreviewContent() {
    Column(
        modifier = Modifier
            .width(360.dp)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        VoiceEngineChooser(
            cloud = previewCloudStatus,
            agentName = "MacBook-Pro",
            localSupported = true,
            status = previewStatus,
            loadFailed = false,
            saving = false,
            saveError = null,
            onContinue = {}, onNotNow = {}, onHideMic = {},
        )
        VoiceEngineChooser(
            cloud = previewCloudStatus,
            agentName = "MacBook-Pro",
            localSupported = false,
            status = null,
            loadFailed = false,
            saving = false,
            saveError = CloudVoiceCopy.CHOICE_SAVE_FAILED,
            onContinue = {}, onNotNow = {}, onHideMic = {},
        )
        CloudVoicePanel(
            cloud = previewCloudStatus.copy(state = CloudVoiceStatus.STATE_UNAVAILABLE),
            agentName = "MacBook-Pro",
            settings = false,
            checking = false,
            onRecheck = {},
            onUseLocal = {},
            rechecked = true,
        )
        CloudVoicePanel(
            cloud = previewCloudStatus.copy(state = CloudVoiceStatus.STATE_LIMIT_REACHED),
            agentName = "MacBook-Pro",
            settings = false,
            checking = false,
            onRecheck = {},
        )
        VoiceEngineSetting(
            route = VoiceRoute.Cloud,
            localBlocker = localBlocker("MacBook-Pro", localSupported = false, status = null, loadFailed = false),
            saveError = null,
            onEngineChange = {},
        )
        CloudVoicePanel(
            cloud = previewCloudStatus,
            agentName = "MacBook-Pro",
            settings = true,
            checking = false,
            onRecheck = {},
        )
    }
}

@Preview(name = "Cloud voice light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun CloudVoicePreviewLight() {
    MobaideTheme(darkTheme = false) { CloudVoicePreviewContent() }
}

@Preview(name = "Cloud voice dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun CloudVoicePreviewDark() {
    MobaideTheme(darkTheme = true) { CloudVoicePreviewContent() }
}

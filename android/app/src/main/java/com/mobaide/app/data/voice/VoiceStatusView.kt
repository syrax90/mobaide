package com.mobaide.app.data.voice

import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import java.util.Locale
import kotlin.math.roundToLong

/**
 * How the mic button looks for the connected agent.
 *
 * - [Hidden]: no voice on this agent (too old, `unsupported`, a state this
 *   client does not know, status not loaded yet) or the user hid the mic.
 */
enum class VoiceMicMode { Hidden, Off, Preparing, Failed, Ready }

object VoiceStatusView {
    fun micMode(
        capability: VoiceCapability?,
        status: VoiceStatusResponse?,
        hiddenByUser: Boolean,
    ): VoiceMicMode {
        if (capability == null || status == null) return VoiceMicMode.Hidden
        val mode = when (status.state) {
            VoiceStatusResponse.STATE_NOT_INSTALLED -> VoiceMicMode.Off
            VoiceStatusResponse.STATE_QUEUED,
            VoiceStatusResponse.STATE_DOWNLOADING,
            VoiceStatusResponse.STATE_VERIFYING -> VoiceMicMode.Preparing
            VoiceStatusResponse.STATE_FAILED -> VoiceMicMode.Failed
            VoiceStatusResponse.STATE_READY -> VoiceMicMode.Ready
            else -> VoiceMicMode.Hidden
        }
        return if (hiddenByUser) VoiceMicMode.Hidden else mode
    }

    /** Whole percent of the install download, or null when unknown. */
    fun progressPercent(status: VoiceStatusResponse): Int? {
        val progress = status.progress ?: return null
        if (progress.total_bytes <= 0) return null
        return (progress.done_bytes * 100 / progress.total_bytes).toInt().coerceIn(0, 100)
    }

    /** One line for the preparing states, e.g. "Preparing voice… 43%". */
    fun preparingLabel(status: VoiceStatusResponse): String {
        if (status.state == VoiceStatusResponse.STATE_QUEUED) {
            return "Waiting for another voice install on this computer…"
        }
        if (status.state == VoiceStatusResponse.STATE_VERIFYING) return "Checking voice input…"
        val progress = status.progress
        if (status.repairing && progress != null) {
            val left = (progress.total_bytes - progress.done_bytes).coerceAtLeast(0)
            return "Updating voice (≈${formatBytes(left)})…"
        }
        val percent = progressPercent(status) ?: return "Preparing voice…"
        return "Preparing voice… $percent%"
    }

    /** Decimal units, as disk and download sizes are usually quoted. */
    fun formatBytes(bytes: Long): String = when {
        bytes >= 1_000_000_000 -> String.format(Locale.US, "%.1f GB", bytes / 1e9)
        bytes >= 1_000_000 -> "${(bytes / 1e6).roundToLong()} MB"
        bytes >= 1_000 -> "${(bytes / 1e3).roundToLong()} KB"
        else -> "$bytes B"
    }

    /** ISO 639-1 codes as English language names, sorted; unknown codes stay as codes. */
    fun languageNames(codes: List<String>): List<String> = codes
        .map { code ->
            Locale.forLanguageTag(code).getDisplayLanguage(Locale.ENGLISH)
                .takeIf { it.isNotBlank() && !it.equals(code, ignoreCase = true) } ?: code
        }
        .sorted()

    /** "4:05" */
    fun formatDuration(ms: Long): String {
        val total = (ms / 1000).coerceAtLeast(0)
        return "%d:%02d".format(Locale.US, total / 60, total % 60)
    }
}

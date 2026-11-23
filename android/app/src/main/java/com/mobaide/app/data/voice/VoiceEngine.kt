package com.mobaide.app.data.voice

import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import kotlin.math.ceil

/**
 * How one agent's dictations are transcribed: by Mobaide's speech service
 * ([Cloud]), by the agent's own model ([Local]), or not decided yet
 * ([Choose]: the mic opens the chooser).
 */
enum class VoiceRoute { Cloud, Local, Choose }

object VoiceRouting {
    /** Cloud voice input exists for this account, whether or not it can record right now. */
    fun cloudOffered(cloud: CloudVoiceStatus?): Boolean = when (cloud?.state) {
        CloudVoiceStatus.STATE_READY,
        CloudVoiceStatus.STATE_UNAVAILABLE,
        CloudVoiceStatus.STATE_LIMIT_REACHED -> true
        else -> false
    }

    /**
     * - Cloud not offered: on-computer voice, exactly as before cloud existed.
     * - No choice yet: an agent whose own voice input is already enabled keeps
     *   it; any other agent asks.
     */
    fun route(cloud: CloudVoiceStatus?, agentId: String?, localStatus: VoiceStatusResponse?): VoiceRoute {
        if (agentId == null || cloud == null || !cloudOffered(cloud)) return VoiceRoute.Local
        return when (cloud.engines[agentId]) {
            CloudVoiceStatus.ENGINE_CLOUD -> VoiceRoute.Cloud
            CloudVoiceStatus.ENGINE_LOCAL -> VoiceRoute.Local
            else -> if (localStatus?.enabled == true) VoiceRoute.Local else VoiceRoute.Choose
        }
    }

    /**
     * The session mic. [VoiceMicMode.Unavailable] is cloud voice input that
     * cannot record now; a tap explains. Hiding the mic hides it in every route.
     */
    fun micMode(
        route: VoiceRoute,
        cloud: CloudVoiceStatus?,
        localMode: VoiceMicMode,
        hiddenByUser: Boolean,
    ): VoiceMicMode = when {
        route == VoiceRoute.Local -> localMode
        hiddenByUser -> VoiceMicMode.Hidden
        route == VoiceRoute.Choose -> VoiceMicMode.Off
        cloud?.state == CloudVoiceStatus.STATE_READY -> VoiceMicMode.Ready
        else -> VoiceMicMode.Unavailable
    }

    /** "2 minutes", "30 minutes", "90 seconds". */
    fun formatAllowance(seconds: Int): String {
        if (seconds % 60 != 0) return "$seconds seconds"
        val minutes = seconds / 60
        return if (minutes == 1) "1 minute" else "$minutes minutes"
    }

    /**
     * "13 of 30 minutes", in the unit of the limit: minutes (rounded up, so
     * any use shows) for a limit in whole minutes, seconds otherwise.
     */
    fun formatUsage(usedSeconds: Int, limitSeconds: Int): String {
        // The recording that crosses the limit is still transcribed, so the
        // count can pass it; "31 of 30 minutes" would only confuse.
        val used = minOf(maxOf(0, usedSeconds), limitSeconds)
        val shown = if (limitSeconds % 60 != 0) used else ceil(used / 60.0).toInt()
        return "$shown of ${formatAllowance(limitSeconds)}"
    }
}

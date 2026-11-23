package com.mobaide.app.data.voice

import com.mobaide.app.data.network.dto.CloudVoiceStatus

/**
 * Copy of cloud voice input, shared by the chooser, the settings and the
 * dictation bar.
 */
object CloudVoiceCopy {
    const val CLOUD_NAME = "Mobaide Cloud"
    const val LOCAL_NAME = "On this computer"
    const val RECOMMENDED = "Recommended"
    const val PRIVACY_POLICY = "Privacy policy"

    const val CLOUD_SUMMARY = "Works right away. Nothing to install on your computer."

    fun cloudDetails(cloud: CloudVoiceStatus): String {
        val perDay = if (hasDailyLimit(cloud)) {
            " and ${VoiceRouting.formatAllowance(cloud.usage.limit_seconds)} per day"
        } else {
            ""
        }
        return "Your recording goes over an encrypted connection to Mobaide’s speech service, which " +
            "turns it into text. The audio is not kept. The text is kept for a short time to " +
            "prevent abuse. Up to ${VoiceRouting.formatAllowance(cloud.limits.max_seconds)} per recording$perDay."
    }

    /** A `limit_seconds` of 0 means the server sets no daily limit. */
    fun hasDailyLimit(cloud: CloudVoiceStatus): Boolean = cloud.usage.limit_seconds > 0

    fun localSummary(agentName: String): String =
        "Private: $agentName turns your speech into text itself."

    const val REVIEW_NOTE = "The text appears for you to review and is never sent on its own."

    const val CHOICE_SAVE_FAILED = "Couldn’t save your choice. Check the connection and try again."
    const val SETTING_SAVE_FAILED = "Couldn’t save this setting. Check the connection and try again."
}

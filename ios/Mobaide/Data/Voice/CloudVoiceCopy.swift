import Foundation

/// Copy of cloud voice input, shared by the chooser, the settings and the
/// dictation bar.
enum CloudVoiceCopy {
    static let cloudName = "Mobaide Cloud"
    static let localName = "On this computer"
    static let recommended = "Recommended"
    static let engineLabel = "Speech recognition"

    static let cloudSummary = "Works right away. Nothing to install on your computer."

    static func cloudDetails(_ cloud: CloudVoiceStatus) -> String {
        let perDay = hasDailyLimit(cloud)
            ? " and \(formatAllowance(seconds: cloud.usage.limit_seconds)) per day"
            : ""
        return "Your recording goes over an encrypted connection to Mobaide's speech service, which "
            + "turns it into text. The audio is not kept. The text is kept for a short time to "
            + "prevent abuse. Up to \(formatAllowance(seconds: cloud.limits.max_seconds)) per recording\(perDay)."
    }

    /// A `limit_seconds` of 0 means the server sets no daily limit.
    static func hasDailyLimit(_ cloud: CloudVoiceStatus) -> Bool {
        cloud.usage.limit_seconds > 0
    }

    static func localSummary(agent: String) -> String {
        "Private: \(agent) turns your speech into text itself."
    }

    static let reviewNote = "The text appears for you to review and is never sent on its own."

    static let settingsPrivacyNote =
        "Recordings go to Mobaide's speech service over an encrypted connection and are not kept. "
            + "The text is kept for a short time to prevent abuse."

    static let choiceSaveFailed = "Couldn't save your choice. Check the connection and try again."
    static let settingSaveFailed = "Couldn't save this setting. Check the connection and try again."

    static func checking(agent: String) -> String { "Checking \(agent)…" }

    static func unreachable(agent: String) -> String { "Couldn't reach \(agent)." }

    /// Why "On this computer" cannot be chosen once the agent answered what
    /// voice it has, or nil when it can.
    static func localBlocker(
        agent: String,
        localSupported: Bool,
        status: VoiceStatusResponse?,
        loadFailed: Bool
    ) -> String? {
        guard localSupported else { return "\(agent) runs a Mobaide agent without on-computer voice input." }
        guard let status else { return loadFailed ? unreachable(agent: agent) : checking(agent: agent) }
        if status.state == VoiceStatusResponse.stateUnsupported {
            return "\(agent) can't run on-computer voice input."
        }
        return nil
    }

    /// The costs of on-computer voice, under its card in the chooser.
    static func localDetails(agent: String, status: VoiceStatusResponse) -> String {
        var text = ""
        if status.state == VoiceStatusResponse.stateNotInstalled {
            text += "A one-time download of about \(VoiceStatusView.formatBytes(status.model.download_bytes)), "
                + "then about \(VoiceStatusView.formatBytes(status.model.disk_bytes)) of disk and about "
                + "\(VoiceStatusView.formatBytes(status.model.ram_bytes)) of memory while you dictate. "
        }
        text += "The audio travels through the Mobaide relay to \(agent) and is never stored."
        if status.host.recommendation == VoiceStatusResponse.recommendationLowResources {
            text += " This computer may struggle with it."
        }
        return text
    }

    /// "2 minutes", "30 minutes", "90 seconds".
    static func formatAllowance(seconds: Int) -> String {
        guard seconds % 60 == 0 else { return "\(seconds) seconds" }
        let minutes = seconds / 60
        return minutes == 1 ? "1 minute" : "\(minutes) minutes"
    }

    /// "13 of 30 minutes", in the unit of the limit: minutes (rounded up, so
    /// any use shows) for a limit in whole minutes, seconds otherwise.
    static func formatUsage(usedSeconds: Int, limitSeconds: Int) -> String {
        // The recording that crosses the limit is still transcribed, so the
        // count can pass it; "31 of 30 minutes" would only confuse.
        let used = min(max(0, usedSeconds), limitSeconds)
        let limit = formatAllowance(seconds: limitSeconds)
        guard limitSeconds % 60 == 0 else { return "\(used) of \(limit)" }
        return "\((used + 59) / 60) of \(limit)"
    }

    static let stillUnavailable = "Still not answering. Try again in a few seconds."

    /// The local time the daily limit resets, e.g. "01:00"; empty when unreadable.
    static func resetTimeLabel(_ resetsAt: String) -> String {
        let date = (try? Date(resetsAt, strategy: .iso8601))
            ?? (try? Date(resetsAt, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)))
        return date?.formatted(date: .omitted, time: .shortened) ?? ""
    }
}

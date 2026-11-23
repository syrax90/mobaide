import Foundation

/// How the mic button looks for the connected agent.
///
/// - `hidden`: no voice on this agent (too old, `unsupported`, a state this
///   client does not know, status not loaded yet) or the user hid the mic.
/// - `unavailable`: cloud voice input that cannot record now (the speech
///   service is down, or today's limit is used up); a tap explains.
enum VoiceMicMode: Equatable, Sendable {
    case hidden, off, preparing, failed, ready, unavailable
}

enum VoiceStatusView {
    static func micMode(capability: VoiceCapability?, status: VoiceStatusResponse?, hiddenByUser: Bool) -> VoiceMicMode {
        guard capability != nil, let status else { return .hidden }
        let mode: VoiceMicMode
        switch status.state {
        case VoiceStatusResponse.stateNotInstalled: mode = .off
        case VoiceStatusResponse.stateQueued, VoiceStatusResponse.stateDownloading, VoiceStatusResponse.stateVerifying:
            mode = .preparing
        case VoiceStatusResponse.stateFailed: mode = .failed
        case VoiceStatusResponse.stateReady: mode = .ready
        default: mode = .hidden
        }
        return hiddenByUser ? .hidden : mode
    }

    /// The session mic. `localMode` is `micMode` of the agent's own voice,
    /// which decides alone while that is the route.
    static func composerMicMode(
        route: VoiceRoute,
        cloud: CloudVoiceStatus?,
        localMode: VoiceMicMode,
        hiddenByUser: Bool
    ) -> VoiceMicMode {
        if route == .local { return localMode }
        if hiddenByUser { return .hidden }
        if route == .choose { return .off }
        return cloud?.state == CloudVoiceStatus.stateReady ? .ready : .unavailable
    }

    /// Whole percent of the install download, or nil when unknown.
    static func progressPercent(_ status: VoiceStatusResponse) -> Int? {
        guard let progress = status.progress, progress.total_bytes > 0 else { return nil }
        return Int(min(100, max(0, progress.done_bytes * 100 / progress.total_bytes)))
    }

    /// One line for the preparing states, e.g. "Preparing voice… 43%".
    static func preparingLabel(_ status: VoiceStatusResponse) -> String {
        if status.state == VoiceStatusResponse.stateQueued {
            return "Waiting for another voice install on this computer…"
        }
        if status.state == VoiceStatusResponse.stateVerifying { return "Checking voice input…" }
        if status.repairing, let progress = status.progress {
            return "Updating voice (≈\(formatBytes(max(0, progress.total_bytes - progress.done_bytes))))…"
        }
        guard let percent = progressPercent(status) else { return "Preparing voice…" }
        return "Preparing voice… \(percent)%"
    }

    /// Decimal units, as disk and download sizes are usually quoted.
    static func formatBytes(_ bytes: Int64) -> String {
        switch bytes {
        case 1_000_000_000...: return String(format: "%.1f GB", Double(bytes) / 1e9)
        case 1_000_000...: return "\(Int((Double(bytes) / 1e6).rounded())) MB"
        case 1_000...: return "\(Int((Double(bytes) / 1e3).rounded())) KB"
        default: return "\(bytes) B"
        }
    }

    /// ISO 639-1 codes as English language names, sorted; unknown codes stay as codes.
    static func languageNames(_ codes: [String]) -> [String] {
        let english = Locale(identifier: "en")
        return codes
            .map { english.localizedString(forLanguageCode: $0) ?? $0 }
            .sorted()
    }

    /// "4:05"
    static func formatDuration(ms: Int) -> String {
        let total = max(0, ms / 1000)
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

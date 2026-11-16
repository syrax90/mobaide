import Foundation

/// Attention reason → notification copy (port of Android
/// `AttentionNotifier.contentFor`). The label fallback and per-reason strings
/// mirror Android byte-for-byte so both platforms alert identically.
enum AttentionContent {
    static let fallbackLabel = "Claude session"

    /// `title` (non-blank) → `project_name` (non-blank) → a label naming the
    /// session's kind ("Codex session", …).
    static func label(for session: SessionResponse) -> String {
        if let title = session.title, !title.isBlank { return title }
        if let projectName = session.project_name, !projectName.isBlank { return projectName }
        return kindFallbackLabel(session)
    }

    /// An absent `kind` decodes as `.claude`, which is right: an agent too old
    /// to send it only alerted for Claude sessions.
    static func kindFallbackLabel(_ session: SessionResponse) -> String {
        switch session.kind {
        case .claude: return fallbackLabel
        case .codex: return "Codex session"
        case .antigravity: return "Antigravity session"
        case .opencode: return "OpenCode session"
        case .copilot: return "GitHub Copilot session"
        case .cursor: return "Cursor session"
        case .pi: return "Pi session"
        case .autoScan: return "Auto Scan session"
        case .shell: return "Terminal session"
        case .custom:
            if let name = session.kind_label, !name.isBlank { return "\(name) session" }
            return "Agent session"
        }
    }

    /// Title/body for a session's `attention` reason, or nil when the reason
    /// is absent/unrecognized — in which case no notification is posted
    /// (parity with `AttentionNotifier` returning null).
    static func content(for session: SessionResponse) -> (title: String, body: String)? {
        content(reason: session.attention, label: label(for: session), exitReason: session.exit_reason)
    }

    /// Title/body of an alert for `reason`, with `label` as the name of what
    /// needs the user; nil for a reason this build does not know. One table
    /// for sessions and Herdr panes, as on the relay and the web client.
    static func content(
        reason: String?, label: String, exitReason: String? = nil
    ) -> (title: String, body: String)? {
        switch reason {
        case "awaiting_permission":
            return ("Permission needed", "\(label) is waiting for your approval")
        case "awaiting_os_permission":
            return (
                "Action needed on your Mac",
                "\(label) is paused — your Mac is asking to allow file access. Approve it on the Mac to continue."
            )
        case "turn_finished":
            return ("Your move", "\(label) finished its turn — tap to continue")
        case "turn_failed":
            return ("Turn failed", "\(label) hit an error — tap to see what happened")
        case "ended":
            let suffix = exitReason.flatMap { $0.isBlank ? nil : " (\($0))" } ?? ""
            return ("Session ended\(suffix)", label)
        default:
            return nil
        }
    }

    /// Sessions whose attention warrants a notification — the "attention set"
    /// the foreground reconcile works from, in snapshot order.
    static func attentionSessions(in sessions: [SessionResponse]) -> [SessionResponse] {
        sessions.filter { content(for: $0) != nil }
    }

    /// Title/body of a Herdr alert: the session copy with the pane title as
    /// the label. Nil for a reason this build does not know.
    static func content(for attention: HerdrAttention) -> (title: String, body: String)? {
        content(reason: attention.reason, label: attention.title.isBlank ? "Herdr agent" : attention.title)
    }
}

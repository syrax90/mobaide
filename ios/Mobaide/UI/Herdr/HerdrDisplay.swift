import Foundation

/// What a pane shows; ordered "needs you first", the order of the Agents list.
enum HerdrStatus: Int, Comparable, Sendable {
    case blocked, failed, done, working, idle, unknown, none

    static func < (lhs: HerdrStatus, rhs: HerdrStatus) -> Bool { lhs.rawValue < rhs.rawValue }

    /// The status asks for the user: the session-list card shows its chip.
    var needsUser: Bool { self == .blocked || self == .failed || self == .done }
}

struct HerdrPaneRow: Identifiable, Equatable, Sendable {
    let server: HerdrServer
    let workspace: HerdrWorkspace
    let tab: HerdrTab
    let pane: HerdrPane
    let status: HerdrStatus

    /// Stable across snapshots, for list identity. The key that names a pane
    /// to the alert code and to the row that is being opened.
    var id: String {
        ForegroundSessionTracker.herdrPaneKey(serverId: server.server_id, paneId: pane.pane_id)
    }
}

/// Words and order of the Herdr dashboard, shared by its screens (Android
/// `HerdrDisplay.kt`, web `herdrDisplay.ts`).
enum HerdrDisplay {
    private static let agentLabels: [String: String] = [
        "claude": "Claude Code",
        "codex": "Codex",
        "antigravity": "Antigravity",
        "opencode": "OpenCode",
        "copilot": "GitHub Copilot",
        "cursor": "Cursor",
        "pi": "Pi",
    ]

    /// The display name of an agent; a name Mobaide does not know stays as it is.
    static func agentLabel(_ agent: String) -> String { agentLabels[agent] ?? agent }

    /// A pane's status on this device. A `done` whose `turn` the user already
    /// saw here is idle: "seen" is kept per device, because marking a pane seen
    /// in herdr would move the view of a user at the desktop.
    static func status(_ pane: HerdrPane, seenTurn: Int64?) -> HerdrStatus {
        guard pane.agent != nil else { return .none }
        switch pane.status {
        case "blocked": return .blocked
        case "failed": return .failed
        case "working": return .working
        case "idle": return .idle
        case "done":
            if let turn = pane.turn, turn == seenTurn { return .idle }
            return .done
        default: return .unknown
        }
    }

    /// The chip of a status, in the tones the session cards use.
    static func chip(_ status: HerdrStatus) -> (text: String, tone: PixelTone)? {
        switch status {
        case .blocked: return ("Needs you", .warning)
        case .failed: return ("Failed", .warning)
        case .done: return ("Done", .ok)
        case .working: return ("Working", .accent)
        case .idle: return ("Idle", .muted)
        case .unknown, .none: return nil
        }
    }

    /// The tone of the leading square of a row that does not work.
    static func markTone(_ status: HerdrStatus) -> PixelTone {
        switch status {
        case .blocked, .failed: return .danger
        case .done: return .ok
        case .none: return .muted
        default: return .accent
        }
    }

    /// Every pane of every server, in tree order.
    static func paneRows(agentId: String, snapshot: HerdrSnapshot?, seen: [String: Int64]) -> [HerdrPaneRow] {
        var rows: [HerdrPaneRow] = []
        for server in snapshot?.servers ?? [] {
            for workspace in server.workspaces {
                for tab in workspace.tabs {
                    for pane in tab.panes {
                        let key = HerdrSeenStore.key(agentId: agentId, serverId: server.server_id, paneId: pane.pane_id)
                        rows.append(HerdrPaneRow(
                            server: server,
                            workspace: workspace,
                            tab: tab,
                            pane: pane,
                            status: status(pane, seenTurn: seen[key])
                        ))
                    }
                }
            }
        }
        return rows
    }

    /// The panes that hold an agent, the ones that need the user first. The
    /// sort is stable by hand: `sorted` gives no such promise.
    static func agentRows(_ rows: [HerdrPaneRow]) -> [HerdrPaneRow] {
        rows.enumerated()
            .filter { $0.element.status != .none }
            .sorted { ($0.element.status, $0.offset) < ($1.element.status, $1.offset) }
            .map(\.element)
    }

    /// The most urgent status among some panes.
    static func rollup(_ rows: [HerdrPaneRow]) -> HerdrStatus {
        rows.map(\.status).min() ?? .none
    }

    private static func plural(_ count: Int, _ noun: String) -> String {
        "\(count) \(noun)\(count == 1 ? "" : "s")"
    }

    /// The one-line summary on the session list: "3 agents · 1 needs you".
    static func summary(_ rows: [HerdrPaneRow]) -> String {
        let agents = rows.filter { $0.status != .none }
        if agents.isEmpty {
            return rows.isEmpty ? "No terminals" : plural(rows.count, "terminal")
        }
        func count(_ status: HerdrStatus) -> Int { agents.filter { $0.status == status }.count }
        var parts = [plural(agents.count, "agent")]
        let blocked = count(.blocked)
        if blocked > 0 { parts.append("\(blocked) need\(blocked == 1 ? "s" : "") you") }
        let failed = count(.failed)
        if failed > 0 { parts.append("\(failed) failed") }
        let done = count(.done)
        if done > 0 { parts.append("\(done) done") }
        let working = count(.working)
        if working > 0 { parts.append("\(working) working") }
        return parts.joined(separator: " · ")
    }

    /// "Herdr" for the usual server; the product and the session name otherwise.
    static func serverLabel(_ server: HerdrServer, among servers: [HerdrServer]) -> String {
        let product = server.product == HerdrServer.productCore ? "Herdr app" : "Herdr"
        let sameProduct = servers.filter { $0.product == server.product }.count
        return server.name == "default" && sameProduct <= 1 ? product : "\(product) · \(server.name)"
    }

    /// What the lists and the pane header call a pane.
    static func paneTitle(_ pane: HerdrPane) -> String {
        if !pane.title.isBlank { return pane.title }
        if let agent = pane.agent { return agentLabel(agent) }
        return pane.pane_id
    }

    static func findPane(
        in snapshot: HerdrSnapshot?, serverId: String, paneId: String
    ) -> (server: HerdrServer, pane: HerdrPane)? {
        guard let server = snapshot?.servers.first(where: { $0.server_id == serverId }) else { return nil }
        for workspace in server.workspaces {
            for tab in workspace.tabs {
                if let pane = tab.panes.first(where: { $0.pane_id == paneId }) {
                    return (server, pane)
                }
            }
        }
        return nil
    }
}

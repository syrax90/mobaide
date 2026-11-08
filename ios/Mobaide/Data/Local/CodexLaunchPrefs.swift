import Foundation

/// Mirror of Android `CodexLaunchPrefs` — last-used Codex launch options,
/// persisted as a JSON blob in `PreferencesStore`.
struct CodexLaunchPrefs: Codable, Equatable, Sendable {
    let model: String?
    let sandbox: String
    let approval: String
    let fullAuto: Bool
    let reasoningEffort: String
    /// The classic terminal instead of the chat view.
    let terminalUI: Bool

    init(
        model: String? = nil,
        sandbox: String = "workspace-write",
        approval: String = "on-request",
        fullAuto: Bool = false,
        reasoningEffort: String = "high",
        terminalUI: Bool = false
    ) {
        self.model = model
        self.sandbox = sandbox
        self.approval = approval
        self.fullAuto = fullAuto
        self.reasoningEffort = reasoningEffort
        self.terminalUI = terminalUI
    }
}

extension CodexLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        sandbox = try container.decodeIfPresent(String.self, forKey: .sandbox) ?? "workspace-write"
        approval = try container.decodeIfPresent(String.self, forKey: .approval) ?? "on-request"
        fullAuto = try container.decodeIfPresent(Bool.self, forKey: .fullAuto) ?? false
        reasoningEffort = try container.decodeIfPresent(String.self, forKey: .reasoningEffort) ?? "high"
        terminalUI = try container.decodeIfPresent(Bool.self, forKey: .terminalUI) ?? false
    }
}

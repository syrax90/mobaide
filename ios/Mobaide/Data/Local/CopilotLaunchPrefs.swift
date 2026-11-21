import Foundation

/// Mirror of Android `CopilotLaunchPrefs` — last-used Copilot launch
/// options, persisted as a JSON blob in `PreferencesStore`.
struct CopilotLaunchPrefs: Codable, Equatable, Sendable {
    let allowAll: Bool
    let sandbox: Bool
    /// The classic terminal instead of the chat view.
    let terminalUI: Bool

    init(allowAll: Bool = false, sandbox: Bool = false, terminalUI: Bool = false) {
        self.allowAll = allowAll
        self.sandbox = sandbox
        self.terminalUI = terminalUI
    }
}

extension CopilotLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        allowAll = try container.decodeIfPresent(Bool.self, forKey: .allowAll) ?? false
        sandbox = try container.decodeIfPresent(Bool.self, forKey: .sandbox) ?? false
        terminalUI = try container.decodeIfPresent(Bool.self, forKey: .terminalUI) ?? false
    }
}

import Foundation

/// Mirror of Android `OpenCodeLaunchPrefs` — last-used OpenCode launch
/// options, persisted as a JSON blob in `PreferencesStore`.
struct OpenCodeLaunchPrefs: Codable, Equatable, Sendable {
    let autoApprove: Bool
    /// The classic terminal instead of the chat view.
    let terminalUI: Bool

    init(autoApprove: Bool = false, terminalUI: Bool = false) {
        self.autoApprove = autoApprove
        self.terminalUI = terminalUI
    }
}

extension OpenCodeLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        autoApprove = try container.decodeIfPresent(Bool.self, forKey: .autoApprove) ?? false
        terminalUI = try container.decodeIfPresent(Bool.self, forKey: .terminalUI) ?? false
    }
}

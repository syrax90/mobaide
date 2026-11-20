import Foundation

/// Mirror of Android `PiLaunchPrefs` — last-used Pi launch
/// options, persisted as a JSON blob in `PreferencesStore`.
struct PiLaunchPrefs: Codable, Equatable, Sendable {
    /// The classic terminal instead of the chat view.
    let terminalUI: Bool
    /// Pi's codemode tool.
    let codeMode: Bool

    init(terminalUI: Bool = false, codeMode: Bool = false) {
        self.terminalUI = terminalUI
        self.codeMode = codeMode
    }
}

extension PiLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        terminalUI = try container.decodeIfPresent(Bool.self, forKey: .terminalUI) ?? false
        codeMode = try container.decodeIfPresent(Bool.self, forKey: .codeMode) ?? false
    }
}

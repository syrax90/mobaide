import Foundation

/// Mirror of Android `CursorLaunchPrefs` — last-used Cursor launch
/// options, persisted as a JSON blob in `PreferencesStore`.
struct CursorLaunchPrefs: Codable, Equatable, Sendable {
    let force: Bool
    /// The classic terminal instead of the chat view.
    let terminalUI: Bool

    init(force: Bool = false, terminalUI: Bool = false) {
        self.force = force
        self.terminalUI = terminalUI
    }
}

extension CursorLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        force = try container.decodeIfPresent(Bool.self, forKey: .force) ?? false
        terminalUI = try container.decodeIfPresent(Bool.self, forKey: .terminalUI) ?? false
    }
}

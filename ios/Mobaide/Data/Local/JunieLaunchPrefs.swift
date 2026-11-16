import Foundation

/// Mirror of Android `JunieLaunchPrefs` — last-used Junie launch
/// options, persisted as a JSON blob in `PreferencesStore`.
struct JunieLaunchPrefs: Codable, Equatable, Sendable {
    let planMode: Bool
    let brave: Bool
    /// The classic terminal instead of the chat view.
    let terminalUI: Bool

    init(planMode: Bool = false, brave: Bool = false, terminalUI: Bool = false) {
        self.planMode = planMode
        self.brave = brave
        self.terminalUI = terminalUI
    }
}

extension JunieLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        planMode = try container.decodeIfPresent(Bool.self, forKey: .planMode) ?? false
        brave = try container.decodeIfPresent(Bool.self, forKey: .brave) ?? false
        terminalUI = try container.decodeIfPresent(Bool.self, forKey: .terminalUI) ?? false
    }
}

import Foundation

/// Mirror of Android `ClaudeLaunchPrefs` — last-used Claude launch options,
/// persisted as a JSON blob in `PreferencesStore`.
struct ClaudeLaunchPrefs: Codable, Equatable, Sendable {
    let model: String?
    let effort: String
    let fastMode: Bool
    let ultracode: Bool
    let appendSystemPrompt: String

    init(
        model: String? = nil,
        effort: String = "high",
        fastMode: Bool = false,
        ultracode: Bool = false,
        appendSystemPrompt: String = ""
    ) {
        self.model = model
        self.effort = effort
        self.fastMode = fastMode
        self.ultracode = ultracode
        self.appendSystemPrompt = appendSystemPrompt
    }
}

extension ClaudeLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        effort = try container.decodeIfPresent(String.self, forKey: .effort) ?? "high"
        fastMode = try container.decodeIfPresent(Bool.self, forKey: .fastMode) ?? false
        ultracode = try container.decodeIfPresent(Bool.self, forKey: .ultracode) ?? false
        appendSystemPrompt = try container.decodeIfPresent(String.self, forKey: .appendSystemPrompt) ?? ""
    }
}

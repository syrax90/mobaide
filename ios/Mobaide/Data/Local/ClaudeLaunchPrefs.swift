import Foundation

/// Mirror of Android `ClaudeLaunchPrefs` — last-used Claude launch options,
/// persisted as a JSON blob in `PreferencesStore`. Android's blob also carries
/// a `planMode` field for its Auto Scan bucket; iOS adds it together with the
/// Auto Scan launch UI — until then the stores are device-local and
/// both decoders are lenient, so the shape difference is harmless.
struct ClaudeLaunchPrefs: Codable, Equatable, Sendable {
    let model: String?
    let effort: String
    let fastMode: Bool
    let ultracode: Bool
    let appendSystemPrompt: String
    let skipPermissions: Bool
    let noMemory: Bool

    init(
        model: String? = nil,
        effort: String = "high",
        fastMode: Bool = false,
        ultracode: Bool = false,
        appendSystemPrompt: String = "",
        skipPermissions: Bool = false,
        noMemory: Bool = false
    ) {
        self.model = model
        self.effort = effort
        self.fastMode = fastMode
        self.ultracode = ultracode
        self.appendSystemPrompt = appendSystemPrompt
        self.skipPermissions = skipPermissions
        self.noMemory = noMemory
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
        skipPermissions = try container.decodeIfPresent(Bool.self, forKey: .skipPermissions) ?? false
        noMemory = try container.decodeIfPresent(Bool.self, forKey: .noMemory) ?? false
    }
}

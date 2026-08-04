import Foundation

/// Mirror of Android `ClaudeLaunchPrefs` — last-used Claude launch options,
/// persisted as a JSON blob in `PreferencesStore`. Backs two independent
/// per-kind buckets — Claude Code and Auto Scan — so each mode remembers its
/// own choices; `planMode` is meaningful only in the Auto Scan bucket.
struct ClaudeLaunchPrefs: Codable, Equatable, Sendable {
    let model: String?
    let effort: String
    let fastMode: Bool
    let ultracode: Bool
    let appendSystemPrompt: String
    let skipPermissions: Bool
    let noMemory: Bool
    let planMode: Bool

    init(
        model: String? = nil,
        effort: String = "high",
        fastMode: Bool = false,
        ultracode: Bool = false,
        appendSystemPrompt: String = "",
        skipPermissions: Bool = false,
        noMemory: Bool = false,
        planMode: Bool = false
    ) {
        self.model = model
        self.effort = effort
        self.fastMode = fastMode
        self.ultracode = ultracode
        self.appendSystemPrompt = appendSystemPrompt
        self.skipPermissions = skipPermissions
        self.noMemory = noMemory
        self.planMode = planMode
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
        planMode = try container.decodeIfPresent(Bool.self, forKey: .planMode) ?? false
    }
}

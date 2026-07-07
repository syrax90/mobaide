import Foundation

/// Mirror of Android `AntigravityLaunchPrefs` — last-used Antigravity launch
/// options, persisted as a JSON blob in `PreferencesStore`.
struct AntigravityLaunchPrefs: Codable, Equatable, Sendable {
    let model: String?
    let skipPermissions: Bool
    let sandbox: Bool

    init(model: String? = nil, skipPermissions: Bool = false, sandbox: Bool = false) {
        self.model = model
        self.skipPermissions = skipPermissions
        self.sandbox = sandbox
    }
}

extension AntigravityLaunchPrefs {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        skipPermissions = try container.decodeIfPresent(Bool.self, forKey: .skipPermissions) ?? false
        sandbox = try container.decodeIfPresent(Bool.self, forKey: .sandbox) ?? false
    }
}

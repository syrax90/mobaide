import Foundation
import Observation

/// Which finished turns of Herdr agents the user has already looked at, on
/// this device. A `done` pane whose `turn` is stored here shows as idle.
///
/// Kept on the device and not in herdr: the only herdr call that marks an
/// agent seen also moves the view of a user at the desktop.
@MainActor
@Observable
final class HerdrSeenStore {
    private static let defaultsKey = "herdr_seen_turns"
    /// A pane id is never reused on purpose, so old entries only grow.
    private static let maxEntries = 500

    private(set) var seen: [String: Int64]
    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let stored = defaults.dictionary(forKey: Self.defaultsKey) ?? [:]
        seen = stored.compactMapValues { ($0 as? NSNumber)?.int64Value }
    }

    /// The key of one pane of one server of one agent.
    nonisolated static func key(agentId: String, serverId: String, paneId: String) -> String {
        "\(agentId)\u{0}\(serverId)\u{0}\(paneId)"
    }

    func markSeen(key: String, turn: Int64) {
        guard seen[key] != turn else { return }
        seen[key] = turn
        if seen.count > Self.maxEntries {
            // No order is kept, so any entries go: a dropped one only shows
            // one finished agent as "Done" again.
            for stale in seen.keys.prefix(seen.count - Self.maxEntries) where stale != key {
                seen.removeValue(forKey: stale)
            }
        }
        defaults.set(seen.mapValues { NSNumber(value: $0) }, forKey: Self.defaultsKey)
    }
}

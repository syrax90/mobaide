import Foundation

/// What one session's attention looked like the last time this device
/// surfaced it, plus whether this agent's baseline has been taken.
struct AttentionSeenRecord: Codable, Equatable, Sendable {
    var baselined: Bool = false
    var seen: [String: String] = [:]
}

/// Per-agent memory of which attention instances this device has already
/// surfaced, so the foreground reconcile is edge-triggered.
///
/// The agent's `attention` field is latched level state: it is set on the
/// triggering hook and cleared only by a client PTY write or a
/// `UserPromptSubmit`, so a session nobody answers keeps its reason forever and
/// every full-state snapshot carries it. Notification Center is not a usable
/// memory of what was shown — tapping the alert (iOS dismisses on tap) or
/// swiping it away empties it — so without this store the reconcile re-posts
/// the same stale alert on every app open.
///
/// Scoped per agent because notifications are agent-scoped, and keyed by the
/// attention reason: a re-latch of the *same* reason that this device never saw
/// clear is indistinguishable from the instance it already surfaced, which is
/// the deliberate trade-off of carrying no attention identity on the wire.
@MainActor
final class AttentionSeenStore {
    private let preferences: PreferencesStore
    private let agentKey: () -> String?

    init(preferences: PreferencesStore, agentKey: @escaping () -> String?) {
        self.preferences = preferences
        self.agentKey = agentKey
    }

    /// Identity of one attention instance. `exit_reason` participates because
    /// `set_attention` emits again when only that changes — a `/clear`
    /// `SessionEnd` leaves the PTY running, so the session can legitimately end
    /// a second time and that ending must still reach the user.
    static func key(for session: SessionResponse) -> String? {
        guard let attention = session.attention else { return nil }
        return "\(attention)|\(session.exit_reason ?? "")"
    }

    /// True once this device has surfaced this exact attention instance.
    func hasSurfaced(_ session: SessionResponse) -> Bool {
        guard let key = Self.key(for: session) else { return false }
        return load()?.seen[session.session_id] == key
    }

    func record(_ session: SessionResponse) {
        guard let key = Self.key(for: session) else { return }
        mutate { $0.seen[session.session_id] = key }
    }

    /// Forget one session, re-arming it: the next attention it raises is
    /// unseen again. Driven by an observed clear.
    func forget(sessionId: String) {
        mutate { $0.seen.removeValue(forKey: sessionId) }
    }

    /// Drop every entry outside `sessionIds`, re-arming sessions whose
    /// attention cleared (or that no longer exist) while this device was away —
    /// the snapshot-side counterpart of `forget`.
    func retain(sessionIds: Set<String>) {
        mutate { $0.seen = $0.seen.filter { sessionIds.contains($0.key) } }
    }

    var isBaselined: Bool { load()?.baselined ?? false }

    /// Adopt everything currently latched as already surfaced, in one write,
    /// and settle this agent's baseline for good.
    func adoptBaseline(_ sessions: [SessionResponse]) {
        mutate { record in
            for session in sessions {
                guard let key = Self.key(for: session) else { continue }
                record.seen[session.session_id] = key
            }
            record.baselined = true
        }
    }

    /// Drop every agent's bucket. The agent id may already be gone from the
    /// keychain by the time sign-out reaches here, so the current-agent key
    /// cannot be trusted to find what needs clearing.
    func clearAll() {
        preferences.removeAllAttentionSeen()
    }

    private func load() -> AttentionSeenRecord? {
        guard let agent = agentKey() else { return nil }
        return preferences.getAttentionSeen(agentKey: agent)
    }

    /// Read-modify-write in one main-actor turn so the event loop and a
    /// reconcile suspended across an `await` cannot clobber each other's
    /// records.
    private func mutate(_ body: (inout AttentionSeenRecord) -> Void) {
        guard let agent = agentKey() else { return }
        var record = preferences.getAttentionSeen(agentKey: agent)
        body(&record)
        preferences.setAttentionSeen(agentKey: agent, record: record)
    }
}

import Foundation

/// Logs the Firebase `agent_paired` conversion once per account.
///
/// Two triggers feed it. The live `account.first_agent_paired` event is
/// post-install by construction, so it logs whenever no outcome is stored.
/// The reconcile on every user-events connect reads `first_paired_at` from
/// `GET /api/auth/me`, so a pairing done on another client while this app was
/// closed still counts — but only when it happened after this install armed
/// (`conversion_armed_at`), which keeps every account that paired before the
/// Firebase build, backfilled ones included, silent for good.
///
/// With the sink disabled nothing is read, fetched, armed or persisted, so a
/// later configured build evaluates fresh. Outcomes are keyed per user id and
/// deliberately survive sign-out: signing back in must not log again.
actor AgentPairedConversion: FirstAgentPairedApplying {
    enum Outcome: String {
        case logged
        case skippedPredatesInstall = "skipped_predates_install"
    }

    static let eventName = "agent_paired"
    /// Only the reconcile path compares a server stamp with the device clock;
    /// the allowance covers clock skew, not pairings that predate the install.
    static let firstPairingTolerance: TimeInterval = 3600

    private let sink: any AnalyticsSink
    private let fetchMe: @Sendable (String) async throws -> MeResponse
    private let userIdentityStore: UserIdentityStore
    private let state: ConversionStateStore
    private let now: @Sendable () -> Date

    init(
        sink: any AnalyticsSink,
        fetchMe: @escaping @Sendable (String) async throws -> MeResponse,
        userIdentityStore: UserIdentityStore,
        state: ConversionStateStore,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.sink = sink
        self.fetchMe = fetchMe
        self.userIdentityStore = userIdentityStore
        self.state = state
        self.now = now
    }

    func armIfNeeded() {
        guard sink.isEnabled else { return }
        _ = state.armIfUnset(now: now())
    }

    func firstAgentPaired(agentId: String) async {
        guard sink.isEnabled, let userId = userIdentityStore.getUserId() else { return }
        record(userId: userId, source: "live")
    }

    func reconcileAfterConnect() async {
        guard sink.isEnabled,
              let userId = userIdentityStore.getUserId(),
              let token = userIdentityStore.getUserAccessToken(),
              state.getOutcome(userId: userId) == nil
        else { return }
        let armedAt = state.armIfUnset(now: now())
        guard let me = try? await fetchMe(token),
              me.user_id == userId,
              let stamp = me.first_paired_at,
              let pairedAt = parse(stamp)
        else { return }
        // The live event may have landed during the fetch.
        guard state.getOutcome(userId: userId) == nil else { return }
        if pairedAt >= armedAt.addingTimeInterval(-Self.firstPairingTolerance) {
            record(userId: userId, source: "reconcile")
        } else {
            state.setOutcome(
                userId: userId, outcome: Outcome.skippedPredatesInstall.rawValue
            )
        }
    }

    /// One actor turn: no suspension between the outcome check and the write.
    /// Log first — `logEvent` is a local enqueue that never throws, and a
    /// duplicate is cheaper than a lost conversion.
    private func record(userId: String, source: String) {
        guard state.getOutcome(userId: userId) == nil else { return }
        sink.logEvent(Self.eventName, parameters: ["source": source])
        state.setOutcome(userId: userId, outcome: Outcome.logged.rawValue)
    }

    /// The server omits the fractional part when the microsecond is zero.
    private func parse(_ iso: String) -> Date? {
        fractionalFormatter.date(from: iso) ?? plainFormatter.date(from: iso)
    }

    private let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private let plainFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}

import Foundation
import UIKit

/// An outing runs from the first terminal visit to the next time the session
/// list settles on screen. It is good when the terminals were in healthy use
/// for `ReviewPromptPolicy.minHealthySeconds` and every visit ended cleanly;
/// it lives in memory only.
@MainActor
@Observable
final class ReviewPromptCoordinator {
    private struct Outing {
        var started = false
        var healthySeconds: TimeInterval = 0
        var clean = true
    }

    /// Bumped once per review request; `RootView` turns each bump into
    /// StoreKit's `requestReview()`.
    private(set) var requestCount = 0

    private let preferencesStore: PreferencesStore
    private let foregroundSessionTracker: ForegroundSessionTracker
    private let sleeper: any Sleeper
    private let isAppActive: @MainActor () -> Bool
    private let now: () -> Date
    @ObservationIgnored private var launchRecorded = false
    @ObservationIgnored private var outing = Outing()

    init(
        preferencesStore: PreferencesStore,
        foregroundSessionTracker: ForegroundSessionTracker,
        sleeper: any Sleeper = ContinuousSleeper(),
        isAppActive: @escaping @MainActor () -> Bool = { UIApplication.shared.applicationState == .active },
        now: @escaping () -> Date = { Date() }
    ) {
        self.preferencesStore = preferencesStore
        self.foregroundSessionTracker = foregroundSessionTracker
        self.sleeper = sleeper
        self.isAppActive = isAppActive
        self.now = now
    }

    /// Counts this process as one launch; later calls in the same process are
    /// no-ops. Android counts fresh activities instead, because its
    /// notification service keeps the process alive after a swipe-away.
    func recordLaunch() {
        guard !launchRecorded else { return }
        launchRecorded = true
        preferencesStore.incrementReviewLaunchCount()
    }

    func terminalVisitStarted() {
        outing.started = true
    }

    func recordTerminalVisit(healthySeconds: TimeInterval, endedCleanly: Bool) {
        outing.started = true
        outing.healthySeconds += healthySeconds
        outing.clean = outing.clean && endedCleanly
    }

    /// An error shown to the user spoils the open outing. With none open there
    /// is nothing to spoil.
    func noteError() {
        if outing.started {
            outing.clean = false
        }
    }

    /// Run while the session list is on screen; cancelling it (the list left
    /// during the settle wait) leaves the outing open.
    func sessionListShown() async {
        do {
            try await sleeper.sleep(for: ReviewPromptPolicy.settleDelay)
        } catch {
            return
        }
        // Back inside a session (auto-resume, a quick tap, a git child
        // screen): the outing goes on.
        guard foregroundSessionTracker.visibleSessionId == nil,
              !foregroundSessionTracker.hasWatchHolds
        else { return }
        let finished = outing
        outing = Outing()
        guard ReviewPromptPolicy.isGoodOuting(
            healthySeconds: finished.healthySeconds,
            allVisitsClean: finished.clean
        ) else { return }
        let state = preferencesStore.incrementReviewGoodSessions()
        let date = now()
        // An inactive scene cannot show the sheet: keep the cooldown for a
        // request the user can see.
        guard ReviewPromptPolicy.isEligible(state, now: date), isAppActive() else { return }
        preferencesStore.markReviewRequested(at: date)
        requestCount += 1
    }
}

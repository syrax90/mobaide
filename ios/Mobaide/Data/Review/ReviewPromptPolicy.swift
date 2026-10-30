import Foundation

/// The persisted counters behind the review prompt (`PreferencesStore` keys
/// `review_prompt_*`).
struct ReviewPromptState: Equatable, Sendable {
    var launchCount: Int
    var goodSessions: Int
    var lastRequestedAt: Date?
}

/// When the app asks the store for a review. Android `ReviewPromptPolicy`
/// holds the same numbers; docs/in-app-review.md is the shared contract.
enum ReviewPromptPolicy {
    /// Healthy terminal time an outing needs to count as a good session.
    static let minHealthySeconds: TimeInterval = 60
    static let requiredGoodSessions = 3
    /// The install's first launch never asks.
    static let minLaunches = 2
    /// Under Apple's 3-per-365-days cap and Play's unpublished quota.
    static let cooldown: TimeInterval = 120 * 24 * 60 * 60
    /// Lets the pop back to the session list finish before deciding.
    static let settleDelay: Duration = .seconds(1)

    static func isGoodOuting(healthySeconds: TimeInterval, allVisitsClean: Bool) -> Bool {
        allVisitsClean && healthySeconds >= minHealthySeconds
    }

    static func isEligible(_ state: ReviewPromptState, now: Date) -> Bool {
        guard state.launchCount >= minLaunches,
              state.goodSessions >= requiredGoodSessions
        else { return false }
        guard let last = state.lastRequestedAt else { return true }
        // A stamp in the future means the clock moved back, so it proves nothing.
        return last > now || now.timeIntervalSince(last) >= cooldown
    }
}

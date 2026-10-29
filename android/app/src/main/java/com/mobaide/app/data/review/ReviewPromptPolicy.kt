package com.mobaide.app.data.review

/** The persisted counters behind the review prompt (`PreferencesStore` keys `review_prompt_*`). */
data class ReviewPromptState(
    val launchCount: Int,
    val goodSessions: Int,
    val lastRequestedAtMs: Long?,
)

/**
 * When the app asks the store for a review. iOS `ReviewPromptPolicy` holds the
 * same numbers; docs/in-app-review.md is the shared contract.
 */
object ReviewPromptPolicy {
    /** Healthy terminal time an outing needs to count as a good session. */
    const val MIN_HEALTHY_MS: Long = 60_000

    const val REQUIRED_GOOD_SESSIONS: Int = 3

    /** The install's first launch never asks. */
    const val MIN_LAUNCHES: Int = 2

    /** Under Apple's 3-per-365-days cap and Play's unpublished quota. */
    const val COOLDOWN_MS: Long = 120L * 24 * 60 * 60 * 1000

    /** Lets the pop back to the session list finish before deciding. */
    const val SETTLE_MS: Long = 1_000

    fun isGoodOuting(healthyMs: Long, allVisitsClean: Boolean): Boolean =
        allVisitsClean && healthyMs >= MIN_HEALTHY_MS

    fun isEligible(state: ReviewPromptState, nowMs: Long): Boolean {
        if (state.launchCount < MIN_LAUNCHES) return false
        if (state.goodSessions < REQUIRED_GOOD_SESSIONS) return false
        val last = state.lastRequestedAtMs ?: return true
        // A stamp in the future means the clock moved back, so it proves nothing.
        return last > nowMs || nowMs - last >= COOLDOWN_MS
    }
}

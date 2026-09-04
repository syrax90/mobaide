package com.mobaide.app.data.analytics

import android.util.Log
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.CentralApi
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.OffsetDateTime

/**
 * Logs the Firebase `agent_paired` conversion exactly once per account on
 * this install.
 *
 * Two triggers: the live `account.first_agent_paired` user event, which is
 * post-install by construction, and a reconcile on every user-events
 * connect that reads `first_paired_at` from `GET /api/auth/me` for a
 * pairing that happened while the app was closed. The reconcile only counts
 * a pairing stamped after this install armed — at the first launch of a
 * build carrying the sink ([armIfNeeded] from `MainActivity`), or at the
 * first reconcile if that somehow came first — so backfilled and
 * pre-existing accounts never log; the skip is persisted because the stamp
 * never changes. Arming at launch rather than at sign-in matters: an account
 * that pairs elsewhere before this install's first connect must still count.
 *
 * Both paths stop before any I/O when the sink is disabled, so a build
 * without Firebase config neither calls the server nor persists an outcome.
 */
class AgentPairedConversion(
    private val sink: AnalyticsSink,
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
    private val preferencesStore: PreferencesStore,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val mutex = Mutex()

    suspend fun armIfNeeded() {
        if (!sink.isEnabled) return
        preferencesStore.armConversionIfUnset(clock())
    }

    suspend fun onFirstAgentPairedEvent() {
        if (!sink.isEnabled) return
        val userId = userIdentityStore.getUserId() ?: return
        mutex.withLock {
            if (preferencesStore.getAgentPairedOutcome(userId) != null) return
            sink.logEvent(EVENT_NAME, mapOf(PARAM_SOURCE to SOURCE_LIVE))
            preferencesStore.setAgentPairedOutcome(userId, OUTCOME_LOGGED)
        }
    }

    suspend fun reconcile() {
        if (!sink.isEnabled) return
        val userId = userIdentityStore.getUserId() ?: return
        if (preferencesStore.getAgentPairedOutcome(userId) != null) return
        val armedAt = preferencesStore.armConversionIfUnset(clock())
        val token = userIdentityStore.getUserAccessToken() ?: return
        val me = try {
            centralApi.getMe(token)
        } catch (t: Throwable) {
            Log.w(TAG, "GET /api/auth/me failed; retrying on the next connect", t)
            return
        }
        if (me.user_id != userId) return
        val pairedAt = me.first_paired_at?.let(::parseIsoMillis) ?: return
        mutex.withLock {
            if (preferencesStore.getAgentPairedOutcome(userId) != null) return
            if (pairedAt >= armedAt - FIRST_PAIRING_TOLERANCE_MS) {
                sink.logEvent(EVENT_NAME, mapOf(PARAM_SOURCE to SOURCE_RECONCILE))
                preferencesStore.setAgentPairedOutcome(userId, OUTCOME_LOGGED)
            } else {
                preferencesStore.setAgentPairedOutcome(userId, OUTCOME_SKIPPED_PREDATES_INSTALL)
            }
        }
    }

    private fun parseIsoMillis(value: String): Long? =
        runCatching { OffsetDateTime.parse(value).toInstant().toEpochMilli() }.getOrNull()

    companion object {
        private const val TAG = "AgentPairedConversion"
        const val EVENT_NAME: String = "agent_paired"
        const val PARAM_SOURCE: String = "source"
        const val SOURCE_LIVE: String = "live"
        const val SOURCE_RECONCILE: String = "reconcile"
        const val OUTCOME_LOGGED: String = "logged"
        const val OUTCOME_SKIPPED_PREDATES_INSTALL: String = "skipped_predates_install"

        // Only the reconcile path compares a server stamp with the device
        // clock; the hour absorbs ordinary skew without admitting pairings
        // that genuinely preceded this install.
        const val FIRST_PAIRING_TOLERANCE_MS: Long = 3_600_000L
    }
}

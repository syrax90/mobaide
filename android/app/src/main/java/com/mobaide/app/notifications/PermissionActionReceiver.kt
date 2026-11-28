package com.mobaide.app.notifications

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.ChatRespondRequest
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import java.io.IOException

/**
 * Answers a chat permission from the Allow or Deny action of its alert
 * ([AttentionNotifier]) with one REST call through the current agent
 * connection. The alert goes at once, so a second tap cannot send a second
 * answer; when the answer fails, an alert says why. Neither touches the
 * alert of another request of the session (the next one) when it shows.
 *
 * Allow is refused while the device stays locked, on every Android version.
 * Android 12+ asks for the unlock itself (`setAuthenticationRequired`) but
 * delivers the broadcast while the keyguard is still going away, so the
 * receiver waits for it a moment; that flag holds only for a tap in the
 * shade, not for another app that sends the action (a notification
 * listener). Older versions cannot ask, so a locked Allow is refused at once.
 */
class PermissionActionReceiver : BroadcastReceiver() {

    /** The alert an action belongs to: enough to answer it and to post it again. */
    data class Target(
        val sessionId: String,
        val requestId: String,
        val title: String,
        val body: String,
        // The agent connected when the alert was posted: the only one the
        // answer can go to.
        val agentId: String?,
    )

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface Dependencies {
        fun restApi(): RestApi
        fun notifier(): AttentionNotifier
        fun tokenStore(): TokenStore
    }

    override fun onReceive(context: Context, intent: Intent) {
        val target = targetFrom(intent) ?: return
        val choice = intent.getStringExtra(EXTRA_CHOICE)
            ?.takeIf { it == ChatRespondRequest.ALLOW || it == ChatRespondRequest.DENY }
            ?: return
        val deps = EntryPointAccessors.fromApplication(context.applicationContext, Dependencies::class.java)
        val notifier = deps.notifier()
        val connected = deps.tokenStore().getAgentId()
        if (connected == null) {
            notifier.post(target.sessionId, FAILURE_TITLE, failureBody(401))
            return
        }
        if (target.agentId != connected) {
            // Mobaide switched to another computer since: the answer would
            // reach an agent that does not have this request.
            notifier.post(target.sessionId, FAILURE_TITLE, OTHER_COMPUTER)
            return
        }
        val locked = choice == ChatRespondRequest.ALLOW && isLocked(context)
        if (locked && Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            notifier.post(
                target.sessionId,
                target.title,
                unlockFirst(target.body),
                target.requestId,
                alertOnce = true,
            )
            return
        }
        // A newer request's alert (raised while the unlock went on) stays.
        val shownNow = notifier.shownRequestId(target.sessionId)
        if (shownNow == null || shownNow == target.requestId) notifier.cancel(target.sessionId)
        val pending = goAsync()
        scope.launch {
            try {
                // Android 12+ sends an Allow that asked for the unlock while
                // the keyguard is still going away.
                val started = SystemClock.elapsedRealtime()
                if (locked && !unlockedWithin(context, UNLOCK_WAIT_MS)) {
                    val shown = notifier.shownRequestId(target.sessionId)
                    if (shown == null || shown == target.requestId) {
                        notifier.post(target.sessionId, target.title, unlockFirst(target.body), target.requestId)
                    }
                    return@launch
                }
                val left = TIMEOUT_MS - (SystemClock.elapsedRealtime() - started)
                val failure = answer(deps.restApi(), target, choice, left)
                // The alert of the next request may already show; the one
                // this tap cancelled can still be listed for a moment.
                val shown = notifier.shownRequestId(target.sessionId)
                if (failure != null && (shown == null || shown == target.requestId)) {
                    notifier.post(target.sessionId, FAILURE_TITLE, failure)
                }
            } finally {
                pending.finish()
            }
        }
    }

    private fun isLocked(context: Context): Boolean =
        context.getSystemService(KeyguardManager::class.java)?.isDeviceLocked == true

    private suspend fun unlockedWithin(context: Context, timeoutMs: Long): Boolean =
        withTimeoutOrNull(timeoutMs) {
            while (isLocked(context)) delay(UNLOCK_POLL_MS)
            true
        } ?: false

    private fun unlockFirst(body: String): String = "$UNLOCK_FIRST\n${body.removePrefix("$UNLOCK_FIRST\n")}"

    companion object {
        private const val ACTION = "com.mobaide.app.action.ANSWER_PERMISSION"
        private const val EXTRA_SESSION_ID = "session_id"
        private const val EXTRA_REQUEST_ID = "request_id"
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_BODY = "body"
        private const val EXTRA_CHOICE = "choice"
        private const val EXTRA_AGENT_ID = "agent_id"

        // The whole hold of the broadcast, the unlock wait included: well
        // inside the time a goAsync() receiver may keep it.
        private const val TIMEOUT_MS = 8_000L
        private const val UNLOCK_WAIT_MS = 2_000L
        private const val UNLOCK_POLL_MS = 100L

        private const val UNLOCK_FIRST = "Unlock your phone first, then tap Allow."
        private const val FAILURE_TITLE = "Could not answer"
        private const val OTHER_COMPUTER =
            "This alert is from another computer. Switch to it in Mobaide to answer."

        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

        fun intent(context: Context, target: Target, choice: String): Intent =
            Intent(context, PermissionActionReceiver::class.java).apply {
                action = ACTION
                putExtra(EXTRA_SESSION_ID, target.sessionId)
                putExtra(EXTRA_REQUEST_ID, target.requestId)
                putExtra(EXTRA_TITLE, target.title)
                putExtra(EXTRA_BODY, target.body)
                putExtra(EXTRA_CHOICE, choice)
                putExtra(EXTRA_AGENT_ID, target.agentId)
            }

        private fun targetFrom(intent: Intent): Target? {
            if (intent.action != ACTION) return null
            return Target(
                sessionId = intent.getStringExtra(EXTRA_SESSION_ID) ?: return null,
                requestId = intent.getStringExtra(EXTRA_REQUEST_ID) ?: return null,
                title = intent.getStringExtra(EXTRA_TITLE).orEmpty(),
                body = intent.getStringExtra(EXTRA_BODY).orEmpty(),
                agentId = intent.getStringExtra(EXTRA_AGENT_ID),
            )
        }

        /** The answer sent, or the body of the "Could not answer" alert. */
        internal suspend fun answer(
            restApi: RestApi,
            target: Target,
            choice: String,
            timeoutMs: Long = TIMEOUT_MS,
        ): String? =
            try {
                withTimeout(timeoutMs) {
                    restApi.respondToChatRequest(target.sessionId, ChatRespondRequest(target.requestId, choice))
                }
                null
            } catch (e: ApiException) {
                failureBody(e.code)
            } catch (_: TimeoutCancellationException) {
                NO_CONNECTION
            } catch (_: IOException) {
                NO_CONNECTION
            } catch (e: CancellationException) {
                throw e
            } catch (_: IllegalStateException) {
                // No agent connection is configured any more.
                failureBody(401)
            } catch (_: Exception) {
                failureBody(-1)
            }

        private const val NO_CONNECTION = "No connection to your computer. Tap to open."

        /** Same words as iOS's `PermissionActionHandler.failureBody`. */
        internal fun failureBody(code: Int): String = when (code) {
            409 -> "The request was already answered. Tap to open."
            404 -> "The chat session has ended. Tap to open."
            401, 403 -> "Sign in to Mobaide again. Tap to open."
            503 -> "Your computer is offline. Tap to open."
            else -> "Something went wrong. Tap to open."
        }
    }
}

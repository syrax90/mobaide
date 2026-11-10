package com.mobaide.app.ui.chat

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.os.Build
import android.widget.Toast
import androidx.compose.ui.hapticfeedback.HapticFeedback
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.core.net.toUri
import com.mobaide.app.data.chat.ChatCost
import com.mobaide.app.data.chat.ChatState
import com.mobaide.app.data.chat.ChatStatus
import com.mobaide.app.data.chat.ChatUsage
import com.mobaide.app.ui.chat.markdown.Markdown
import com.mobaide.app.ui.common.copyToClipboard
import com.mobaide.app.ui.util.openUrlInCustomTab
import java.text.NumberFormat
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.Locale
import kotlin.math.roundToInt

enum class ChatStatusTone { Muted, Accent, Danger }

data class ChatStatusLine(val text: String, val tone: ChatStatusTone)

/** The top bar's second line: the first matching condition wins. */
fun chatStatusLine(live: Boolean, state: ChatState, nowMs: Long): ChatStatusLine = when {
    !live -> ChatStatusLine("Reconnecting…", ChatStatusTone.Muted)
    state.requests.isNotEmpty() -> ChatStatusLine("Waiting for you", ChatStatusTone.Accent)
    state.status == ChatStatus.STARTING -> ChatStatusLine("Starting…", ChatStatusTone.Muted)
    state.status == ChatStatus.LOADING -> ChatStatusLine("Opening the session…", ChatStatusTone.Muted)
    state.cancelling -> ChatStatusLine("Stopping…", ChatStatusTone.Muted)
    state.status == ChatStatus.RUNNING -> {
        val elapsed = parseIsoMillis(state.turnStartedAt)?.let { formatElapsed(nowMs - it) }
        ChatStatusLine(if (elapsed != null) "Working · $elapsed" else "Working", ChatStatusTone.Muted)
    }
    state.status == ChatStatus.ERROR -> ChatStatusLine("Stopped", ChatStatusTone.Danger)
    state.status == ChatStatus.STOPPED -> ChatStatusLine("Session closed", ChatStatusTone.Muted)
    else -> ChatStatusLine("Ready", ChatStatusTone.Muted)
}

/** "12s", "3m 05s", "1h 02m". */
fun formatElapsed(ms: Long): String {
    val total = (ms.coerceAtLeast(0) / 1000)
    val hours = total / 3600
    val minutes = (total % 3600) / 60
    val seconds = total % 60
    return when {
        hours > 0 -> "${hours}h ${"%02d".format(Locale.ROOT, minutes)}m"
        minutes > 0 -> "${minutes}m ${"%02d".format(Locale.ROOT, seconds)}s"
        else -> "${seconds}s"
    }
}

fun parseIsoMillis(iso: String?): Long? {
    if (iso.isNullOrBlank()) return null
    return runCatching { OffsetDateTime.parse(iso).toInstant().toEpochMilli() }.getOrNull()
        ?: runCatching { Instant.parse(iso).toEpochMilli() }.getOrNull()
        ?: runCatching { LocalDateTime.parse(iso).toInstant(ZoneOffset.UTC).toEpochMilli() }.getOrNull()
}

fun usageFraction(usage: ChatUsage): Float =
    if (usage.size <= 0) 0f else (usage.used.toFloat() / usage.size).coerceIn(0f, 1f)

fun usagePercent(usage: ChatUsage): Int = (usageFraction(usage) * 100).roundToInt()

fun formatUsage(usage: ChatUsage): String {
    val numbers = NumberFormat.getIntegerInstance(Locale.US)
    return "${numbers.format(usage.used)} of ${numbers.format(usage.size)} tokens (${usagePercent(usage)} %)"
}

fun formatCost(cost: ChatCost): String = if (cost.currency.equals("USD", ignoreCase = true)) {
    "≈ $" + "%.2f".format(Locale.US, cost.amount)
} else {
    "≈ " + "%.2f".format(Locale.US, cost.amount) + " " + cost.currency
}

/** Opens http, https and mailto links outside the app; anything else is ignored. */
fun openExternalLink(context: Context, url: String) {
    if (!Markdown.isSafeUrl(url)) return
    val trimmed = url.trim()
    if (trimmed.lowercase().startsWith("mailto:")) {
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, trimmed.toUri()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: ActivityNotFoundException) {
        }
    } else {
        openUrlInCustomTab(context, trimmed)
    }
}

fun copyWithFeedback(context: Context, haptic: HapticFeedback, label: String, text: String) {
    context.copyToClipboard(label = label, text = text)
    haptic.performHapticFeedback(HapticFeedbackType.LongPress)
    // Android 13+ shows its own clipboard confirmation.
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
        Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
    }
}

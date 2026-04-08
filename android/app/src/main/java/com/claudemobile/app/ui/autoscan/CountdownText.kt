package com.claudemobile.app.ui.autoscan

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.format.DateTimeParseException

/**
 * Renders "Next scan in 1m 23s" / "Running…" / "Now" derived from an
 * ISO-8601 `next_scan_at` string. Ticks every 1 second on the main thread.
 */
@Composable
fun CountdownText(
    nextScanAtIso: String?,
    modifier: Modifier = Modifier,
    style: androidx.compose.ui.text.TextStyle = androidx.compose.material3.LocalTextStyle.current,
    prefix: String = "Next scan in ",
) {
    val target: Instant? = remember(nextScanAtIso) {
        if (nextScanAtIso.isNullOrBlank()) null
        else try {
            Instant.parse(nextScanAtIso)
        } catch (_: DateTimeParseException) {
            null
        }
    }
    var label by remember(target) { mutableStateOf(formatCountdown(target, prefix)) }
    LaunchedEffect(target) {
        while (true) {
            label = formatCountdown(target, prefix)
            delay(1000)
        }
    }
    Text(text = label, modifier = modifier, style = style)
}

private fun formatCountdown(target: Instant?, prefix: String): String {
    if (target == null) return "—"
    val secondsLeft = target.epochSecond - Instant.now().epochSecond
    if (secondsLeft <= 0) return "${prefix}now"
    val mins = secondsLeft / 60
    val secs = secondsLeft % 60
    return when {
        mins > 0 -> "${prefix}${mins}m ${secs}s"
        else -> "${prefix}${secs}s"
    }
}

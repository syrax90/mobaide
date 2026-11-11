package com.mobaide.app.ui.sessions

import androidx.compose.ui.graphics.Color
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.theme.PixelColors

/**
 * Human-readable label for a session's kind. Custom types prefer the wire
 * `kind_label` snapshot (creation-time name — stable across rename/delete of
 * the type), then a live registry lookup by wire kind, then a generic
 * fallback.
 */
fun sessionKindLabel(
    session: SessionResponse,
    customTypeNames: Map<String, String>,
): String = when (val kind = session.kind) {
    SessionKind.Claude -> "Claude Code"
    SessionKind.Codex -> "Codex"
    SessionKind.Antigravity -> "Antigravity"
    SessionKind.AutoScan -> "Auto Scan"
    SessionKind.Shell -> "Terminal"
    is SessionKind.Custom -> session.kind_label
        ?: customTypeNames[kind.raw]
        ?: "Custom"
}

/**
 * The terminal header's label: the live OSC title while there is one,
 * otherwise the kind. Deliberately never falls back to the session id — the
 * cards may show a truncated id to disambiguate a list, a single screen's
 * header has nothing to disambiguate against.
 */
fun sessionHeaderLabel(
    session: SessionResponse,
    customTypeNames: Map<String, String>,
): String = session.title?.takeIf { it.isNotBlank() }
    ?: sessionKindLabel(session, customTypeNames)

/**
 * A card's headline: the live title, otherwise
 * `kind · project` — with several sessions of one kind open, the bare kind
 * identifies nothing — falling back to a truncated id for a project-less one.
 */
fun sessionTitle(session: SessionResponse, kindLabel: String): String =
    session.title?.takeIf { it.isNotBlank() }
        ?: "$kindLabel · ${session.project_name ?: session.session_id.take(8)}"

/** The status-dot colour for a session's `state`. */
fun PixelColors.sessionStateColor(state: String): Color = when (state) {
    "running" -> statusRunning
    "starting" -> statusStarting
    "stopping" -> statusStopping
    "stopped" -> statusStopped
    "crashed" -> statusCrashed
    else -> statusStopped
}

enum class AttentionTone { Warning, Ok, Muted }

data class SessionAttention(val text: String, val tone: AttentionTone)

/** The card chip for a session's `attention` field. */
fun sessionAttention(value: String?): SessionAttention? = when (value) {
    "awaiting_permission", "awaiting_os_permission" -> SessionAttention("Needs permission", AttentionTone.Warning)
    "turn_finished" -> SessionAttention("Ready", AttentionTone.Ok)
    "turn_failed" -> SessionAttention("Failed", AttentionTone.Warning)
    "ended" -> SessionAttention("Ended", AttentionTone.Muted)
    else -> null
}

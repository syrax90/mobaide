package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse

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

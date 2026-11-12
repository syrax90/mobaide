package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the shared session label helpers. [sessionHeaderLabel] backs the
 * terminal top bar, whose contract is that it names a session by its live OSC title
 * or its kind — never by its id.
 */
class SessionDisplayTest {

    private fun session(
        title: String? = null,
        kind: SessionKind = SessionKind.Claude,
        kindLabel: String? = null,
        projectName: String? = null,
        state: String = "running",
        attention: String? = null,
        activity: String? = null,
    ): SessionResponse = SessionResponse(
        session_id = "0123456789abcdef",
        state = state,
        created_at = "2026-01-01T00:00:00Z",
        title = title,
        project_name = projectName,
        kind = kind,
        kind_label = kindLabel,
        attention = attention,
        activity = activity,
    )

    @Test
    fun kindLabel_prefersCreationSnapshotOverLiveRegistry() {
        val kind = SessionKind.Custom("custom_ab12cd34ef56")
        val names = mapOf("custom_ab12cd34ef56" to "Aider Renamed")

        assertEquals("Aider", sessionKindLabel(session(kind = kind, kindLabel = "Aider"), names))
        assertEquals("Aider Renamed", sessionKindLabel(session(kind = kind), names))
        assertEquals("Custom", sessionKindLabel(session(kind = kind), emptyMap()))
    }

    @Test
    fun headerLabel_fallsBackToKind_whenTitleUnusable() {
        assertEquals(
            "a session with no title yet renders its kind, never its id",
            "Codex",
            sessionHeaderLabel(session(kind = SessionKind.Codex), emptyMap()),
        )
        assertEquals(
            "a whitespace-only OSC title is not a label",
            "Terminal",
            sessionHeaderLabel(session(title = "   ", kind = SessionKind.Shell), emptyMap()),
        )
    }

    @Test
    fun title_fallsBackToKindAndProject_whenTitleUnusable() {
        assertEquals(
            "several sessions of one kind need the project to tell them apart",
            "Codex · mobaide",
            sessionTitle(session(projectName = "mobaide"), "Codex"),
        )
        assertEquals(
            "a whitespace-only OSC title is not a headline",
            "Terminal · mobaide",
            sessionTitle(session(title = "   ", projectName = "mobaide"), "Terminal"),
        )
    }

    @Test
    fun title_fallsBackToTruncatedId_forAProjectLessSession() {
        assertEquals("Claude Code · 01234567", sessionTitle(session(), "Claude Code"))
    }

    @Test
    fun attention_rendersNoChip_forAbsentOrUnknownReasons() {
        assertNull(sessionAttention(null))
        assertNull("a newer agent's reason must be invisible, not garbled", sessionAttention("something_else"))
        assertNull(sessionAttention(""))
    }

    @Test
    fun working_onlyForARunningSessionTheAgentReportsMidTurn() {
        assertTrue(isSessionWorking(session(activity = "working")))
        assertFalse(isSessionWorking(session(activity = "idle")))
        assertFalse(
            "Codex, Antigravity and Terminal sessions report no activity and keep the plain square",
            isSessionWorking(session()),
        )
        listOf("starting", "stopping", "stopped", "crashed").forEach { state ->
            assertFalse(
                "a $state session never spins, whatever its last activity",
                isSessionWorking(session(state = state, activity = "working")),
            )
        }
    }

    @Test
    fun working_yieldsToAPendingPermissionPrompt() {
        assertFalse(isSessionWorking(session(activity = "working", attention = "awaiting_permission")))
        assertFalse(isSessionWorking(session(activity = "working", attention = "awaiting_os_permission")))
    }

    @Test
    fun statusChip_showsWorkingOverAStandingReadyFromAnEarlierTurn() {
        assertEquals(
            SessionAttention("Working", AttentionTone.Working),
            sessionStatusChip(session(activity = "working", attention = "turn_finished")),
        )
    }

    @Test
    fun statusChip_fallsBackToTheAttentionChip_onceTheTurnIsNotWorking() {
        assertEquals(
            SessionAttention("Ready", AttentionTone.Ok),
            sessionStatusChip(session(activity = "idle", attention = "turn_finished")),
        )
        assertEquals(
            SessionAttention("Needs permission", AttentionTone.Warning),
            sessionStatusChip(session(activity = "working", attention = "awaiting_permission")),
        )
        assertNull(sessionStatusChip(session(activity = "idle")))
    }

    @Test
    fun waiting_onlyForARunningSessionParkedOnBackgroundWork() {
        assertTrue(isSessionWaiting(session(activity = "waiting")))
        assertFalse(isSessionWaiting(session(activity = "working")))
        assertFalse(isSessionWaiting(session(activity = "idle")))
        assertFalse(
            "a crashed session is parked on nothing, whatever its last activity",
            isSessionWaiting(session(state = "crashed", activity = "waiting")),
        )
        listOf("awaiting_permission", "awaiting_os_permission").forEach { attention ->
            assertFalse(
                "$attention: the session is blocked on the user",
                isSessionWaiting(session(activity = "waiting", attention = attention)),
            )
        }
    }

    @Test
    fun statusChip_showsBackgroundOverAStandingReady_whileBackgroundWorkIsInFlight() {
        assertEquals(
            "Ready here told the user it was their move on a session about to resume by itself",
            SessionAttention("Background", AttentionTone.Muted),
            sessionStatusChip(session(activity = "waiting", attention = "turn_finished")),
        )
    }

    @Test
    fun statusChip_showsNeedsPermissionOverBackground() {
        assertEquals(
            SessionAttention("Needs permission", AttentionTone.Warning),
            sessionStatusChip(session(activity = "waiting", attention = "awaiting_permission")),
        )
    }
}

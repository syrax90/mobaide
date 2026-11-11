package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.theme.DarkPixelColors
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
    fun kindLabel_rendersBuiltInKinds() {
        assertEquals("Claude Code", sessionKindLabel(session(kind = SessionKind.Claude), emptyMap()))
        assertEquals("Codex", sessionKindLabel(session(kind = SessionKind.Codex), emptyMap()))
        assertEquals("Antigravity", sessionKindLabel(session(kind = SessionKind.Antigravity), emptyMap()))
        assertEquals("Auto Scan", sessionKindLabel(session(kind = SessionKind.AutoScan), emptyMap()))
        assertEquals("Terminal", sessionKindLabel(session(kind = SessionKind.Shell), emptyMap()))
    }

    @Test
    fun kindLabel_prefersCreationSnapshotOverLiveRegistry() {
        val kind = SessionKind.Custom("custom_ab12cd34ef56")
        val names = mapOf("custom_ab12cd34ef56" to "Aider Renamed")

        assertEquals("Aider", sessionKindLabel(session(kind = kind, kindLabel = "Aider"), names))
        assertEquals("Aider Renamed", sessionKindLabel(session(kind = kind), names))
        assertEquals("Custom", sessionKindLabel(session(kind = kind), emptyMap()))
    }

    @Test
    fun headerLabel_prefersTitle() {
        assertEquals(
            "Fix the header",
            sessionHeaderLabel(session(title = "Fix the header", kind = SessionKind.Codex), emptyMap()),
        )
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
    fun headerLabel_reusesCustomKindChain() {
        assertEquals(
            "Aider Renamed",
            sessionHeaderLabel(
                session(kind = SessionKind.Custom("custom_ab12cd34ef56")),
                mapOf("custom_ab12cd34ef56" to "Aider Renamed"),
            ),
        )
    }

    @Test
    fun title_prefersTheLiveTitle() {
        assertEquals(
            "Fix the header",
            sessionTitle(session(title = "Fix the header", projectName = "mobaide"), "Codex"),
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
    fun stateColor_mapsEveryLifecycleStateToItsStatusColour() {
        val colors = DarkPixelColors
        assertEquals(colors.statusRunning, colors.sessionStateColor("running"))
        assertEquals(colors.statusStarting, colors.sessionStateColor("starting"))
        assertEquals(colors.statusStopping, colors.sessionStateColor("stopping"))
        assertEquals(colors.statusStopped, colors.sessionStateColor("stopped"))
        assertEquals(colors.statusCrashed, colors.sessionStateColor("crashed"))
    }

    @Test
    fun stateColor_treatsAnUnknownStateAsStopped() {
        val colors = DarkPixelColors
        assertEquals(
            "a newer agent's state must render muted, never as a crash or a live session",
            colors.statusStopped,
            colors.sessionStateColor("hibernating"),
        )
        assertEquals(colors.statusStopped, colors.sessionStateColor(""))
    }

    @Test
    fun attention_mapsTheWireReasonsToTheCardChip() {
        assertEquals(
            SessionAttention("Needs permission", AttentionTone.Warning),
            sessionAttention("awaiting_permission"),
        )
        assertEquals(
            "the host-side file-access stall reads as the same ask",
            SessionAttention("Needs permission", AttentionTone.Warning),
            sessionAttention("awaiting_os_permission"),
        )
        assertEquals(SessionAttention("Ready", AttentionTone.Ok), sessionAttention("turn_finished"))
        assertEquals(SessionAttention("Failed", AttentionTone.Warning), sessionAttention("turn_failed"))
        assertEquals(SessionAttention("Ended", AttentionTone.Muted), sessionAttention("ended"))
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
}

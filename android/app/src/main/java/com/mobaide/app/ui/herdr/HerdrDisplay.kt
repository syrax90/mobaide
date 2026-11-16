package com.mobaide.app.ui.herdr

import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.network.dto.HerdrPane
import com.mobaide.app.data.network.dto.HerdrServer
import com.mobaide.app.data.network.dto.HerdrSnapshot
import com.mobaide.app.data.network.dto.HerdrTab
import com.mobaide.app.data.network.dto.HerdrWorkspace
import com.mobaide.app.notifications.herdrPaneKey
import com.mobaide.app.ui.sessions.AttentionTone
import com.mobaide.app.ui.sessions.SessionAttention

/** What a pane shows; ordered "needs you first", the order of the Agents list. */
enum class HerdrStatus { Blocked, Failed, Done, Working, Idle, Unknown, None }

data class HerdrPaneRow(
    val server: HerdrServer,
    val workspace: HerdrWorkspace,
    val tab: HerdrTab,
    val pane: HerdrPane,
    val status: HerdrStatus,
) {
    /** Stable across snapshots, for list keys. */
    val key: String get() = herdrPaneKey(server.server_id, pane.pane_id)
}

private val AGENT_LABELS = mapOf(
    "claude" to "Claude Code",
    "codex" to "Codex",
    "antigravity" to "Antigravity",
    "opencode" to "OpenCode",
    "copilot" to "GitHub Copilot",
    "cursor" to "Cursor",
    "pi" to "Pi",
)

/** The display name of an agent; a name Mobaide does not know stays as it is. */
fun herdrAgentLabel(agent: String): String = AGENT_LABELS[agent] ?: agent

/**
 * A pane's status on this device. A `done` whose `turn` the user already saw
 * here is idle: "seen" is kept per device, because marking a pane seen in herdr
 * would move the view of a user at the desktop.
 */
fun herdrStatus(pane: HerdrPane, seenTurn: Long?): HerdrStatus {
    if (pane.agent == null) return HerdrStatus.None
    return when (pane.status) {
        "blocked" -> HerdrStatus.Blocked
        "failed" -> HerdrStatus.Failed
        "working" -> HerdrStatus.Working
        "idle" -> HerdrStatus.Idle
        "done" -> if (pane.turn != null && pane.turn == seenTurn) HerdrStatus.Idle else HerdrStatus.Done
        else -> HerdrStatus.Unknown
    }
}

/** The chip of a status, in the words and tones the session cards use. */
fun herdrChip(status: HerdrStatus): SessionAttention? = when (status) {
    HerdrStatus.Blocked -> SessionAttention("Needs you", AttentionTone.Warning)
    HerdrStatus.Failed -> SessionAttention("Failed", AttentionTone.Warning)
    HerdrStatus.Done -> SessionAttention("Done", AttentionTone.Ok)
    HerdrStatus.Working -> SessionAttention("Working", AttentionTone.Working)
    HerdrStatus.Idle -> SessionAttention("Idle", AttentionTone.Muted)
    HerdrStatus.Unknown, HerdrStatus.None -> null
}

/** Every pane of every server, in tree order. */
fun herdrPaneRows(agentId: String, snapshot: HerdrSnapshot?, seen: Map<String, Long>): List<HerdrPaneRow> =
    snapshot?.servers.orEmpty().flatMap { server ->
        server.workspaces.flatMap { workspace ->
            workspace.tabs.flatMap { tab ->
                tab.panes.map { pane ->
                    HerdrPaneRow(
                        server = server,
                        workspace = workspace,
                        tab = tab,
                        pane = pane,
                        status = herdrStatus(
                            pane,
                            seen[HerdrSeenStore.key(agentId, server.server_id, pane.pane_id)],
                        ),
                    )
                }
            }
        }
    }

/** The panes that hold an agent, the ones that need the user first. */
fun herdrAgentRows(rows: List<HerdrPaneRow>): List<HerdrPaneRow> =
    rows.filter { it.status != HerdrStatus.None }.sortedBy { it.status.ordinal }

/** The most urgent status among some panes. */
fun herdrRollup(rows: List<HerdrPaneRow>): HerdrStatus =
    rows.minOfOrNull { it.status } ?: HerdrStatus.None

private fun plural(count: Int, noun: String) = "$count $noun${if (count == 1) "" else "s"}"

/** The one-line summary on the session list: "3 agents · 1 needs you". */
fun herdrSummary(rows: List<HerdrPaneRow>): String {
    val agents = rows.filter { it.status != HerdrStatus.None }
    if (agents.isEmpty()) {
        return if (rows.isEmpty()) "No terminals" else plural(rows.size, "terminal")
    }
    val parts = mutableListOf(plural(agents.size, "agent"))
    fun count(status: HerdrStatus) = agents.count { it.status == status }
    val blocked = count(HerdrStatus.Blocked)
    if (blocked > 0) parts += "$blocked need${if (blocked == 1) "s" else ""} you"
    count(HerdrStatus.Failed).takeIf { it > 0 }?.let { parts += "$it failed" }
    count(HerdrStatus.Done).takeIf { it > 0 }?.let { parts += "$it done" }
    count(HerdrStatus.Working).takeIf { it > 0 }?.let { parts += "$it working" }
    return parts.joinToString(" · ")
}

/** "Herdr" for the usual server; the product and the session name otherwise. */
fun herdrServerLabel(server: HerdrServer, servers: List<HerdrServer>): String {
    val product = if (server.product == HerdrServer.PRODUCT_CORE) "Herdr app" else "Herdr"
    val sameProduct = servers.count { it.product == server.product }
    return if (server.name == "default" && sameProduct <= 1) product else "$product · ${server.name}"
}

/** What the lists and the pane header call a pane. */
fun herdrPaneTitle(pane: HerdrPane): String =
    pane.title.takeIf { it.isNotBlank() }
        ?: pane.agent?.let(::herdrAgentLabel)
        ?: pane.pane_id

fun HerdrSnapshot?.findPane(serverId: String, paneId: String): Pair<HerdrServer, HerdrPane>? {
    val server = this?.servers?.firstOrNull { it.server_id == serverId } ?: return null
    val pane = server.workspaces.asSequence()
        .flatMap { it.tabs.asSequence() }
        .flatMap { it.panes.asSequence() }
        .firstOrNull { it.pane_id == paneId } ?: return null
    return server to pane
}

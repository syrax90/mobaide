package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * The Herdr dashboard records (`GET /api/herdr`, the `herdr.changed` agent event).
 * Mobaide owns these shapes; both herdr products use them. [status], [agent],
 * [HerdrServer.product] and [HerdrServer.state] stay open strings so a value a
 * newer agent sends still decodes.
 */
@Serializable
data class HerdrRect(
    val x: Int,
    val y: Int,
    val width: Int,
    val height: Int,
)

@Serializable
data class HerdrPane(
    val pane_id: String,
    val title: String = "",
    /** Null for a pane without an agent, which then has no [status] either. */
    val agent: String? = null,
    val status: String? = null,
    /** Changes each time the agent finishes a turn; see `HerdrSeenStore`. */
    val turn: Long? = null,
    val cwd: String? = null,
    val agent_session_id: String? = null,
    val rect: HerdrRect? = null,
)

@Serializable
data class HerdrTab(
    val tab_id: String,
    val label: String = "",
    val width: Int? = null,
    val height: Int? = null,
    val panes: List<HerdrPane> = emptyList(),
)

@Serializable
data class HerdrWorkspace(
    val workspace_id: String,
    val label: String = "",
    val tabs: List<HerdrTab> = emptyList(),
)

@Serializable
data class HerdrAgentKind(
    val id: String,
    val label: String,
)

/** Every flag defaults to off: an action is offered only on an explicit `true`. */
@Serializable
data class HerdrFeatures(
    val new_workspace: Boolean = false,
    val new_tab: Boolean = false,
    val split: Boolean = false,
    val rename: Boolean = false,
    val close_group: Boolean = false,
    val close_pane: Boolean = false,
    val control: Boolean = false,
    val type_in_watch: Boolean = false,
    val agents: List<HerdrAgentKind> = emptyList(),
)

@Serializable
data class HerdrServer(
    val server_id: String,
    /** `tui` (Herdr TUI) or `core` (herdr-core). */
    val product: String = PRODUCT_TUI,
    val name: String = "default",
    val version: String? = null,
    /** `ready` or `outdated`. */
    val state: String = STATE_READY,
    val message: String? = null,
    val features: HerdrFeatures = HerdrFeatures(),
    val workspaces: List<HerdrWorkspace> = emptyList(),
) {
    val isReady: Boolean get() = state == STATE_READY

    companion object {
        const val PRODUCT_TUI = "tui"
        const val PRODUCT_CORE = "core"
        const val STATE_READY = "ready"
    }
}

@Serializable
data class HerdrSnapshot(
    val servers: List<HerdrServer> = emptyList(),
)

/** `herdr.attention`: an agent in a pane needs the user. */
@Serializable
data class HerdrAttention(
    val server_id: String,
    val pane_id: String,
    val reason: String,
    val title: String,
    val agent: String? = null,
    val agent_id: String? = null,
)

@Serializable
data class HerdrPaneTarget(
    val server_id: String,
    val pane_id: String,
)

@Serializable
data class HerdrOpenPaneResponse(
    val session_id: String,
)

@Serializable
data class HerdrControlRequest(
    val server_id: String,
    val pane_id: String,
    val control: Boolean,
    /** Names the client that takes or gives back control. */
    val client_id: String = "",
)

@Serializable
data class HerdrPaneTextResponse(
    val text: String = "",
    val truncated: Boolean = false,
)

@Serializable
data class HerdrCreateRequest(
    val server_id: String,
    val placement: String,
    val workspace_id: String? = null,
    val pane_id: String? = null,
    val cwd: String? = null,
    val label: String? = null,
    val agent: String? = null,
) {
    companion object {
        const val PLACEMENT_WORKSPACE = "workspace"
        const val PLACEMENT_TAB = "tab"
        const val PLACEMENT_SPLIT_RIGHT = "split_right"
        const val PLACEMENT_SPLIT_DOWN = "split_down"
    }
}

@Serializable
data class HerdrCreateResponse(
    val pane_id: String,
    /** Set when the pane was made and its agent did not start: a message for the user. */
    val agent_error: String? = null,
)

@Serializable
data class HerdrStartAgentRequest(
    val server_id: String,
    val pane_id: String,
    val agent: String,
)

@Serializable
data class HerdrRenameRequest(
    val server_id: String,
    val target: String,
    val id: String,
    val label: String,
)

@Serializable
data class HerdrCloseRequest(
    val server_id: String,
    val target: String,
    val id: String,
)

/** What a rename or a close acts on. */
object HerdrTarget {
    const val WORKSPACE = "workspace"
    const val TAB = "tab"
    const val PANE = "pane"
}

package com.mobaide.app.data.network.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class AutoScanInstanceDto(
    @SerialName("project_name")
    val workspace_name: String,
    val status: String,
    val started_at: Double,
    val last_activity_at: Double,
    val processed_count: Int = 0,
    val last_scan_time: Double? = null,
    val scan_interval_seconds: Int = 300,
    val next_scan_at: String? = null,
    val cycle_number: Int = 0,
    val current_seq: Long = 0L,
    // Older agents scan Linear with Claude Code only.
    val source: String = AUTO_SCAN_SOURCE_LINEAR,
    val cli: String = "claude",
    // "chat" opens each issue's session in the chat view.
    val ui: String = AUTO_SCAN_UI_TERMINAL,
)

@Serializable
data class AutoScanListResponse(
    val scans: List<AutoScanInstanceDto> = emptyList(),
    val capacity: Int = 0,
    val max_scans: Int = 5,
    @SerialName("available_projects")
    val available_workspaces: List<String> = emptyList(),
    val auto_scan_prompt: String? = null,
)

/**
 * `POST /api/autoscan/start`. An older client sends only the workspace and
 * [claude_options]. A client of an agent with `capabilities.autoscan` sends
 * every choice: [source], [cli], [ui], [interval_minutes], the chosen source's filter, [initial_prompt]
 * (`""` clears the saved prompt) and the chosen agent's options only:
 * [claude_options] (without the prompt) for Claude Code, `<kind>_options` for
 * the others, none for Pi.
 */
@Serializable
data class StartScanRequest(
    @SerialName("project_name")
    val workspace_name: String,
    val claude_options: ClaudeOptionsModel? = null,
    val codex_options: CodexScanOptionsDto? = null,
    val antigravity_options: AntigravityScanOptionsDto? = null,
    val opencode_options: OpenCodeScanOptionsDto? = null,
    val copilot_options: CopilotScanOptionsDto? = null,
    val cursor_options: CursorScanOptionsDto? = null,
    val junie_options: JunieScanOptionsDto? = null,
    val ui: String? = null,
    val interval_minutes: Int? = null,
    val source: String? = null,
    val cli: String? = null,
    val initial_prompt: String? = null,
    val linear_filter: AutoScanFilterDto? = null,
    val github_filter: GitHubScanFilterDto? = null,
)

const val AUTO_SCAN_UI_TERMINAL = "terminal"
const val AUTO_SCAN_UI_CHAT = "chat"
const val AUTO_SCAN_SOURCE_LINEAR = "linear"
const val AUTO_SCAN_SOURCE_GITHUB = "github"

/**
 * `GET /api/autoscan/github/status`: what a GitHub scan of the workspace still
 * needs. [problem] is the first blocking reason in plain words; null means a
 * scan can start.
 */
@Serializable
data class GitHubStatusDto(
    val installed: Boolean = false,
    val version: String? = null,
    val signed_in: Boolean = false,
    val account: String? = null,
    val host: String? = null,
    val repo: String? = null,
    val repo_url: String? = null,
    val problem: String? = null,
    // Null from an agent that predates it: then the host's name decides.
    val github_host: Boolean? = null,
    val can_install: Boolean = false,
    val install_command: String? = null,
) {
    /** Signing in can help: the remote is on GitHub (github.com or an Enterprise Server). */
    val offersSignIn: Boolean
        get() = !signed_in && (github_host ?: (host?.contains("github") == true))
}

@Serializable
data class GitHubLabelDto(
    val name: String,
    val color: String = "",
)

/** A GitHub issue type of the repository (Bug, Task, Feature…); [color] is GitHub's name, e.g. `RED`. */
@Serializable
data class GitHubIssueTypeDto(
    val name: String,
    val color: String = "",
    val description: String = "",
)

/** `GET /api/autoscan/github/labels`; [issue_types] is empty when the repository has none. */
@Serializable
data class GitHubLabelsResponse(
    val labels: List<GitHubLabelDto> = emptyList(),
    val issue_types: List<GitHubIssueTypeDto> = emptyList(),
)

/** `POST /api/autoscan/github/setup`; [action] is `install` or `sign_in`. */
@Serializable
data class GitHubSetupRequest(
    @SerialName("project_name")
    val workspace_name: String,
    val action: String,
    val rows: Int = 24,
    val cols: Int = 80,
)

@Serializable
data class StopScanRequest(
    @SerialName("project_name")
    val workspace_name: String,
)

@Serializable
data class RunNowRequest(
    @SerialName("project_name")
    val workspace_name: String,
)

@Serializable
data class RunNowResponse(
    val ok: Boolean,
    @SerialName("project_name")
    val workspace_name: String,
    val next_scan_at: String? = null,
)

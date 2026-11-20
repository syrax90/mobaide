package com.mobaide.app.data.network.dto

import kotlinx.serialization.EncodeDefault
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.Serializable

/**
 * Per-workspace Auto Scan Linear filter, mirrored from
 * `mobaide.core.workspaces_config.AutoScanFilter` on the agent.
 *
 *  - `state_names` and `label_names` are the trigger filter (issues must
 *    match all of `state.name in state_names` AND `labels.name in label_names`).
 *  - `active_state` is set on the issue when the scanner claims it.
 *  - `done_state` is the terminal state that triggers worktree cleanup.
 *  - `removal_label` is stripped from the issue on claim.
 *
 * Defaults reproduce the historical hardcoded behavior so older workspaces
 * that don't carry an explicit filter on the wire still scan correctly.
 */
@Serializable
data class AutoScanFilterDto(
    val state_names: List<String> = listOf("Proposals"),
    val label_names: List<String> = listOf("AutoPipeline"),
    val active_state: String = "In Progress",
    val done_state: String = "Done",
    val removal_label: String = "AutoPipeline",
)

/**
 * The Linear filter as an Auto Scan template keeps it: any field may still be
 * empty. Every field is always sent: the agent's draft defaults are empty, so
 * a field the client left out because it equals a Kotlin default would be lost.
 */
@OptIn(ExperimentalSerializationApi::class)
@Serializable
data class AutoScanFilterDraftDto(
    @EncodeDefault val state_names: List<String> = emptyList(),
    @EncodeDefault val label_names: List<String> = emptyList(),
    @EncodeDefault val active_state: String = "",
    @EncodeDefault val done_state: String = "",
    @EncodeDefault val removal_label: String = "",
)

/**
 * Per-workspace Auto Scan GitHub filter. An open issue that carries any of
 * [labels] (1–20 names, no commas) is picked up; [assignee] null takes any
 * assignee (`@me` is the signed-in `gh` account); [active_label] is added when
 * the scan takes the issue. A non-empty [issue_types] also requires the issue
 * to have one of these GitHub issue types. In a template [labels] may be empty.
 */
@Serializable
data class GitHubScanFilterDto(
    val labels: List<String> = emptyList(),
    val assignee: String? = null,
    val active_label: String? = null,
    val issue_types: List<String> = emptyList(),
)

/**
 * The workspace's last Auto Scan choices (`auto_scan` on the workspace). Each
 * field is null until a scan start chose it; a null filter means the wizard
 * starts with empty fields. `<kind>_options` are the options of the last start
 * with that agent; one the client cannot read is null. Absent on older agents.
 */
@Serializable
data class AutoScanSettingsDto(
    val source: String? = null,
    val cli: String? = null,
    val ui: String? = null,
    val interval_minutes: Int? = null,
    val linear_filter: AutoScanFilterDto? = null,
    val github_filter: GitHubScanFilterDto? = null,
    @Serializable(with = LenientClaudeScanOptions::class)
    val claude_options: ClaudeScanOptionsDto? = null,
    @Serializable(with = LenientCodexScanOptions::class)
    val codex_options: CodexScanOptionsDto? = null,
    @Serializable(with = LenientAntigravityScanOptions::class)
    val antigravity_options: AntigravityScanOptionsDto? = null,
    @Serializable(with = LenientOpenCodeScanOptions::class)
    val opencode_options: OpenCodeScanOptionsDto? = null,
    @Serializable(with = LenientCopilotScanOptions::class)
    val copilot_options: CopilotScanOptionsDto? = null,
    @Serializable(with = LenientCursorScanOptions::class)
    val cursor_options: CursorScanOptionsDto? = null,
    @Serializable(with = LenientJunieScanOptions::class)
    val junie_options: JunieScanOptionsDto? = null,
    @Serializable(with = LenientPiScanOptions::class)
    val pi_options: PiScanOptionsDto? = null,
)

/** A Linear label discovered for the workspace's API key. */
@Serializable
data class LinearLabelDto(
    val name: String,
    val color: String = "",
)

/**
 * A Linear workflow state discovered for the workspace's API key.
 *
 * `type` is one of Linear's documented state types (`backlog` / `unstarted` /
 * `started` / `completed` / `canceled` / `triage`). The UI uses it to render
 * a leading icon on the multi-select chips.
 */
@Serializable
data class LinearStateDto(
    val name: String,
    val type: String = "",
)

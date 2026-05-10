package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * Per-project Auto Scan Linear filter, mirrored from
 * `mobaide.core.projects_config.AutoScanFilter` on the agent.
 *
 *  - `state_names` and `label_names` are the trigger filter (issues must
 *    match all of `state.name in state_names` AND `labels.name in label_names`).
 *  - `active_state` is set on the issue when the scanner claims it.
 *  - `done_state` is the terminal state that triggers worktree cleanup.
 *  - `removal_label` is stripped from the issue on claim.
 *
 * Defaults reproduce the historical hardcoded behavior so older projects
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

/** A Linear label discovered for the project's API key. */
@Serializable
data class LinearLabelDto(
    val name: String,
    val color: String = "",
)

/**
 * A Linear workflow state discovered for the project's API key.
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

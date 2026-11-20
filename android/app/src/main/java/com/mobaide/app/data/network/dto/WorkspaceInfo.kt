package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** Represent a single workspace entry returned by the agent's `list_public()` endpoint. */
@Serializable
data class WorkspaceInfo(
    val name: String,
    val path: String,
    val has_linear_key: Boolean = false,
    val is_git: Boolean = false,
    // `default_flags` values are heterogeneous on the wire (bool | int | float | str),
    // so we keep them as raw JsonElement and let the UI layer coerce by FlagType.
    val default_flags: Map<String, JsonElement> = emptyMap(),
    val conda_env: String? = null,
    val order: Int = 0,
    val path_exists: Boolean = true,
    val auto_scan_filter: AutoScanFilterDto = AutoScanFilterDto(),
    // Per-workspace Auto Scan initial prompt, saved on scan start so the create-
    // session wizard can pre-seed it. `null` means "use the agent's default".
    val auto_scan_prompt: String? = null,
    // The last Auto Scan choices; null from an agent that predates them.
    val auto_scan: AutoScanSettingsDto? = null,
)

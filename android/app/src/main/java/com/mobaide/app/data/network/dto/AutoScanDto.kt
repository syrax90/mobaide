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

@Serializable
data class StartScanRequest(
    @SerialName("project_name")
    val workspace_name: String,
    val claude_options: ClaudeOptionsModel? = null,
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

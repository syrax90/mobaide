package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class AutoScanInstanceDto(
    val project_name: String,
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
    val available_projects: List<String> = emptyList(),
    val auto_scan_prompt: String? = null,
)

@Serializable
data class StartScanRequest(
    val project_name: String,
    val claude_options: ClaudeOptionsModel? = null,
)

@Serializable
data class StopScanRequest(
    val project_name: String,
)

@Serializable
data class RunNowRequest(
    val project_name: String,
)

@Serializable
data class RunNowResponse(
    val ok: Boolean,
    val project_name: String,
    val next_scan_at: String? = null,
)

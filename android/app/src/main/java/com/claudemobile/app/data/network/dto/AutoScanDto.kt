package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class ScanStatusResponse(
    val status: String,
    val active_project: String? = null,
    val processed_count: Int = 0,
    val last_scan_time: Double? = null,
    val scan_interval_seconds: Int = 300,
    val available_projects: List<String> = emptyList(),
    val recent_events: List<Map<String, JsonElement>> = emptyList(),
)

@Serializable
data class StartScanRequest(
    val project_name: String,
)

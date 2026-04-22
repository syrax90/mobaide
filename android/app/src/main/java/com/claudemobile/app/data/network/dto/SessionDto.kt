package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class SessionResponse(
    val session_id: String,
    val state: String,
    val created_at: String,
    val pid: Int? = null,
    val command: List<String> = emptyList(),
    val cwd: String? = null,
)

@Serializable
data class CreateSessionRequest(
    val command: List<String>? = null,
    val cwd: String? = null,
    val env: Map<String, String>? = null,
    val rows: Int = 24,
    val cols: Int = 80,
    // When set, command/cwd/env are ignored; the server derives them from the named project.
    val project_name: String? = null,
    val claude_options: ClaudeOptionsModel? = null,
)

@Serializable
data class ScrollbackResponse(
    val session_id: String,
    val lines: List<String>,
    val total: Int,
)

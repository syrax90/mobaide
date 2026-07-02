package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** DTO mirroring the server-side GeminiOptionsModel; serialized as JSON on session creation. */
@Serializable
data class GeminiOptionsModel(
    val model: String? = null,
    val approval_mode: String = "default",
    val include_directories: List<String> = emptyList(),
    val sandbox: Boolean = false,
    val worktree: Boolean = false,
    val branch_name: String? = null,
)

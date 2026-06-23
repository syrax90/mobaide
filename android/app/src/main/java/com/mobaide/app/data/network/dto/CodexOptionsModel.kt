package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** DTO mirroring the server-side CodexOptionsModel; serialized as JSON on session creation. */
@Serializable
data class CodexOptionsModel(
    val model: String? = null,
    val sandbox: String = "workspace-write",
    val approval: String = "never",
    val reasoning_effort: String? = "high",
    val full_auto: Boolean = false,
    val initial_prompt: String? = null,
    val worktree: Boolean = false,
    val branch_name: String? = null,
)

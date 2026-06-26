package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** DTO mirroring the server-side ClaudeOptionsModel; serialized as JSON on session creation. */
@Serializable
data class ClaudeOptionsModel(
    val plan_mode: Boolean = false,
    val no_memory: Boolean = false,
    val skip_permissions: Boolean = true,
    val initial_prompt: String? = null,
    val worktree: Boolean = false,
    val branch_name: String? = null,
    val identifier: String? = null,
    val title: String? = null,
    val model: String? = null,
    val effort: String? = null,
    val fast_mode: Boolean = false,
    val ultracode: Boolean = false,
    val append_system_prompt: String? = null,
)

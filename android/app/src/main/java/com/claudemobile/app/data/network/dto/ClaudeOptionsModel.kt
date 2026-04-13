package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

/** Mirror of the agent's ClaudeOptionsModel — see claude_agent/claude_mobile/agent/sessions/models.py. */
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
)

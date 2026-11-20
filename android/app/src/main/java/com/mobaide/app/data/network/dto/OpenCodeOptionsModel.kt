package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * DTO mirroring the server-side OpenCodeOptionsModel; serialized as JSON on
 * session creation.
 */
@Serializable
data class OpenCodeOptionsModel(
    val auto_approve: Boolean = false,
    // true = a terminal session that runs OpenCode's own sign-in, not an OpenCode session.
    val connect: Boolean = false,
    // OpenCode 2's Code Mode; on by default there, false turns it off for the session.
    val code_mode: Boolean = true,
    val worktree: Boolean = false,
    val branch_name: String? = null,
    // The ref the new worktree starts from; null = the agent's default branch.
    val base_branch: String? = null,
)

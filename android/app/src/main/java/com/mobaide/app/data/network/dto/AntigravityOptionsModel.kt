package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * DTO mirroring the server-side AntigravityOptionsModel; serialized as JSON on
 * session creation. `skip_permissions` and `sandbox` are mutually exclusive
 * (the server rejects the pair with a 422); the UI enforces it before submit.
 */
@Serializable
data class AntigravityOptionsModel(
    val model: String? = null,
    val skip_permissions: Boolean = false,
    val sandbox: Boolean = false,
    val add_dirs: List<String> = emptyList(),
    val worktree: Boolean = false,
    val branch_name: String? = null,
    // The ref the new worktree starts from; null = the agent's default branch.
    val base_branch: String? = null,
)

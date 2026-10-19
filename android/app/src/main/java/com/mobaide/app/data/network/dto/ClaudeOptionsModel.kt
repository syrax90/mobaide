package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** DTO mirroring the server-side ClaudeOptionsModel; serialized as JSON on session creation. */
@Serializable
data class ClaudeOptionsModel(
    val plan_mode: Boolean = false,
    val no_memory: Boolean = false,
    // Deliberately kept true (matching the server's wire-absence default),
    // even though CreateSessionUiState.skipPermissions now starts false: the
    // request builder always passes that state value explicitly, and RestApi's
    // Json has encodeDefaults=false, so a matching default here would make the
    // common "toggle left off" case silently omit the field — indistinguishable
    // from an old pre-update client that never sends it at all.
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
    // Unlike skip_permissions above, this default is safe to leave at the wire
    // default: the server also treats an absent debug as false, so the field
    // being omitted when the toggle is off carries exactly the right meaning.
    val debug: Boolean = false,
    // null (the wire default) means "leave Claude Code's view alone", so both
    // explicit true (viewMode focus) and false (viewMode default) are emitted.
    val focus_mode: Boolean? = null,
)

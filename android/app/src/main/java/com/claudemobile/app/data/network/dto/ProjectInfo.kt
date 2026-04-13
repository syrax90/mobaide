package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

/** Mirror of the agent's `list_public()` entry — see core/projects_config.py. */
@Serializable
data class ProjectInfo(
    val name: String,
    val path: String,
    val has_linear_key: Boolean = false,
    val is_git: Boolean = false,
    val default_flags: Map<String, String> = emptyMap(),
)

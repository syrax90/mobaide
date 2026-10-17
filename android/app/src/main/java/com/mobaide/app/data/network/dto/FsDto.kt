package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * `GET /api/fs/dirs`: subdirectory names of one agent-host directory. `path` is
 * the listed directory after the agent's `~` expansion; `truncated` means the
 * agent stopped early, so the list must not be filtered locally as complete.
 */
@Serializable
data class FsDirsResponse(
    val path: String,
    val dirs: List<String>,
    val truncated: Boolean,
)

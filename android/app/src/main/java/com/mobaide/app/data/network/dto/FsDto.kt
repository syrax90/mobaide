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

/** One file or folder of a session's folder: relative, `/` separators, folders without a trailing one. */
@Serializable
data class SessionFileEntry(
    val path: String,
    val dir: Boolean,
)

/**
 * `GET /api/sessions/{id}/files`: the agent's best matches for an `@` mention,
 * best first. `truncated` means a cap stopped the listing behind them.
 */
@Serializable
data class SessionFilesResponse(
    val entries: List<SessionFileEntry>,
    val truncated: Boolean,
)

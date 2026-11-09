package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** `GET /api/chat/{kind}/sessions`: sessions the agent stored on the PC, newest first. */
@Serializable
data class ChatStoredSessionsResponse(
    val sessions: List<ChatStoredSession> = emptyList(),
    val next_cursor: String? = null,
)

@Serializable
data class ChatStoredSession(
    val session_id: String,
    val cwd: String,
    val title: String? = null,
    val updated_at: String? = null,
    // The open Mobaide chat session that already shows this row.
    val open_session_id: String? = null,
)

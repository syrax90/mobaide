package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * Body for `POST /api/codex/login`. The key is piped to `codex login
 * --with-api-key` on the agent host (stdin, never persisted) — the app holds it
 * only transiently in memory and never writes it to disk.
 */
@Serializable
data class CodexLoginRequest(val api_key: String)

@Serializable
data class CodexLoginResponse(val authenticated: Boolean)

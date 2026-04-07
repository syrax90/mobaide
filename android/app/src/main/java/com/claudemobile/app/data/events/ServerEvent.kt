package com.claudemobile.app.data.events

import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.network.dto.ScanStatusResponse
import com.claudemobile.app.data.network.dto.SessionResponse
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** Wire envelope: {"type": "...", "payload": {...}}. */
@Serializable
data class ServerEventEnvelope(
    val type: String,
    val payload: JsonElement,
)

/** Typed events emitted by [ServerEventBus]. */
sealed class ServerEvent {
    data class SessionsChanged(val sessions: List<SessionResponse>) : ServerEvent()
    data class SessionUpdated(val session: SessionResponse) : ServerEvent()
    data class ProjectsChanged(val projects: List<ProjectInfo>) : ServerEvent()
    data class AutoScanStatus(val status: ScanStatusResponse) : ServerEvent()
    data class AutoScanEvent(
        val eventType: String,
        val timestamp: Double,
        val data: JsonElement?,
    ) : ServerEvent()
}

@Serializable
data class SessionsChangedPayload(val sessions: List<SessionResponse>)

@Serializable
data class SessionUpdatedPayload(val session: SessionResponse)

@Serializable
data class ProjectsChangedPayload(val projects: List<ProjectInfo>)

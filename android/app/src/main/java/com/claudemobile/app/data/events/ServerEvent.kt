package com.claudemobile.app.data.events

import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.network.dto.SessionResponse
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** Wire envelope: {"type": "...", "payload": {...}}. */
@Serializable
data class ServerEventEnvelope(
    val type: String,
    val payload: JsonElement,
)

/** Structured payload for an auto-scan progress event (matches server). */
sealed class ScanProgressPayload {
    data class Phase(
        val phase: String,           // scan_started | scan_completed | scan_stopped
        val cycleNumber: Int,
        val nextScanAt: String?,
        val issuesProcessed: Int = 0, // # of issues processed in just-completed cycle
    ) : ScanProgressPayload()

    data class Issue(
        val identifier: String,
        val title: String,
        val step: String,            // state_update | label_removal | worktree_create | session_spawn
        val status: String,          // started | ok | failed
        val error: String?,
    ) : ScanProgressPayload()

    data class Worktree(
        val identifier: String,
        val path: String,
    ) : ScanProgressPayload()

    data class Session(
        val identifier: String,
        val sessionId: String,
        val pid: Int?,
    ) : ScanProgressPayload()

    data class Log(
        val line: String,
        val level: String,
        val ansi: Boolean,
    ) : ScanProgressPayload()

    /** Unknown/forward-compatible event types fall through here. */
    data class Unknown(val type: String) : ScanProgressPayload()
}

/** Typed events emitted by [ServerEventBus]. */
sealed class ServerEvent {
    data class SessionsChanged(val sessions: List<SessionResponse>) : ServerEvent()
    data class SessionUpdated(val session: SessionResponse) : ServerEvent()
    data class ProjectsChanged(val projects: List<ProjectInfo>) : ServerEvent()
    data class AutoScanStatus(val instance: AutoScanInstanceDto) : ServerEvent()
    data class AutoScanListChanged(val list: AutoScanListResponse) : ServerEvent()
    data class AutoScanEvent(
        val projectName: String,
        val seq: Long,
        val ts: String,
        val payload: ScanProgressPayload,
    ) : ServerEvent()
}

@Serializable
data class SessionsChangedPayload(val sessions: List<SessionResponse>)

@Serializable
data class SessionUpdatedPayload(val session: SessionResponse)

@Serializable
data class ProjectsChangedPayload(val projects: List<ProjectInfo>)

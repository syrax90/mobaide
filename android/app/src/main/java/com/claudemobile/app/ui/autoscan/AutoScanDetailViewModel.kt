package com.claudemobile.app.ui.autoscan

import androidx.compose.ui.text.AnnotatedString
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.events.ScanProgressPayload
import com.claudemobile.app.data.network.AutoScanWsClient
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.AutoScanUiEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filterIsInstance
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

const val LOG_RING_MAX = 2000
val PHASE_ORDER = listOf("scan", "state", "label", "worktree", "spawn")

private fun stepToPhase(step: String): String = when (step) {
    "state_update" -> "state"
    "label_removal" -> "label"
    "worktree_create" -> "worktree"
    "session_spawn" -> "spawn"
    else -> step
}

@HiltViewModel
class AutoScanDetailViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val repository: AutoScanRepository,
) : ViewModel() {
    val projectName: String = checkNotNull(savedStateHandle["projectName"])

    enum class ConnectionState { Connecting, Connected, Replaying, Disconnected }

    data class IssueRow(
        val identifier: String,
        val title: String,
        val step: String,
        val status: String,
        val error: String?,
    )

    data class PhaseState(
        val status: String = "pending",   // pending | active | ok | failed | skipped
        val lines: List<AnnotatedString> = emptyList(),
    )

    /**
     * One line in the scrollable log pane.
     *
     * @param id Monotonically increasing id assigned at append time. Never
     *           recycled, so it stays unique even after the ring buffer
     *           evicts the oldest entry. Used as the LazyColumn key so
     *           duplicate-text lines (common when idle scans repeat the
     *           same "no AutoPipeline issues" message) do not collide
     *           and crash the list.
     */
    data class LogLine(val id: Long, val text: AnnotatedString)

    data class UiState(
        val instance: AutoScanInstanceDto? = null,
        val nextScanAt: String? = null,
        val cycleNumber: Int = 0,
        val activePhase: String? = null,
        val phases: Map<String, PhaseState> = PHASE_ORDER.associateWith { PhaseState() },
        val recentIssues: List<IssueRow> = emptyList(),
        val logLines: List<LogLine> = emptyList(),
        val newLineCount: Int = 0,
        val isPausedAutoscroll: Boolean = false,
        val connection: ConnectionState = ConnectionState.Connecting,
        val lastSeq: Long = 0L,
    )

    private val _ui = MutableStateFlow(UiState())
    val uiState: StateFlow<UiState> = _ui.asStateFlow()

    /** One-shot error messages from the repository for Snackbar consumption. */
    val errors: Flow<String> =
        repository.events.filterIsInstance<AutoScanUiEvent.Error>().map { it.message }

    private val logRing: ArrayDeque<LogLine> = ArrayDeque(LOG_RING_MAX)
    private var nextLogId: Long = 0L
    private var streamJob: Job? = null

    init {
        startStream()
    }

    fun startStream() {
        streamJob?.cancel()
        val resume = if (_ui.value.lastSeq > 0L) mapOf(projectName to _ui.value.lastSeq)
                     else emptyMap()
        streamJob = viewModelScope.launch {
            repository.observeDetailFrames(projectName, resume).collect { frame ->
                handleFrame(frame)
            }
        }
    }

    fun stopStream() {
        streamJob?.cancel()
        streamJob = null
    }

    private fun handleFrame(frame: AutoScanWsClient.Frame) {
        when (frame) {
            is AutoScanWsClient.Frame.Connected -> {
                _ui.update { it.copy(connection = ConnectionState.Connecting) }
            }
            is AutoScanWsClient.Frame.Snapshot -> {
                val instance = frame.scans.firstOrNull { it.project_name == projectName }
                _ui.update {
                    it.copy(
                        instance = instance,
                        nextScanAt = instance?.next_scan_at,
                        cycleNumber = instance?.cycle_number ?: it.cycleNumber,
                        connection = ConnectionState.Replaying,
                    )
                }
                frame.events[projectName]?.forEach { reduceEvent(it) }
            }
            is AutoScanWsClient.Frame.CaughtUp -> {
                _ui.update { it.copy(connection = ConnectionState.Connected) }
            }
            is AutoScanWsClient.Frame.Event -> {
                if (frame.event.projectName == projectName) reduceEvent(frame.event)
            }
            is AutoScanWsClient.Frame.Disconnected -> {
                _ui.update { it.copy(connection = ConnectionState.Disconnected) }
            }
        }
    }

    private fun reduceEvent(ev: AutoScanWsClient.ParsedEvent) {
        val newSeq = ev.seq.coerceAtLeast(_ui.value.lastSeq)
        when (val p = ev.payload) {
            is ScanProgressPayload.Phase -> handlePhase(p, newSeq)
            is ScanProgressPayload.Issue -> handleIssue(p, newSeq)
            is ScanProgressPayload.Worktree -> appendLog("worktree: ${p.path}", newSeq)
            is ScanProgressPayload.Session -> appendLog(
                "session: ${p.sessionId}${if (p.pid != null) " (pid=${p.pid})" else ""}",
                newSeq,
            )
            is ScanProgressPayload.Log -> {
                val parsed = if (p.ansi) AnsiParser.parse(p.line) else AnnotatedString(p.line)
                appendLogAnnotated(parsed, newSeq)
            }
            is ScanProgressPayload.Unknown -> Unit
        }
    }

    private fun handlePhase(p: ScanProgressPayload.Phase, newSeq: Long) {
        when (p.phase) {
            "scan_started" -> {
                // recentIssues is NOT cleared: idle scans (0 issues) must not blank the list.
                _ui.update {
                    it.copy(
                        cycleNumber = p.cycleNumber,
                        activePhase = "scan",
                        phases = it.phases.mapValues { (k, _) ->
                            if (k == "scan") PhaseState("active") else PhaseState()
                        },
                        lastSeq = newSeq,
                    )
                }
                appendLog("--- Scan #${p.cycleNumber} started ---", newSeq)
            }
            "scan_completed" -> {
                val idle = p.issuesProcessed == 0
                _ui.update {
                    it.copy(
                        nextScanAt = p.nextScanAt,
                        activePhase = null,
                        phases = it.phases.mapValues { (_, v) ->
                            when {
                                v.status == "active" -> v.copy(status = "ok")
                                idle && v.status == "pending" -> v.copy(status = "skipped")
                                else -> v
                            }
                        },
                        lastSeq = newSeq,
                    )
                }
                appendLog("--- Scan #${p.cycleNumber} completed ---", newSeq)
                if (idle) {
                    appendLog(
                        "no AutoPipeline issues — skipping remaining stages",
                        newSeq,
                    )
                }
            }
            "scan_stopped" -> {
                _ui.update { it.copy(activePhase = null, lastSeq = newSeq) }
                appendLog("--- Scan stopped ---", newSeq)
            }
        }
    }

    private fun handleIssue(p: ScanProgressPayload.Issue, newSeq: Long) {
        val phase = stepToPhase(p.step)
        val newPhaseStatus = when (p.status) {
            "started" -> "active"
            "ok" -> "ok"
            "failed" -> "failed"
            else -> "pending"
        }
        _ui.update { ui ->
            val phases = ui.phases.toMutableMap()
            phases[phase] = (phases[phase] ?: PhaseState()).copy(status = newPhaseStatus)
            val issues = ui.recentIssues.toMutableList().apply {
                add(IssueRow(p.identifier, p.title, p.step, p.status, p.error))
                if (size > 50) removeAt(0)
            }
            ui.copy(
                phases = phases,
                activePhase = if (newPhaseStatus == "active") phase else ui.activePhase,
                recentIssues = issues,
                lastSeq = newSeq,
            )
        }
        val errStr = p.error?.let { " [$it]" } ?: ""
        appendLog("${p.identifier} ${p.step} ${p.status}$errStr", newSeq)
    }

    private fun appendLog(line: String, newSeq: Long) {
        appendLogAnnotated(AnnotatedString(line), newSeq)
    }

    private fun appendLogAnnotated(line: AnnotatedString, newSeq: Long) {
        if (logRing.size >= LOG_RING_MAX) logRing.removeFirst()
        val entry = LogLine(id = nextLogId++, text = line)
        logRing.addLast(entry)
        _ui.update {
            it.copy(
                logLines = logRing.toList(),
                lastSeq = newSeq,
                newLineCount = if (it.isPausedAutoscroll) it.newLineCount + 1 else 0,
            )
        }
    }

    fun runNow() {
        viewModelScope.launch {
            try {
                val response = repository.runNow(projectName)
                // Update immediately from REST so the UI resets before the bus event arrives.
                _ui.update { it.copy(nextScanAt = response.next_scan_at) }
            } catch (_: Exception) {
                // Repository already emitted an AutoScanUiEvent.Error.
            }
        }
    }

    fun stop() {
        viewModelScope.launch {
            try { repository.stopScan(projectName) } catch (_: Exception) { }
        }
    }

    fun setPaused(paused: Boolean) {
        _ui.update { it.copy(isPausedAutoscroll = paused, newLineCount = if (paused) it.newLineCount else 0) }
    }

    fun resumeAutoscroll() {
        _ui.update { it.copy(isPausedAutoscroll = false, newLineCount = 0) }
    }

    override fun onCleared() {
        super.onCleared()
        streamJob?.cancel()
    }
}

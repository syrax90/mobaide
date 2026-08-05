package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.AgentUpdateRepository
import com.mobaide.app.data.repository.AgentVersionMonitor
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SessionListUiState(
    val sessions: List<SessionResponse> = emptyList(),
    val isReconnecting: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
    val activeScans: List<AutoScanInstanceDto> = emptyList(),
    val outdated: AgentVersionMonitor.Outdated? = null,
    // Live custom-agent-type names keyed by the full wire kind
    // ("custom_<id>" → name). Fallback label source for custom sessions whose
    // wire kind_label snapshot is absent.
    val customTypeNames: Map<String, String> = emptyMap(),
)

@HiltViewModel
class SessionListViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
    private val agentEventBus: AgentEventBus,
    private val agentVersionMonitor: AgentVersionMonitor,
    private val agentUpdateRepository: AgentUpdateRepository,
    private val customAgentTypesRepository: CustomAgentTypesRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SessionListUiState())
    val uiState: StateFlow<SessionListUiState> = _uiState.asStateFlow()

    private val _autoResumeSessionId = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val autoResumeSessionId: SharedFlow<String> = _autoResumeSessionId.asSharedFlow()

    private var didTryAutoResume = false

    init {
        agentEventBus.start()

        viewModelScope.launch {
            combine(
                sessionRepository.observeSessionList(),
                sessionRepository.observeSessionsSynced(),
            ) { sessions, synced -> sessions to synced }
                .collect { (sessions, synced) ->
                    _uiState.update { it.copy(sessions = sessions) }
                    maybeAutoResume(sessions, synced)
                }
        }

        viewModelScope.launch {
            sessionRepository.endedSessionNotices.collect {
                _uiState.update { it.copy(notice = "That session has ended.") }
            }
        }

        viewModelScope.launch {
            autoScanRepository.observeList().collect { list ->
                _uiState.update {
                    it.copy(activeScans = list.scans)
                }
            }
        }

        // Initial REST seed for the multi-scan list.
        viewModelScope.launch {
            try {
                autoScanRepository.refresh()
            } catch (_: Exception) {
                // Auto scan may not be configured — ignore.
            }
        }

        viewModelScope.launch {
            agentEventBus.connection.collect { state ->
                _uiState.update {
                    it.copy(isReconnecting = state == AgentEventBus.ConnState.Connecting)
                }
            }
        }

        viewModelScope.launch {
            customAgentTypesRepository.observeTypes().collect { types ->
                _uiState.update { state ->
                    state.copy(
                        customTypeNames = types.associate { "custom_${it.id}" to it.name },
                    )
                }
            }
        }

        // Best-effort cold-start seed for the label fallback; steady-state
        // freshness comes from UserEventBus's connect-time reconcile.
        viewModelScope.launch {
            try {
                customAgentTypesRepository.refreshTypes()
            } catch (_: Exception) {
                // Custom sessions fall back to their kind_label snapshot.
            }
        }

        // One-shot probe of the agent's running version vs. the rolled-out
        // manifest pointer — drives the AgentOutdatedBanner. Failure is
        // silent: the banner simply does not appear until a future probe
        // succeeds (e.g. on pull-to-refresh).
        viewModelScope.launch { agentVersionMonitor.probe() }

        viewModelScope.launch {
            agentVersionMonitor.outdated.collect { outdated ->
                _uiState.update { it.copy(outdated = outdated) }
            }
        }
    }

    /** Resumes only against a snapshot the connected agent just pushed. A list
     *  left over from a dropped tunnel can still show a session as `running`
     *  after the agent restarted and lost it, and resuming into that would open
     *  a terminal for a session that no longer exists. */
    private suspend fun maybeAutoResume(sessions: List<SessionResponse>, synced: Boolean) {
        if (didTryAutoResume || !synced || sessions.isEmpty()) return
        didTryAutoResume = true
        val lastId = preferencesStore.getLastUsedSessionId() ?: return
        val match = sessions.firstOrNull { it.session_id == lastId } ?: return
        if (match.state == "running" || match.state == "starting") {
            _autoResumeSessionId.tryEmit(lastId)
        } else {
            preferencesStore.setLastUsedSessionId(null)
        }
    }

    fun refresh() {
        agentEventBus.forceReconnect()
        viewModelScope.launch {
            try {
                autoScanRepository.refresh()
            } catch (_: Exception) {
                // Auto scan may not be configured — ignore.
            }
        }
        viewModelScope.launch { agentVersionMonitor.probe() }
    }

    fun forceUpdateNow() {
        viewModelScope.launch {
            val msg = agentUpdateRepository.forceUpdateNow().fold(
                onSuccess = { resp ->
                    when (resp.outcome) {
                        "staged" -> "Update staged — agent restarting…"
                        "no_change" -> "Agent is already up to date."
                        "deferred" -> "Agent busy — will retry when idle."
                        "signature_failed" -> "Update rejected: signature verification failed."
                        else -> "Update outcome: ${resp.outcome}"
                    }
                },
                onFailure = { e -> "Update failed: ${e.message ?: e::class.simpleName}" },
            )
            _uiState.update { it.copy(error = msg) }
        }
    }

    fun deleteSession(sessionId: String) {
        viewModelScope.launch {
            try {
                sessionRepository.deleteSession(sessionId)
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to stop session: ${e.message}") }
            }
        }
    }

    fun stopAutoScan(projectName: String) {
        viewModelScope.launch {
            try {
                autoScanRepository.stopScan(projectName)
            } catch (e: Exception) {
                _uiState.update { it.copy(error = "Failed to stop scan: ${e.message}") }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    fun clearNotice() {
        _uiState.update { it.copy(notice = null) }
    }
}

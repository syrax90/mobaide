package com.mobaide.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.dto.KeepAwakeStatus
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentSettingsRepository
import com.mobaide.app.ui.voice.DEFAULT_AGENT_NAME
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.transformLatest
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class KeepAwakeAvailability {
    /** No agent is selected: the section says to connect to one. */
    NoAgent,
    Checking,

    /** The agent did not answer. */
    Unreachable,

    /** The agent predates the setting (no `capabilities.agent_settings.keep_awake`). */
    Outdated,
    Available,
}

data class KeepAwakeUiState(
    val agentName: String = DEFAULT_AGENT_NAME,
    val availability: KeepAwakeAvailability = KeepAwakeAvailability.Checking,
    val status: KeepAwakeStatus? = null,
    /** The value being saved: the box shows it until the agent answers. */
    val saving: Boolean? = null,
    /** The agent refused or missed the last change; the box shows its old value. */
    val saveFailed: Boolean = false,
)

/**
 * Keep awake for the connected agent. The status lives in
 * [AgentSettingsRepository], which `agent_settings.changed` keeps fresh, so a
 * change made on another device, or a lock the computer lost, shows at once.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class KeepAwakeViewModel @Inject constructor(
    private val repository: AgentSettingsRepository,
    agentRepository: AgentRepository,
    tokenStore: TokenStore,
) : ViewModel() {
    private val agentKey: String? = tokenStore.getAgentId() ?: tokenStore.getAgentUrl()
    private val availability = MutableStateFlow(
        if (agentKey == null) KeepAwakeAvailability.NoAgent else KeepAwakeAvailability.Checking,
    )
    private val saving = MutableStateFlow<Boolean?>(null)
    private val saveFailed = MutableStateFlow(false)
    private var probe: Job? = null

    private val agentName = agentRepository.observeAgents().map { agents ->
        agents.firstOrNull { it.agent_id == tokenStore.getAgentId() }?.name ?: DEFAULT_AGENT_NAME
    }

    /**
     * Nothing says that an agent which did not answer is back, so while it
     * does not answer it is asked again by itself, like the voice section,
     * only while the screen collects [uiState].
     */
    private val unreachablePoll: Flow<Unit> = availability
        .map { it == KeepAwakeAvailability.Unreachable }
        .distinctUntilChanged()
        .transformLatest { unreachable ->
            while (unreachable) {
                delay(UNREACHABLE_POLL_MS)
                refresh()
                probe?.join()
            }
            emit(Unit)
        }
        .onStart { emit(Unit) }

    val uiState: StateFlow<KeepAwakeUiState> = combine(
        combine(availability, repository.settings, agentName, ::Triple),
        saving,
        saveFailed,
        unreachablePoll,
    ) { (available, settings, name), pending, failed, _ ->
        KeepAwakeUiState(
            agentName = name,
            availability = available,
            status = settings?.keep_awake,
            saving = pending,
            saveFailed = failed,
        )
    }.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        KeepAwakeUiState(availability = availability.value),
    )

    init {
        refresh()
    }

    /** Asks the agent again; the screen calls it each time it comes on screen. */
    fun refresh() {
        if (agentKey == null || probe?.isActive == true) return
        probe = viewModelScope.launch {
            val supported = try {
                repository.keepAwakeSupported()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                null
            }
            availability.value = when (supported) {
                null -> KeepAwakeAvailability.Unreachable
                false -> KeepAwakeAvailability.Outdated
                true -> KeepAwakeAvailability.Available
            }
            if (supported != true) return@launch
            try {
                repository.refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                if (repository.settings.value == null) availability.value = KeepAwakeAvailability.Unreachable
            }
        }
    }

    fun setKeepAwake(enabled: Boolean) {
        if (saving.value != null) return
        saving.value = enabled
        saveFailed.value = false
        viewModelScope.launch {
            try {
                repository.setKeepAwake(enabled)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                saveFailed.value = true
            } finally {
                saving.value = null
            }
        }
    }

    private companion object {
        const val UNREACHABLE_POLL_MS = 5_000L
    }
}

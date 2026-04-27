package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class CreateSessionStep { ProjectSelect, ModePick, StandardConfig, AutoScanConfig }

enum class CreateSessionMode { Standard, AutoScan }

data class CreateSessionUiState(
    val step: CreateSessionStep = CreateSessionStep.ProjectSelect,
    val projects: List<ProjectInfo> = emptyList(),
    val isLoadingProjects: Boolean = false,
    val selectedProjectName: String? = null,
    val selectedMode: CreateSessionMode? = null,
    val planMode: Boolean = false,
    val noMemory: Boolean = false,
    val skipPermissions: Boolean = true,
    val maxEffort: Boolean = true,
    val initialPrompt: String = "",
    val worktree: Boolean = false,
    val branchName: String = "",
    val submitting: Boolean = false,
    val autoScanStarting: Boolean = false,
    val autoScanConfigInitialized: Boolean = false,
    val dismiss: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class CreateSessionViewModel @Inject constructor(
    private val projectsRepository: ProjectsRepository,
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(CreateSessionUiState())
    val uiState: StateFlow<CreateSessionUiState> = _uiState.asStateFlow()

    private val _navigateToSession = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val navigateToSession: SharedFlow<String> = _navigateToSession.asSharedFlow()

    private val _navigateToAutoScan = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val navigateToAutoScan: SharedFlow<String> = _navigateToAutoScan.asSharedFlow()

    private val _transientMessages = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val transientMessages: SharedFlow<String> = _transientMessages.asSharedFlow()

    init {
        viewModelScope.launch {
            val last = preferencesStore.getLastUsedProjectName()
            projectsRepository.observeProjects().collect { projects ->
                _uiState.update { state ->
                    state.copy(
                        projects = projects,
                        isLoadingProjects = projects.isEmpty(),
                        selectedProjectName = state.selectedProjectName
                            ?: projects.firstOrNull { it.name == last }?.name,
                    )
                }
            }
        }

        // REST seed: populates projects if the bus snapshot hasn't arrived yet.
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingProjects = true) }
            try {
                val projects = projectsRepository.listProjects()
                val last = preferencesStore.getLastUsedProjectName()
                _uiState.update { state ->
                    if (state.projects.isEmpty()) {
                        state.copy(
                            projects = projects,
                            isLoadingProjects = false,
                            selectedProjectName = state.selectedProjectName
                                ?: projects.firstOrNull { it.name == last }?.name,
                        )
                    } else {
                        state.copy(isLoadingProjects = false)
                    }
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoadingProjects = false, error = "Failed to load projects: ${e.message}")
                }
            }
        }
    }

    fun selectProject(name: String) = _uiState.update {
        // Clamp worktree off for non-git projects so the launch payload stays consistent.
        val isGit = it.projects.firstOrNull { p -> p.name == name }?.is_git == true
        it.copy(
            selectedProjectName = name,
            worktree = if (isGit) it.worktree else false,
            branchName = if (isGit) it.branchName else "",
        )
    }

    fun confirmProject() = _uiState.update {
        if (it.selectedProjectName != null) it.copy(step = CreateSessionStep.ModePick) else it
    }

    fun pickMode(mode: CreateSessionMode) = _uiState.update {
        it.copy(selectedMode = mode)
    }

    fun selectStandardMode() = _uiState.update {
        it.copy(step = CreateSessionStep.StandardConfig)
    }

    fun selectAutoScanMode() = _uiState.update {
        if (it.autoScanConfigInitialized) {
            it.copy(step = CreateSessionStep.AutoScanConfig)
        } else {
            it.copy(
                step = CreateSessionStep.AutoScanConfig,
                autoScanConfigInitialized = true,
                worktree = true,
                initialPrompt = autoScanRepository.getAutoScanPrompt() ?: "",
            )
        }
    }

    fun submitAutoScan() {
        val state = _uiState.value
        val projectName = state.selectedProjectName ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(autoScanStarting = true) }
            try {
                val options = ClaudeOptionsModel(
                    plan_mode = state.planMode,
                    no_memory = state.noMemory,
                    skip_permissions = state.skipPermissions,
                    max_effort = state.maxEffort,
                    worktree = true,
                    initial_prompt = state.initialPrompt.takeIf { it.isNotBlank() },
                )
                autoScanRepository.startScan(projectName, options)
                preferencesStore.setLastUsedProjectName(projectName)
                _uiState.update { it.copy(autoScanStarting = false) }
                _navigateToAutoScan.tryEmit(projectName)
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        autoScanStarting = false,
                        error = "Failed to start Auto Scan: ${e.message}",
                    )
                }
            }
        }
    }

    fun goBack() = _uiState.update {
        when (it.step) {
            CreateSessionStep.StandardConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.AutoScanConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.ModePick -> it.copy(
                step = CreateSessionStep.ProjectSelect,
                selectedMode = null,
            )
            CreateSessionStep.ProjectSelect -> it
        }
    }

    fun togglePlanMode(v: Boolean) = _uiState.update { it.copy(planMode = v) }
    fun toggleNoMemory(v: Boolean) = _uiState.update { it.copy(noMemory = v) }
    fun toggleSkipPermissions(v: Boolean) = _uiState.update { it.copy(skipPermissions = v) }
    fun toggleMaxEffort(v: Boolean) = _uiState.update { it.copy(maxEffort = v) }
    fun toggleWorktree(v: Boolean) = _uiState.update { it.copy(worktree = v) }
    fun setBranchName(v: String) = _uiState.update { it.copy(branchName = v) }
    fun setInitialPrompt(v: String) = _uiState.update { it.copy(initialPrompt = v) }
    fun clearError() = _uiState.update { it.copy(error = null) }

    /** Reset the dismiss flag after the screen has consumed it. */
    fun consumeDismiss() = _uiState.update { it.copy(dismiss = false) }

    fun submit() {
        val state = _uiState.value
        val projectName = state.selectedProjectName ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(submitting = true) }
            try {
                val response = sessionRepository.createSession(
                    CreateSessionRequest(
                        project_name = projectName,
                        claude_options = ClaudeOptionsModel(
                            no_memory = state.noMemory,
                            skip_permissions = state.skipPermissions,
                            max_effort = state.maxEffort,
                            worktree = state.worktree,
                            branch_name = state.branchName.takeIf { it.isNotBlank() },
                        ),
                    ),
                )
                preferencesStore.setLastUsedProjectName(projectName)
                preferencesStore.setLastUsedSessionId(response.session_id)
                _uiState.update { it.copy(submitting = false) }
                _navigateToSession.tryEmit(response.session_id)
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(submitting = false, error = "Failed to create session: ${e.message}")
                }
            }
        }
    }
}

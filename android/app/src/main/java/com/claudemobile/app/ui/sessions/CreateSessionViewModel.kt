package com.claudemobile.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.dto.ClaudeOptionsModel
import com.claudemobile.app.data.network.dto.CreateSessionRequest
import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.ProjectsRepository
import com.claudemobile.app.data.repository.SessionRepository
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

enum class CreateSessionStep { ProjectSelect, ModePick, StandardConfig }

data class CreateSessionUiState(
    val step: CreateSessionStep = CreateSessionStep.ProjectSelect,
    val projects: List<ProjectInfo> = emptyList(),
    val isLoadingProjects: Boolean = false,
    val selectedProjectName: String? = null,
    val planMode: Boolean = false,
    val skipPermissions: Boolean = false,
    val noMemory: Boolean = false,
    val worktree: Boolean = false,
    val branchName: String = "",
    val initialPrompt: String = "",
    val submitting: Boolean = false,
    val autoScanStarting: Boolean = false,
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

    init {
        loadProjects()
    }

    private fun loadProjects() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingProjects = true) }
            try {
                val projects = projectsRepository.listProjects()
                val last = preferencesStore.getLastUsedProjectName()
                val preselected = projects.firstOrNull { it.name == last }?.name
                _uiState.update {
                    it.copy(
                        projects = projects,
                        isLoadingProjects = false,
                        selectedProjectName = preselected,
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoadingProjects = false, error = "Failed to load projects: ${e.message}")
                }
            }
        }
    }

    fun selectProject(name: String) = _uiState.update {
        // Selecting a project advances to the mode picker (CLA-15). The
        // standard config form is only revealed once the user picks a mode.
        it.copy(selectedProjectName = name, step = CreateSessionStep.ModePick)
    }

    fun selectStandardMode() = _uiState.update {
        it.copy(step = CreateSessionStep.StandardConfig)
    }

    fun selectAutoScanMode() {
        val state = _uiState.value
        val projectName = state.selectedProjectName ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(autoScanStarting = true) }
            try {
                autoScanRepository.startScan(projectName)
                preferencesStore.setLastUsedProjectName(projectName)
                _uiState.update { it.copy(autoScanStarting = false, dismiss = true) }
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
            CreateSessionStep.ModePick -> it.copy(
                step = CreateSessionStep.ProjectSelect,
                selectedProjectName = null,
            )
            CreateSessionStep.ProjectSelect -> it
        }
    }

    fun togglePlanMode(v: Boolean) = _uiState.update { it.copy(planMode = v) }
    fun toggleSkipPermissions(v: Boolean) = _uiState.update { it.copy(skipPermissions = v) }
    fun toggleNoMemory(v: Boolean) = _uiState.update { it.copy(noMemory = v) }
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
                            plan_mode = state.planMode,
                            skip_permissions = state.skipPermissions,
                            no_memory = state.noMemory,
                            worktree = state.worktree,
                            branch_name = state.branchName.takeIf { it.isNotBlank() },
                            initial_prompt = state.initialPrompt.takeIf { it.isNotBlank() },
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

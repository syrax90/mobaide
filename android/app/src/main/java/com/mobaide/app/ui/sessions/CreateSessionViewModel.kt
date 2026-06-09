package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
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
import java.nio.file.InvalidPathException
import java.nio.file.Paths
import javax.inject.Inject

enum class CreateSessionStep { ProjectSelect, ModePick, StandardConfig, AutoScanConfig, TerminalConfig }

enum class CreateSessionMode { Standard, AutoScan, CustomShell }

/**
 * Active selection on the project picker.
 *
 * `None` is the freshly-mounted state; `Stored` references a project from
 * [CreateSessionUiState.projects] by name; `CustomPath` is an ephemeral
 * one-off launch directory typed by the user and never persisted to
 * `projects.json` on the agent.
 */
sealed interface ProjectSelection {
    data object None : ProjectSelection
    data class Stored(val name: String) : ProjectSelection
    data class CustomPath(val path: String) : ProjectSelection
}

data class CreateSessionUiState(
    val step: CreateSessionStep = CreateSessionStep.ProjectSelect,
    val projects: List<ProjectInfo> = emptyList(),
    val isLoadingProjects: Boolean = false,
    val selection: ProjectSelection = ProjectSelection.None,
    val customPathDraft: String = "",
    val customPathError: String? = null,
    val recentCustomPaths: List<String> = emptyList(),
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

/**
 * Read-only projection: the name of a stored project, or `null` for any
 * other selection (custom path / none). Lets callers that only care about
 * stored-project selections stay terse.
 */
val CreateSessionUiState.selectedProjectName: String?
    get() = (selection as? ProjectSelection.Stored)?.name

@HiltViewModel
class CreateSessionViewModel @Inject constructor(
    private val projectsRepository: ProjectsRepository,
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
    private val tokenStore: TokenStore,
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
                    val resolved = state.selection.takeUnless { it is ProjectSelection.None }
                        ?: projects.firstOrNull { it.name == last }?.let { ProjectSelection.Stored(it.name) }
                        ?: ProjectSelection.None
                    state.copy(
                        projects = projects,
                        isLoadingProjects = projects.isEmpty(),
                        selection = resolved,
                    )
                }
            }
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingProjects = true) }
            try {
                val projects = projectsRepository.listProjects()
                val last = preferencesStore.getLastUsedProjectName()
                _uiState.update { state ->
                    if (state.projects.isEmpty()) {
                        val resolved = state.selection.takeUnless { it is ProjectSelection.None }
                            ?: projects.firstOrNull { it.name == last }?.let { ProjectSelection.Stored(it.name) }
                            ?: ProjectSelection.None
                        state.copy(
                            projects = projects,
                            isLoadingProjects = false,
                            selection = resolved,
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
        val isGit = it.projects.firstOrNull { p -> p.name == name }?.is_git == true
        it.copy(
            selection = ProjectSelection.Stored(name),
            customPathDraft = "",
            customPathError = null,
            worktree = if (isGit) it.worktree else false,
            branchName = if (isGit) it.branchName else "",
        )
    }

    /**
     * Called after `ProjectsViewModel.projectCreated` emits — uses the new
     * project's own `is_git` directly rather than looking it up in
     * `state.projects`, which has not yet been refreshed by the
     * `AgentEvent.ProjectsChanged` bus event.
     */
    fun onProjectCreated(project: ProjectInfo) = _uiState.update {
        it.copy(
            selection = ProjectSelection.Stored(project.name),
            customPathDraft = "",
            customPathError = null,
            worktree = if (project.is_git) it.worktree else false,
            branchName = if (project.is_git) it.branchName else "",
        )
    }

    /**
     * Switches the picker into "Custom path…" mode. Loads any per-agent
     * recents asynchronously so the chips show up as soon as the panel
     * renders. Clears any prior draft / error.
     */
    fun selectCustomPath() {
        _uiState.update {
            it.copy(
                selection = ProjectSelection.CustomPath(""),
                customPathDraft = "",
                customPathError = null,
                worktree = false,
                branchName = "",
            )
        }
        val scope = agentScopeKey() ?: return
        viewModelScope.launch {
            val recents = preferencesStore.getRecentCustomPaths(scope)
            _uiState.update { it.copy(recentCustomPaths = recents) }
        }
    }

    fun setCustomPathDraft(value: String) = _uiState.update {
        val error = validateCustomPath(value)
        it.copy(
            customPathDraft = value,
            customPathError = error,
            selection = if (error == null && value.isNotBlank()) {
                ProjectSelection.CustomPath(value)
            } else {
                ProjectSelection.CustomPath("")
            },
        )
    }

    fun pickRecentCustomPath(path: String) = setCustomPathDraft(path)

    fun confirmProject() = _uiState.update { state ->
        when (val sel = state.selection) {
            is ProjectSelection.Stored -> state.copy(step = CreateSessionStep.ModePick)
            is ProjectSelection.CustomPath ->
                if (sel.path.isNotBlank() && state.customPathError == null) {
                    state.copy(step = CreateSessionStep.ModePick)
                } else {
                    state
                }
            ProjectSelection.None -> state
        }
    }

    fun pickMode(mode: CreateSessionMode) {
        _uiState.update { it.copy(selectedMode = mode) }
    }

    fun selectStandardMode() = _uiState.update {
        it.copy(step = CreateSessionStep.StandardConfig)
    }

    /**
     * Continue from the mode picker for Terminal mode. A stored git project
     * opens the Terminal session page (worktree toggle); anything else
     * (non-git project, custom path) has nothing to configure, so the shell
     * session is created immediately rather than showing an empty sheet.
     */
    fun selectTerminalMode() {
        val state = _uiState.value
        val storedGit = (state.selection as? ProjectSelection.Stored)?.let { sel ->
            state.projects.firstOrNull { it.name == sel.name }?.is_git == true
        } ?: false
        if (storedGit) {
            _uiState.update { it.copy(step = CreateSessionStep.TerminalConfig) }
        } else {
            submitCustomShell()
        }
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
        val projectName = (state.selection as? ProjectSelection.Stored)?.name ?: return
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
            CreateSessionStep.TerminalConfig -> it.copy(step = CreateSessionStep.ModePick)
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

    fun consumeDismiss() = _uiState.update { it.copy(dismiss = false) }

    fun submit() {
        val state = _uiState.value
        if (state.submitting) return
        val claudeOptions = ClaudeOptionsModel(
            no_memory = state.noMemory,
            skip_permissions = state.skipPermissions,
            max_effort = state.maxEffort,
            worktree = state.worktree,
            branch_name = state.branchName.takeIf { it.isNotBlank() },
        )
        when (val sel = state.selection) {
            is ProjectSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    project_name = sel.name,
                    claude_options = claudeOptions,
                ),
            )
            is ProjectSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        project_name = null,
                        cwd = sel.path,
                        claude_options = claudeOptions.copy(worktree = false, branch_name = null),
                    ),
                )
            }
            ProjectSelection.None -> Unit
        }
    }

    fun submitCustomShell() {
        val state = _uiState.value
        if (state.submitting) return
        when (val sel = state.selection) {
            is ProjectSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    project_name = sel.name,
                    command = null,
                    claude_options = null,
                    worktree = state.worktree,
                    branch_name = state.branchName.takeIf { it.isNotBlank() },
                ),
            )
            is ProjectSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        project_name = null,
                        cwd = sel.path,
                        command = null,
                        claude_options = null,
                        worktree = false,
                        branch_name = null,
                    ),
                )
            }
            ProjectSelection.None -> Unit
        }
    }

    private fun submitSession(selection: ProjectSelection, request: CreateSessionRequest) {
        viewModelScope.launch {
            _uiState.update { it.copy(submitting = true) }
            try {
                val response = sessionRepository.createSession(request)
                when (selection) {
                    is ProjectSelection.Stored -> {
                        preferencesStore.setLastUsedProjectName(selection.name)
                    }
                    is ProjectSelection.CustomPath -> {
                        val scope = agentScopeKey()
                        if (scope != null) {
                            preferencesStore.addRecentCustomPath(scope, selection.path)
                        }
                    }
                    ProjectSelection.None -> Unit
                }
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

    private fun agentScopeKey(): String? = tokenStore.getAgentId() ?: tokenStore.getAgentUrl()

    private fun validateCustomPath(input: String): String? {
        if (input.isBlank()) return "Path is required"
        return try {
            if (Paths.get(input).isAbsolute || isWindowsAbsolute(input)) null
            else "Path must be absolute"
        } catch (_: InvalidPathException) {
            "Path is invalid"
        }
    }

    private fun isWindowsAbsolute(input: String): Boolean =
        WINDOWS_ABS_REGEX.containsMatchIn(input) || input.startsWith("\\\\")

    companion object {
        private val WINDOWS_ABS_REGEX = Regex("^[A-Za-z]:[\\\\/]")
    }
}

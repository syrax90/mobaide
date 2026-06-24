package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CodexOptionsModel
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
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.nio.file.InvalidPathException
import java.nio.file.Paths
import javax.inject.Inject

enum class CreateSessionStep { ProjectSelect, ModePick, StandardConfig, CodexConfig, AutoScanConfig, TerminalConfig }

enum class CreateSessionMode { Standard, Codex, AutoScan, CustomShell }

/**
 * Convenience autonomy presets that collapse Codex's two-axis sandbox×approval
 * model into one choice. [Auto] is the wizard default; [FullAccess] maps to
 * Codex's `--yolo` (full_auto). The Advanced controls can set any raw
 * sandbox/approval combination, in which case no preset is highlighted.
 */
enum class CodexAutonomyPreset { ReadOnly, Auto, FullAccess }

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
    // Codex launch options (CodexConfig step). Defaults reflect the "Auto" preset.
    val codexModel: String? = null,
    val codexSandbox: String = "workspace-write",
    val codexApproval: String = "on-request",
    val codexFullAuto: Boolean = false,
    val codexReasoningEffort: String = "high",
    val initialPrompt: String = "",
    val worktree: Boolean = false,
    val branchName: String = "",
    val submitting: Boolean = false,
    val autoScanStarting: Boolean = false,
    val autoScanConfigInitialized: Boolean = false,
    val dismiss: Boolean = false,
    val error: String? = null,
    // Codex login dialog. All fields are memory-only — the API key is
    // never persisted to disk on the device.
    val codexLoginVisible: Boolean = false,
    val codexApiKeyDraft: String = "",
    val codexApiKeyVisible: Boolean = false,
    val codexLoginSubmitting: Boolean = false,
    val codexLoginError: String? = null,
)

/**
 * Read-only projection: the name of a stored project, or `null` for any
 * other selection (custom path / none). Lets callers that only care about
 * stored-project selections stay terse.
 */
val CreateSessionUiState.selectedProjectName: String?
    get() = (selection as? ProjectSelection.Stored)?.name

/**
 * The autonomy preset currently reflected by the raw Codex sandbox/approval/
 * full-auto fields, or `null` when the user has dialed in a custom combination
 * via the Advanced controls (so the segmented control highlights nothing).
 */
val CreateSessionUiState.codexPreset: CodexAutonomyPreset?
    get() = when {
        codexFullAuto -> CodexAutonomyPreset.FullAccess
        codexSandbox == "read-only" && codexApproval == "on-request" -> CodexAutonomyPreset.ReadOnly
        codexSandbox == "workspace-write" && codexApproval == "on-request" -> CodexAutonomyPreset.Auto
        else -> null
    }

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

    fun selectCodexMode() = _uiState.update {
        it.copy(step = CreateSessionStep.CodexConfig)
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
            // Pre-seed from this project's last-saved Auto Scan prompt (persisted
            // server-side on the previous scan start, delivered via ProjectInfo);
            // fall back to the agent-wide default, then empty.
            val savedPrompt = it.selectedProjectName
                ?.let { name -> it.projects.firstOrNull { p -> p.name == name } }
                ?.auto_scan_prompt
            it.copy(
                step = CreateSessionStep.AutoScanConfig,
                autoScanConfigInitialized = true,
                worktree = true,
                initialPrompt = savedPrompt ?: autoScanRepository.getAutoScanPrompt() ?: "",
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
            CreateSessionStep.CodexConfig -> it.copy(step = CreateSessionStep.ModePick)
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

    /** Sets sandbox + approval + full-auto together from a one-tap autonomy preset. */
    fun selectCodexPreset(preset: CodexAutonomyPreset) = _uiState.update {
        when (preset) {
            CodexAutonomyPreset.ReadOnly ->
                it.copy(codexSandbox = "read-only", codexApproval = "on-request", codexFullAuto = false)
            CodexAutonomyPreset.Auto ->
                it.copy(codexSandbox = "workspace-write", codexApproval = "on-request", codexFullAuto = false)
            CodexAutonomyPreset.FullAccess ->
                it.copy(codexFullAuto = true)
        }
    }

    fun setCodexModel(v: String?) = _uiState.update { it.copy(codexModel = v) }
    fun setCodexReasoningEffort(v: String) = _uiState.update { it.copy(codexReasoningEffort = v) }
    fun setCodexSandbox(v: String) = _uiState.update { it.copy(codexSandbox = v) }
    fun setCodexApproval(v: String) = _uiState.update { it.copy(codexApproval = v) }

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

    fun submitCodex() {
        val state = _uiState.value
        if (state.submitting) return
        val codexOptions = CodexOptionsModel(
            model = state.codexModel?.takeIf { it.isNotBlank() },
            sandbox = state.codexSandbox,
            approval = state.codexApproval,
            reasoning_effort = state.codexReasoningEffort,
            full_auto = state.codexFullAuto,
            worktree = state.worktree,
            branch_name = state.branchName.takeIf { it.isNotBlank() },
        )
        when (val sel = state.selection) {
            is ProjectSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    project_name = sel.name,
                    cli = "codex",
                    codex_options = codexOptions,
                ),
            )
            is ProjectSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        project_name = null,
                        cwd = sel.path,
                        cli = "codex",
                        codex_options = codexOptions.copy(worktree = false, branch_name = null),
                    ),
                )
            }
            ProjectSelection.None -> Unit
        }
    }

    fun setCodexApiKeyDraft(v: String) = _uiState.update {
        it.copy(codexApiKeyDraft = v, codexLoginError = null)
    }

    fun toggleCodexApiKeyVisible() = _uiState.update {
        it.copy(codexApiKeyVisible = !it.codexApiKeyVisible)
    }

    /** Closes the Codex login dialog and wipes the typed key from memory. */
    fun dismissCodexLogin() = _uiState.update {
        it.copy(
            codexLoginVisible = false,
            codexApiKeyDraft = "",
            codexApiKeyVisible = false,
            codexLoginSubmitting = false,
            codexLoginError = null,
        )
    }

    /**
     * Sends the API key to the agent (`POST /api/codex/login`); on success wipes
     * the key, closes the dialog, and re-runs [submitCodex] so the session the
     * user originally asked for is created. The key lives only in transient
     * UiState — never persisted.
     */
    fun submitCodexLogin() {
        val key = _uiState.value.codexApiKeyDraft
        if (key.isBlank() || _uiState.value.codexLoginSubmitting) return
        viewModelScope.launch {
            _uiState.update { it.copy(codexLoginSubmitting = true, codexLoginError = null) }
            try {
                sessionRepository.codexLogin(key)
                _uiState.update {
                    it.copy(
                        codexLoginVisible = false,
                        codexApiKeyDraft = "",
                        codexApiKeyVisible = false,
                        codexLoginSubmitting = false,
                        codexLoginError = null,
                    )
                }
                submitCodex()
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(codexLoginSubmitting = false, codexLoginError = codexLoginErrorMessage(e))
                }
            }
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
                if (e is ApiException && isCodexAuthRequired(e.errorBody)) {
                    // Codex is installed but not signed in: prompt for an API key
                    // instead of a raw error, then retry the original create. The
                    // marker drives this (not the status code) — the server sends
                    // 409, not 401, so the AuthInterceptor's refresh-retry on 401
                    // can't swallow the marker before we read it.
                    _uiState.update {
                        it.copy(submitting = false, codexLoginVisible = true, codexLoginError = null)
                    }
                } else {
                    _uiState.update {
                        it.copy(submitting = false, error = "Failed to create session: ${e.message}")
                    }
                }
            }
        }
    }

    /**
     * True when an error body carries the `codex_auth_required` marker. The
     * marker is top-level on the relay path and nested under `detail` on direct
     * REST, so both shapes are checked; tolerant of any non-JSON body.
     */
    private fun isCodexAuthRequired(body: String): Boolean = runCatching {
        val obj = Json.parseToJsonElement(body).jsonObject
        obj.hasCodexAuthMarker() || (obj["detail"] as? JsonObject)?.hasCodexAuthMarker() == true
    }.getOrDefault(false)

    private fun JsonObject.hasCodexAuthMarker(): Boolean =
        this["codex_auth_required"]?.jsonPrimitive?.booleanOrNull == true

    /** Login-failure text that never echoes the submitted key (the agent already
     *  sanitizes its `detail`). */
    private fun codexLoginErrorMessage(e: Throwable): String = when {
        e is ApiException -> extractDetail(e.errorBody) ?: "Login failed (HTTP ${e.code})"
        else -> e.message ?: "Login failed"
    }

    private fun extractDetail(body: String): String? = runCatching {
        Json.parseToJsonElement(body).jsonObject["detail"]?.jsonPrimitive?.contentOrNull
    }.getOrNull()

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

        /**
         * Curated Codex model quick-picks for the wizard dropdown. The server's
         * `model` field is free-form, so "Default" (a null model, Codex's account
         * default) is always offered alongside these and the list is trivially
         * extended. Current as of 2026-06.
         */
        val CODEX_MODELS = listOf("gpt-5.5", "gpt-5.4-mini")

        /** Codex reasoning-effort levels, low→high; "high" is the wizard default. */
        val REASONING_EFFORTS = listOf("minimal", "low", "medium", "high")
    }
}

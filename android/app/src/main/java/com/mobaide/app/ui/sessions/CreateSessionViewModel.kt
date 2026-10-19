package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.fs.PathCompletion
import com.mobaide.app.data.local.AntigravityLaunchPrefs
import com.mobaide.app.data.local.ClaudeLaunchPrefs
import com.mobaide.app.data.local.CodexLaunchPrefs
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.AntigravityOptionsModel
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CodexOptionsModel
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.CliModelDetail
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.cliAvailable
import com.mobaide.app.data.network.dto.cliModelDetails
import com.mobaide.app.data.network.dto.cliModels
import com.mobaide.app.data.network.dto.cliModelsPending
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.ui.common.PathSuggestionsController
import com.mobaide.app.ui.common.PathSuggestionsUi
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.delay
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

enum class CreateSessionStep { ProjectSelect, ModePick, StandardConfig, CodexConfig, AntigravityConfig, AutoScanConfig, TerminalConfig }

/**
 * Mode-picker selection. The built-in modes are singletons; [Custom] carries
 * the server id of an account-scoped custom agent type (stable across rename,
 * so a selection survives a concurrent edit).
 */
sealed interface CreateSessionMode {
    data object Standard : CreateSessionMode
    data object Codex : CreateSessionMode
    data object Antigravity : CreateSessionMode
    data object AutoScan : CreateSessionMode
    data object CustomShell : CreateSessionMode
    data class Custom(val typeId: String) : CreateSessionMode
}

/** Custom-agent-type editor sheet state; a null [Open.target] means "create". */
sealed interface TypeEditorState {
    data object Hidden : TypeEditorState
    data class Open(val target: CustomAgentTypeDto?) : TypeEditorState
}

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
    // CLI availability for the ModePick gate (binary-on-PATH per managed CLI),
    // re-probed each time the user reaches the step. null = unknown / not-yet-
    // probed / probe failed → treated as available (optimistic) so a transient
    // failure never disables a working mode; only an explicit false from the
    // agent grays a card out. The create-time 503 (CLI-missing) path stays the
    // authoritative backstop.
    val claudeAvailable: Boolean? = null,
    val codexAvailable: Boolean? = null,
    val antigravityAvailable: Boolean? = null,
    val cliAvailabilityLoading: Boolean = false,
    // Per-CLI model lists from the agent's capabilities (capabilities.cli.<id>
    // .models), captured alongside the availability flags. null/empty = agent
    // too old or enumeration unavailable → dropdowns fall back to the hardcoded
    // companion constants.
    val codexModels: List<String>? = null,
    val antigravityModels: List<String>? = null,
    // Per-model Codex reasoning-effort sets (capabilities.cli.codex
    // .model_details). null = agent too old or catalog carried no levels →
    // the effort control falls back to REASONING_EFFORTS.
    val codexModelDetails: List<CliModelDetail>? = null,
    val noMemory: Boolean = false,
    val skipPermissions: Boolean = false,
    // Claude reasoning controls (replace the old boolean "Max effort"). model is
    // a curated alias or null (account default); effort is always sent explicitly.
    val claudeModel: String? = null,
    val claudeEffort: String = "high",
    val claudeFastMode: Boolean = false,
    val claudeUltracode: Boolean = false,
    val claudeDebug: Boolean = false,
    val focusMode: Boolean = true,
    val claudeAppendSystemPrompt: String = "",
    // Auto Scan's own copies of the Claude controls: each kind remembers its
    // own persisted defaults, and in-flight edits must not bleed between the
    // Standard and Auto Scan config steps within one wizard run.
    // skipPermissions defaults ON here (scans usually run unattended); turning
    // it off makes the scan pause at permission prompts and raise attention
    // notifications instead.
    val autoScanPlanMode: Boolean = false,
    val autoScanNoMemory: Boolean = false,
    val autoScanSkipPermissions: Boolean = true,
    val autoScanClaudeModel: String? = null,
    val autoScanClaudeEffort: String = "high",
    val autoScanClaudeFastMode: Boolean = false,
    val autoScanClaudeUltracode: Boolean = false,
    val autoScanClaudeDebug: Boolean = false,
    val autoScanFocusMode: Boolean = true,
    val autoScanClaudeAppendSystemPrompt: String = "",
    // Codex launch options (CodexConfig step). Defaults reflect the "Auto" preset.
    val codexModel: String? = null,
    val codexSandbox: String = "workspace-write",
    val codexApproval: String = "on-request",
    val codexFullAuto: Boolean = false,
    val codexReasoningEffort: String = "high",
    // Antigravity launch options (AntigravityConfig step). The two booleans are
    // mutually exclusive (server rejects the pair with a 422); the setters
    // enforce it. addDirsDraft is the raw text of the additional-directories
    // field, parsed into add_dirs at submit.
    val antigravityModel: String? = null,
    val antigravitySkipPermissions: Boolean = false,
    val antigravitySandbox: Boolean = false,
    val antigravityAddDirsDraft: String = "",
    val initialPrompt: String = "",
    val worktree: Boolean = false,
    val branchName: String = "",
    val submitting: Boolean = false,
    val autoScanStarting: Boolean = false,
    // Name of the stored project the AutoScan config step was last seeded for
    // (null = not yet seeded). Tracked per-project so switching projects mid-
    // wizard re-seeds the prompt, while re-entering the same project preserves
    // the user's in-flight edits.
    val autoScanConfigInitializedFor: String? = null,
    val dismiss: Boolean = false,
    val error: String? = null,
    // Codex login dialog. All fields are memory-only — the API key is
    // never persisted to disk on the device.
    val codexLoginVisible: Boolean = false,
    val codexApiKeyDraft: String = "",
    val codexApiKeyVisible: Boolean = false,
    val codexLoginSubmitting: Boolean = false,
    val codexLoginError: String? = null,
    // Account-scoped custom agent types rendered as extra mode-picker cards.
    // The repository cache is the source of truth; UserEventBus keeps it in
    // sync across devices via custom_type.* events.
    val customTypes: List<CustomAgentTypeDto> = emptyList(),
    val typeEditor: TypeEditorState = TypeEditorState.Hidden,
    val typeEditorError: String? = null,
    val typeEditorSaving: Boolean = false,
    val deleteTypeTarget: CustomAgentTypeDto? = null,
    val deleteTypeInFlight: Boolean = false,
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

/**
 * Effort levels offered for a Codex [model]: the agent-advertised per-model set
 * when `model_details` knows the model, else the static
 * [CreateSessionViewModel.REASONING_EFFORTS] fallback. The Default (null) model
 * always takes the fallback — which model the agent resolves it to is unknown
 * here — mirroring [CreateSessionViewModel.claudeEffortsFor]'s null posture.
 */
fun CreateSessionUiState.codexEffortsFor(model: String?): List<String> =
    model?.let { m -> codexModelDetails?.firstOrNull { it.id == m }?.efforts }
        ?: CreateSessionViewModel.REASONING_EFFORTS

/** Snapshot of the Claude reasoning controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toClaudeLaunchPrefs() = ClaudeLaunchPrefs(
    model = claudeModel,
    effort = claudeEffort,
    fastMode = claudeFastMode,
    ultracode = claudeUltracode,
    appendSystemPrompt = claudeAppendSystemPrompt,
    skipPermissions = skipPermissions,
    noMemory = noMemory,
    debug = claudeDebug,
    focusMode = focusMode,
)

/** Snapshot of the Auto Scan launch controls for its own per-kind bucket. */
private fun CreateSessionUiState.toAutoScanLaunchPrefs() = ClaudeLaunchPrefs(
    model = autoScanClaudeModel,
    effort = autoScanClaudeEffort,
    fastMode = autoScanClaudeFastMode,
    ultracode = autoScanClaudeUltracode,
    appendSystemPrompt = autoScanClaudeAppendSystemPrompt,
    skipPermissions = autoScanSkipPermissions,
    noMemory = autoScanNoMemory,
    planMode = autoScanPlanMode,
    debug = autoScanClaudeDebug,
    focusMode = autoScanFocusMode,
)

/** Snapshot of the Codex launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toCodexLaunchPrefs() = CodexLaunchPrefs(
    model = codexModel,
    sandbox = codexSandbox,
    approval = codexApproval,
    fullAuto = codexFullAuto,
    reasoningEffort = codexReasoningEffort,
)

/** Snapshot of the Antigravity launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toAntigravityLaunchPrefs() = AntigravityLaunchPrefs(
    model = antigravityModel,
    skipPermissions = antigravitySkipPermissions,
    sandbox = antigravitySandbox,
)

/**
 * Mode-picker gate. Only an explicit `false` from the agent disables a card;
 * `null` (unknown / not-yet-probed / probe failed) stays optimistically enabled
 * so a transient probe failure never blocks a CLI that's actually installed.
 */
val CreateSessionUiState.claudeEnabled: Boolean get() = claudeAvailable != false
val CreateSessionUiState.codexEnabled: Boolean get() = codexAvailable != false
val CreateSessionUiState.antigravityEnabled: Boolean get() = antigravityAvailable != false

/**
 * Auto Scan additionally needs a Linear API key on the selected stored project
 * — the agent scans Linear for that project's issues. A custom path or no
 * selection can never satisfy it.
 */
val CreateSessionUiState.autoScanEnabled: Boolean
    get() = claudeEnabled &&
        projects.firstOrNull { it.name == selectedProjectName }?.has_linear_key == true

/**
 * Whether tapping this mode-picker card can commit. Terminal and custom agent
 * types launch through the login shell, so no CLI probe gates them; a
 * stale/deleted type surfaces via the create-time 404.
 */
fun CreateSessionUiState.isModeEnabled(mode: CreateSessionMode): Boolean = when (mode) {
    CreateSessionMode.Standard -> claudeEnabled
    CreateSessionMode.AutoScan -> autoScanEnabled
    CreateSessionMode.Codex -> codexEnabled
    CreateSessionMode.Antigravity -> antigravityEnabled
    CreateSessionMode.CustomShell -> true
    is CreateSessionMode.Custom -> true
}

@HiltViewModel
class CreateSessionViewModel @Inject constructor(
    private val projectsRepository: ProjectsRepository,
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
    private val tokenStore: TokenStore,
    private val customAgentTypesRepository: CustomAgentTypesRepository,
    pathSuggestionRepository: PathSuggestionRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(CreateSessionUiState())
    val uiState: StateFlow<CreateSessionUiState> = _uiState.asStateFlow()

    private val customPathSuggestions = PathSuggestionsController(pathSuggestionRepository, viewModelScope)
    val customPathSuggestionState: StateFlow<PathSuggestionsUi> = customPathSuggestions.state

    private val _navigateToSession = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val navigateToSession: SharedFlow<String> = _navigateToSession.asSharedFlow()

    private val _navigateToAutoScan = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val navigateToAutoScan: SharedFlow<String> = _navigateToAutoScan.asSharedFlow()

    private val _transientMessages = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val transientMessages: SharedFlow<String> = _transientMessages.asSharedFlow()

    init {
        viewModelScope.launch {
            // Pre-fill the Claude reasoning controls from the last successful
            // launch (mirrors the last-used-project pre-fill below).
            preferencesStore.getLastUsedClaudeOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        claudeModel = saved.model,
                        claudeEffort = saved.effort,
                        claudeFastMode = saved.fastMode,
                        claudeUltracode = saved.ultracode,
                        claudeAppendSystemPrompt = saved.appendSystemPrompt,
                        skipPermissions = saved.skipPermissions,
                        noMemory = saved.noMemory,
                        claudeDebug = saved.debug,
                        focusMode = saved.focusMode,
                    )
                }
            }
            // Auto Scan has its own bucket so scan choices never pre-fill the
            // Claude Code sheet (or vice versa). With no saved blob the state
            // defaults apply — notably skipPermissions ON for unattended scans.
            preferencesStore.getLastUsedAutoScanOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        autoScanClaudeModel = saved.model,
                        autoScanClaudeEffort = saved.effort,
                        autoScanClaudeFastMode = saved.fastMode,
                        autoScanClaudeUltracode = saved.ultracode,
                        autoScanClaudeAppendSystemPrompt = saved.appendSystemPrompt,
                        autoScanSkipPermissions = saved.skipPermissions,
                        autoScanNoMemory = saved.noMemory,
                        autoScanPlanMode = saved.planMode,
                        autoScanClaudeDebug = saved.debug,
                        autoScanFocusMode = saved.focusMode,
                    )
                }
            }
            // Same pre-fill for the Codex launch controls (sandbox/approval/
            // full-auto restore the autonomy preset highlight via codexPreset).
            preferencesStore.getLastUsedCodexOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        codexModel = saved.model,
                        codexSandbox = saved.sandbox,
                        codexApproval = saved.approval,
                        codexFullAuto = saved.fullAuto,
                        // The DataStore read races the ModePick capability
                        // probe: when the probe already resolved, its
                        // reconcile ran before this restore landed, so the
                        // restored effort must pass through the same rule or
                        // a stale catalog effort would stomp the clamp.
                        codexReasoningEffort = if (cliProbeResolved) {
                            reconciledCodexEffort(
                                model = saved.model,
                                effort = saved.reasoningEffort,
                                details = it.codexModelDetails,
                            )
                        } else {
                            saved.reasoningEffort
                        },
                    )
                }
            }
            // Same pre-fill for the Antigravity launch controls.
            preferencesStore.getLastUsedAntigravityOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        antigravityModel = saved.model,
                        antigravitySkipPermissions = saved.skipPermissions,
                        antigravitySandbox = saved.sandbox && !saved.skipPermissions,
                    )
                }
            }
        }

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
            customAgentTypesRepository.observeTypes().collect { types ->
                _uiState.update { state ->
                    // A type deleted on another device while the picker is open
                    // must not stay selected — its card is gone and a launch
                    // would 404.
                    val selected = state.selectedMode
                    val stillExists = selected !is CreateSessionMode.Custom ||
                        types.any { it.id == selected.typeId }
                    state.copy(
                        customTypes = types,
                        selectedMode = if (stillExists) selected else null,
                    )
                }
            }
        }

        viewModelScope.launch {
            // Best-effort cold-start seed: the connect-time reconcile in
            // UserEventBus owns steady-state freshness, but this screen can
            // mount before the user-events socket connects.
            try {
                customAgentTypesRepository.refreshTypes()
            } catch (_: Exception) {
                // The picker just shows no custom cards until a refresh lands.
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
        customPathSuggestions.onTextChange("")
        customPathSuggestions.probe()
        val scope = agentScopeKey() ?: return
        viewModelScope.launch {
            // Guarded like removeRecentCustomPath below: a DataStore fault on the
            // entry path must leave the chip row empty, not kill the wizard.
            runCatching {
                val recents = preferencesStore.getRecentCustomPaths(scope)
                _uiState.update { it.copy(recentCustomPaths = recents) }
            }
        }
    }

    fun setCustomPathDraft(value: String) = _uiState.update {
        customPathSuggestions.onTextChange(value)
        val error = validateCustomPath(value)
        it.copy(
            customPathDraft = value,
            customPathError = error,
            // The draft stays as typed so the field never fights the user
            // mid-keystroke; only the committed selection — the value that
            // becomes the request cwd and the stored recent — is trimmed (iOS
            // CreateSessionModel.setCustomPath does the same), and loses the
            // trailing separator a picked folder suggestion leaves behind.
            selection = if (error == null && value.isNotBlank()) {
                ProjectSelection.CustomPath(PathCompletion.trimTrailingSeparator(value.trim()))
            } else {
                ProjectSelection.CustomPath("")
            },
        )
    }

    fun pickRecentCustomPath(path: String) = setCustomPathDraft(path)

    fun onCustomPathFocusChange(focused: Boolean) = customPathSuggestions.onFocusChange(focused)

    fun pickCustomPathSuggestion(name: String) = setCustomPathDraft(customPathSuggestions.accept(name))

    /**
     * Forgets one recent path for the current agent. The store read afterwards
     * is authoritative, so the chip row cannot drift from what a later
     * [selectCustomPath] would load.
     */
    fun removeRecentCustomPath(path: String) {
        val scope = agentScopeKey() ?: return
        viewModelScope.launch {
            // Recents persistence is a UX nicety (TerminalViewModel.sendChord
            // makes the same call): a DataStore fault must not take the wizard
            // down mid-flow, and leaving the chip visible stays truthful — the
            // store still holds it.
            runCatching {
                preferencesStore.removeRecentCustomPath(scope, path)
                val recents = preferencesStore.getRecentCustomPaths(scope)
                _uiState.update { it.copy(recentCustomPaths = recents) }
            }
        }
    }

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

    /**
     * Re-probe the agent for which managed CLIs are installed and update the
     * mode-picker gate. Called each time the user lands on the ModePick step
     * (see `CreateSessionScreen`), so the buttons reflect the agent's current
     * state rather than a value cached at connect time. Best-effort: a failure
     * leaves the last-known/optimistic flags untouched and never raises a
     * user-facing error (this is a passive probe, not a user action) — a missing
     * CLI that slips through still falls back to the create-time 503 handling.
     */
    // Whether a capability probe has resolved this wizard session. Lets the
    // async prefs restore tell "details unknown, probe still pending" (keep
    // the saved effort — refresh reconciles later) apart from "probe resolved
    // with no details" (reconcile now, or the restore stomps the clamp). A
    // models_pending answer deliberately does NOT count as resolved. Both
    // writers run on viewModelScope's main dispatcher, so no synchronization.
    private var cliProbeResolved = false

    // Remaining models_pending re-probes. A budget rather than a one-shot: a
    // legitimately slow warmup (each agent-side probe attempt may take 10s,
    // and the old-CLI path takes two) can outlive several client probes, and
    // a terminal pending answer would strand the visit on fallback lists with
    // no reconciliation. Refilled by any non-pending answer; exhausted, it
    // stops — a truly wedged warmup must not become an endless poll loop.
    private var warmupRetriesLeft = WARMUP_MAX_RETRIES

    /**
     * The reasoning effort actually submittable to the probed agent. No
     * `model_details` advertised → possibly a pre-catalog agent whose wire
     * schema still 422s efforts outside [REASONING_EFFORTS], so anything
     * outside it drops to the default. Details advertised and the selected
     * model's entry omits the effort → the catalog says the model cannot
     * serve it, so the same re-default [setCodexModel] applies on a switch
     * runs here. A model without a catalog entry (or the null Default model)
     * keeps the effort — its capabilities are unknown, never downshift on a
     * guess.
     */
    private fun reconciledCodexEffort(
        model: String?,
        effort: String,
        details: List<CliModelDetail>?,
    ): String {
        if (details == null) {
            return if (effort in REASONING_EFFORTS) effort else "high"
        }
        val detail = model?.let { m -> details.firstOrNull { it.id == m } } ?: return effort
        if (effort in detail.efforts) return effort
        return detail.defaultEffort
            ?: "high".takeIf { it in detail.efforts }
            ?: detail.efforts.first()
    }

    fun refreshCliAvailability() {
        // In-flight dedup: set the flag before launch so the guard holds even
        // when the coroutine body is queued (StandardTestDispatcher) — same
        // discipline as TerminalViewModel.refreshTemplates().
        if (_uiState.value.cliAvailabilityLoading) return
        _uiState.update { it.copy(cliAvailabilityLoading = true) }
        viewModelScope.launch {
            try {
                val version = sessionRepository.getAgentVersion()
                val codexDetails = version.cliModelDetails("codex")
                // models_pending: the agent's startup enumeration was still
                // warming when it answered, so absent details are provisional
                // — the probe does not count as resolved (no reconcile, and a
                // late prefs restore stays untouched too) and one delayed
                // re-probe fetches the warmed answer. The marker itself
                // proves a catalog-era agent, so keeping a persisted catalog
                // effort meanwhile is safe, not just optimistic.
                val pending = version.cliModelsPending("codex")
                if (!pending) cliProbeResolved = true
                _uiState.update {
                    it.copy(
                        claudeAvailable = version.cliAvailable("claude"),
                        codexAvailable = version.cliAvailable("codex"),
                        antigravityAvailable = version.cliAvailable("antigravity"),
                        codexModels = version.cliModels("codex"),
                        antigravityModels = version.cliModels("antigravity"),
                        codexModelDetails = codexDetails,
                        codexReasoningEffort = if (pending) {
                            it.codexReasoningEffort
                        } else {
                            reconciledCodexEffort(
                                model = it.codexModel,
                                effort = it.codexReasoningEffort,
                                details = codexDetails,
                            )
                        },
                        cliAvailabilityLoading = false,
                    )
                }
                if (!pending) {
                    warmupRetriesLeft = WARMUP_MAX_RETRIES
                } else if (warmupRetriesLeft > 0) {
                    warmupRetriesLeft -= 1
                    viewModelScope.launch {
                        delay(WARMUP_RETRY_DELAY_MS)
                        refreshCliAvailability()
                    }
                }
            } catch (_: Exception) {
                _uiState.update { it.copy(cliAvailabilityLoading = false) }
            }
        }
    }

    fun pickMode(mode: CreateSessionMode) {
        _uiState.update { it.copy(selectedMode = mode) }
    }

    /**
     * Commit a mode-picker card tap. The picker has no confirm button — one tap
     * both records the choice and advances, so this records the selection and
     * then runs that mode's commit step. Terminal and custom agent types on a
     * non-git target launch straight away (see [selectTerminalMode]).
     *
     * The [CreateSessionUiState.submitting] guard blocks a second tap on a
     * *different* card from advancing the wizard while such a launch is in
     * flight — [submitPlainTerminal]'s own guard only stops a double submit.
     */
    fun chooseMode(mode: CreateSessionMode) {
        val state = _uiState.value
        if (state.submitting || !state.isModeEnabled(mode)) return
        pickMode(mode)
        when (mode) {
            CreateSessionMode.Standard -> selectStandardMode()
            CreateSessionMode.Codex -> selectCodexMode()
            CreateSessionMode.Antigravity -> selectAntigravityMode()
            CreateSessionMode.AutoScan -> selectAutoScanMode()
            CreateSessionMode.CustomShell -> selectTerminalMode()
            is CreateSessionMode.Custom -> selectCustomAgentMode(mode.typeId)
        }
    }

    fun selectStandardMode() = _uiState.update {
        if (it.claudeEnabled) it.copy(step = CreateSessionStep.StandardConfig) else it
    }

    fun selectCodexMode() = _uiState.update {
        if (it.codexEnabled) it.copy(step = CreateSessionStep.CodexConfig) else it
    }

    fun selectAntigravityMode() = _uiState.update {
        if (it.antigravityEnabled) it.copy(step = CreateSessionStep.AntigravityConfig) else it
    }

    /**
     * Commit the mode picker's Terminal card. A stored git project
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

    /**
     * Commit the mode picker's card for a custom agent type. Identical flow to
     * [selectTerminalMode] — custom types are plain-terminal sessions, so a
     * stored git project opens the Terminal config page (worktree toggle) and
     * anything else launches immediately.
     */
    fun selectCustomAgentMode(typeId: String) {
        val state = _uiState.value
        val storedGit = (state.selection as? ProjectSelection.Stored)?.let { sel ->
            state.projects.firstOrNull { it.name == sel.name }?.is_git == true
        } ?: false
        if (storedGit) {
            _uiState.update { it.copy(step = CreateSessionStep.TerminalConfig) }
        } else {
            submitCustomAgent(typeId)
        }
    }

    /**
     * Submit from the TerminalConfig step, which is shared between plain
     * Terminal mode and custom agent types — dispatch on the selected mode.
     */
    fun submitFromTerminalConfig() {
        when (val mode = _uiState.value.selectedMode) {
            is CreateSessionMode.Custom -> submitCustomAgent(mode.typeId)
            else -> submitCustomShell()
        }
    }

    fun selectAutoScanMode() = _uiState.update {
        if (!it.claudeEnabled) return@update it
        val projectName = it.selectedProjectName
        if (projectName != null && it.autoScanConfigInitializedFor == projectName) {
            // Already seeded for this project — keep the user's in-flight edits.
            it.copy(step = CreateSessionStep.AutoScanConfig)
        } else {
            // First entry for this project, or the selected project changed since
            // the last entry: (re-)seed from this project's last-saved Auto Scan
            // prompt (persisted server-side on the previous scan start, delivered
            // via ProjectInfo); fall back to the agent-wide default, then empty.
            val savedPrompt = projectName
                ?.let { name -> it.projects.firstOrNull { p -> p.name == name } }
                ?.auto_scan_prompt
            it.copy(
                step = CreateSessionStep.AutoScanConfig,
                autoScanConfigInitializedFor = projectName,
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
                    plan_mode = state.autoScanPlanMode,
                    no_memory = state.autoScanNoMemory,
                    skip_permissions = state.autoScanSkipPermissions,
                    model = state.autoScanClaudeModel,
                    effort = state.autoScanClaudeEffort,
                    fast_mode = state.autoScanClaudeFastMode,
                    ultracode = state.autoScanClaudeUltracode,
                    debug = state.autoScanClaudeDebug,
                    focus_mode = state.autoScanFocusMode,
                    append_system_prompt = state.autoScanClaudeAppendSystemPrompt.takeIf { it.isNotBlank() },
                    worktree = true,
                    initial_prompt = state.initialPrompt.takeIf { it.isNotBlank() },
                )
                autoScanRepository.startScan(projectName, options)
                preferencesStore.setLastUsedProjectName(projectName)
                preferencesStore.setLastUsedAutoScanOptions(state.toAutoScanLaunchPrefs())
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
            CreateSessionStep.AntigravityConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.AutoScanConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.TerminalConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.ModePick -> it.copy(
                step = CreateSessionStep.ProjectSelect,
                selectedMode = null,
            )
            CreateSessionStep.ProjectSelect -> it
        }
    }

    fun toggleNoMemory(v: Boolean) = _uiState.update { it.copy(noMemory = v) }
    fun toggleSkipPermissions(v: Boolean) = _uiState.update { it.copy(skipPermissions = v) }
    fun toggleFocusMode(v: Boolean) = _uiState.update { it.copy(focusMode = v) }
    fun setClaudeModel(v: String?) = _uiState.update {
        // Clamp the effort if the new model doesn't support the current level
        // (e.g. xhigh/max → high when switching to haiku, the only capped model).
        val efforts = claudeEffortsFor(v)
        it.copy(claudeModel = v, claudeEffort = if (it.claudeEffort in efforts) it.claudeEffort else "high")
    }

    fun setClaudeEffort(v: String) = _uiState.update { it.copy(claudeEffort = v) }

    fun toggleClaudeFastMode(v: Boolean) = _uiState.update {
        // Fast mode is Opus-only and auto-switches the session to Opus, so lock
        // the model to opus while it's on (the UI disables the model dropdown).
        if (v) it.copy(claudeFastMode = true, claudeModel = "opus") else it.copy(claudeFastMode = false)
    }

    fun toggleClaudeUltracode(v: Boolean) = _uiState.update { it.copy(claudeUltracode = v) }

    fun toggleClaudeDebug(v: Boolean) = _uiState.update { it.copy(claudeDebug = v) }

    fun setClaudeAppendSystemPrompt(v: String) = _uiState.update { it.copy(claudeAppendSystemPrompt = v) }

    // Auto Scan copies of the Claude control setters, including the same
    // coupling rules (effort clamp, fast-mode Opus lock).
    fun toggleAutoScanPlanMode(v: Boolean) = _uiState.update { it.copy(autoScanPlanMode = v) }
    fun toggleAutoScanNoMemory(v: Boolean) = _uiState.update { it.copy(autoScanNoMemory = v) }
    fun toggleAutoScanSkipPermissions(v: Boolean) =
        _uiState.update { it.copy(autoScanSkipPermissions = v) }
    fun toggleAutoScanFocusMode(v: Boolean) =
        _uiState.update { it.copy(autoScanFocusMode = v) }

    fun setAutoScanClaudeModel(v: String?) = _uiState.update {
        val efforts = claudeEffortsFor(v)
        it.copy(
            autoScanClaudeModel = v,
            autoScanClaudeEffort = if (it.autoScanClaudeEffort in efforts) it.autoScanClaudeEffort else "high",
        )
    }

    fun setAutoScanClaudeEffort(v: String) = _uiState.update { it.copy(autoScanClaudeEffort = v) }

    fun toggleAutoScanClaudeFastMode(v: Boolean) = _uiState.update {
        if (v) it.copy(autoScanClaudeFastMode = true, autoScanClaudeModel = "opus")
        else it.copy(autoScanClaudeFastMode = false)
    }

    fun toggleAutoScanClaudeUltracode(v: Boolean) =
        _uiState.update { it.copy(autoScanClaudeUltracode = v) }

    fun toggleAutoScanClaudeDebug(v: Boolean) =
        _uiState.update { it.copy(autoScanClaudeDebug = v) }

    fun setAutoScanClaudeAppendSystemPrompt(v: String) =
        _uiState.update { it.copy(autoScanClaudeAppendSystemPrompt = v) }

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

    fun setCodexModel(v: String?) = _uiState.update { state ->
        // Same clamp as setClaudeModel, with the target upgraded from a
        // hardcoded "high" to the catalog's per-model default. A model without
        // a catalog entry (or the null Default model) keeps the effort — its
        // capabilities are unknown, so never downshift on a guess.
        val detail = v?.let { m -> state.codexModelDetails?.firstOrNull { it.id == m } }
        val effort = when {
            detail == null || state.codexReasoningEffort in detail.efforts ->
                state.codexReasoningEffort
            else -> detail.defaultEffort
                ?: "high".takeIf { it in detail.efforts }
                ?: detail.efforts.first()
        }
        state.copy(codexModel = v, codexReasoningEffort = effort)
    }
    fun setCodexReasoningEffort(v: String) = _uiState.update { it.copy(codexReasoningEffort = v) }
    fun setCodexSandbox(v: String) = _uiState.update { it.copy(codexSandbox = v) }
    fun setCodexApproval(v: String) = _uiState.update { it.copy(codexApproval = v) }

    fun setAntigravityModel(v: String?) = _uiState.update { it.copy(antigravityModel = v) }

    fun setAntigravityAddDirsDraft(v: String) =
        _uiState.update { it.copy(antigravityAddDirsDraft = v) }

    /**
     * Skip-permissions and Sandbox are mutually exclusive (the pair enables a
     * sandbox escape; the server rejects it with a 422), so enabling either
     * force-disables the other — same state-mutating-toggle precedent as
     * [toggleClaudeFastMode].
     */
    fun setAntigravitySkipPermissions(v: Boolean) = _uiState.update {
        if (v) it.copy(antigravitySkipPermissions = true, antigravitySandbox = false)
        else it.copy(antigravitySkipPermissions = false)
    }

    fun setAntigravitySandbox(v: Boolean) = _uiState.update {
        if (v) it.copy(antigravitySandbox = true, antigravitySkipPermissions = false)
        else it.copy(antigravitySandbox = false)
    }

    fun consumeDismiss() = _uiState.update { it.copy(dismiss = false) }

    fun submit() {
        val state = _uiState.value
        if (state.submitting) return
        val claudeOptions = ClaudeOptionsModel(
            no_memory = state.noMemory,
            skip_permissions = state.skipPermissions,
            model = state.claudeModel,
            effort = state.claudeEffort,
            fast_mode = state.claudeFastMode,
            ultracode = state.claudeUltracode,
            debug = state.claudeDebug,
            focus_mode = state.focusMode,
            append_system_prompt = state.claudeAppendSystemPrompt.takeIf { it.isNotBlank() },
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

    fun submitAntigravity() {
        val state = _uiState.value
        if (state.submitting) return
        val antigravityOptions = AntigravityOptionsModel(
            model = state.antigravityModel?.takeIf { it.isNotBlank() },
            skip_permissions = state.antigravitySkipPermissions,
            sandbox = state.antigravitySandbox,
            add_dirs = parseAddDirs(state.antigravityAddDirsDraft),
            worktree = state.worktree,
            branch_name = state.branchName.takeIf { it.isNotBlank() },
        )
        when (val sel = state.selection) {
            is ProjectSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    project_name = sel.name,
                    cli = "antigravity",
                    antigravity_options = antigravityOptions,
                ),
            )
            is ProjectSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        project_name = null,
                        cwd = sel.path,
                        cli = "antigravity",
                        antigravity_options = antigravityOptions.copy(worktree = false, branch_name = null),
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

    fun submitCustomShell() = submitPlainTerminal(cli = null)

    /**
     * Launch a session of an account-scoped custom agent type — the same
     * plain-terminal semantics as [submitCustomShell] including the worktree
     * toggle, except the request carries `cli = "custom_<id>"`. Only the
     * type id goes on the wire; the relay resolves the stored command
     * server-side.
     */
    private fun submitCustomAgent(typeId: String) =
        submitPlainTerminal(cli = "custom_$typeId")

    private fun submitPlainTerminal(cli: String?) {
        val state = _uiState.value
        if (state.submitting) return
        when (val sel = state.selection) {
            is ProjectSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    project_name = sel.name,
                    command = null,
                    cli = cli,
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
                        cli = cli,
                        claude_options = null,
                        worktree = false,
                        branch_name = null,
                    ),
                )
            }
            ProjectSelection.None -> Unit
        }
    }

    fun openTypeEditor(target: CustomAgentTypeDto?) = _uiState.update {
        it.copy(
            typeEditor = TypeEditorState.Open(target),
            typeEditorError = null,
            typeEditorSaving = false,
        )
    }

    fun dismissTypeEditor() = _uiState.update {
        it.copy(
            typeEditor = TypeEditorState.Hidden,
            typeEditorError = null,
            typeEditorSaving = false,
        )
    }

    /**
     * Create or update (depending on the open editor's target) a custom agent
     * type. Values are trimmed and validated client-side first; the sheet is
     * dismissed only on success, and failures surface inline in the sheet —
     * the server's 409 `detail` strings (20-type cap, name conflict) are
     * user-grade copy and are shown verbatim.
     */
    fun saveCustomAgentType(name: String, command: String) {
        val editor = _uiState.value.typeEditor as? TypeEditorState.Open ?: return
        if (_uiState.value.typeEditorSaving) return
        val trimmedName = name.trim()
        val trimmedCommand = command.trim()
        // Lengths are counted in code points to match the server's limits
        // (Pydantic max_length counts characters) — UTF-16 String.length
        // would falsely reject supplementary-plane input (emoji, rare CJK)
        // the server accepts.
        val validationError = when {
            trimmedName.isEmpty() -> "Name is required"
            trimmedName.codePointCount(0, trimmedName.length) > MAX_TYPE_NAME_LENGTH ->
                "Name must be at most $MAX_TYPE_NAME_LENGTH characters"
            trimmedCommand.isEmpty() -> "Command is required"
            trimmedCommand.codePointCount(0, trimmedCommand.length) > MAX_TYPE_COMMAND_LENGTH ->
                "Command must be at most $MAX_TYPE_COMMAND_LENGTH characters"
            else -> null
        }
        if (validationError != null) {
            _uiState.update { it.copy(typeEditorError = validationError) }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(typeEditorSaving = true, typeEditorError = null) }
            try {
                val target = editor.target
                if (target == null) {
                    customAgentTypesRepository.createType(trimmedName, trimmedCommand)
                } else {
                    // Both fields are always sent: the server treats an
                    // unchanged value as fine, while `{}` is a 422 — this
                    // avoids dirty-tracking without hitting that edge.
                    customAgentTypesRepository.updateType(target.id, trimmedName, trimmedCommand)
                }
                // Completion is scoped to the editor this save started from:
                // if the sheet was dismissed and reopened for another target
                // mid-flight, only release the saving flag — never close the
                // new editor. (The sheet also blocks dismissal while saving;
                // this is the defense-in-depth half of that fix.)
                _uiState.update {
                    if (it.typeEditor == editor) {
                        it.copy(typeEditor = TypeEditorState.Hidden, typeEditorSaving = false)
                    } else {
                        it.copy(typeEditorSaving = false)
                    }
                }
            } catch (e: Exception) {
                val message = when {
                    e is ApiException ->
                        extractDetail(e.errorBody) ?: "Couldn't save (HTTP ${e.code})"
                    else -> e.message ?: "Couldn't save the agent type"
                }
                // Same operation scoping: a stale save's error must not be
                // written into an editor it did not come from.
                _uiState.update {
                    if (it.typeEditor == editor) {
                        it.copy(typeEditorSaving = false, typeEditorError = message)
                    } else {
                        it.copy(typeEditorSaving = false)
                    }
                }
            }
        }
    }

    fun requestDeleteType(target: CustomAgentTypeDto) = _uiState.update {
        it.copy(deleteTypeTarget = target)
    }

    fun dismissDeleteType() = _uiState.update { it.copy(deleteTypeTarget = null) }

    /**
     * Pessimistic delete (destructive: the server cascades the type's saved
     * shortcuts). A 403 means the type is already gone elsewhere — treat it
     * as success and reconcile via refresh.
     */
    fun confirmDeleteType() {
        val target = _uiState.value.deleteTypeTarget ?: return
        if (_uiState.value.deleteTypeInFlight) return
        viewModelScope.launch {
            _uiState.update { it.copy(deleteTypeInFlight = true) }
            try {
                customAgentTypesRepository.deleteType(target.id)
                _uiState.update { it.copy(deleteTypeTarget = null, deleteTypeInFlight = false) }
            } catch (e: Exception) {
                if (e is ApiException && e.code == 403) {
                    _uiState.update { it.copy(deleteTypeTarget = null, deleteTypeInFlight = false) }
                    try {
                        customAgentTypesRepository.refreshTypes()
                    } catch (_: Exception) {
                        // The custom_type.deleted event already removed it for
                        // the common case; a failed reconcile self-heals on the
                        // next connect.
                    }
                } else {
                    _uiState.update {
                        it.copy(
                            deleteTypeInFlight = false,
                            error = "Failed to delete agent type: ${e.message}",
                        )
                    }
                }
            }
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
                // Remember the per-CLI launch controls for the next session of
                // the same kind. The request shape selects which blob to write:
                // Claude submits carry claude_options, Codex submits carry
                // codex_options, Antigravity submits carry antigravity_options,
                // shell submits carry none.
                if (request.claude_options != null) {
                    preferencesStore.setLastUsedClaudeOptions(_uiState.value.toClaudeLaunchPrefs())
                }
                if (request.codex_options != null) {
                    preferencesStore.setLastUsedCodexOptions(_uiState.value.toCodexLaunchPrefs())
                }
                if (request.antigravity_options != null) {
                    preferencesStore.setLastUsedAntigravityOptions(_uiState.value.toAntigravityLaunchPrefs())
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
                } else if (e is ApiException && isAgentUpdateRequired(e.errorBody)) {
                    // Relay-side gate: the agent's live tunnel negotiated a
                    // protocol too old to launch custom agent types.
                    _uiState.update {
                        it.copy(
                            submitting = false,
                            error = "Update your agent to launch custom agent types",
                        )
                    }
                } else if (
                    e is ApiException && e.code == 404 &&
                    request.cli?.startsWith("custom_") == true &&
                    extractDetail(e.errorBody) == "Custom agent type not found"
                ) {
                    // The type was deleted between rendering the card and the
                    // launch — refresh so the stale card disappears. Keyed on
                    // the relay's exact detail string, not the status alone: a
                    // custom launch can also 404 for a concurrently-deleted
                    // project (agent-side "Project 'X' not found"), which must
                    // fall through to the generic message below. If the relay
                    // string ever changes this degrades to the generic path.
                    _uiState.update {
                        it.copy(submitting = false, error = "This agent type was deleted")
                    }
                    try {
                        customAgentTypesRepository.refreshTypes()
                    } catch (_: Exception) {
                        // Reconciled on the next user-events connect instead.
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

    /**
     * True when an error body carries the `agent_update_required` marker the
     * relay sends (409, top-level) for custom-type launches against an agent
     * whose tunnel negotiated protocol < 7.
     */
    private fun isAgentUpdateRequired(body: String): Boolean = runCatching {
        Json.parseToJsonElement(body)
            .jsonObject["agent_update_required"]?.jsonPrimitive?.booleanOrNull == true
    }.getOrDefault(false)

    /** Splits the additional-directories draft on newlines/commas into a clean list. */
    private fun parseAddDirs(draft: String): List<String> =
        draft.split('\n', ',').map { it.trim() }.filter { it.isNotEmpty() }

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
        // Judge the same string that gets launched, not the raw draft: the
        // selection is trimmed, so validating the untrimmed text would reject
        // " /tmp/x" as non-absolute while iOS accepts it (CreateSessionModel
        // .validateCustomPath trims first too).
        val trimmed = input.trim()
        if (trimmed.isEmpty()) return "Path is required"
        return try {
            if (Paths.get(trimmed).isAbsolute || isWindowsAbsolute(trimmed)) null
            else "Path must be absolute"
        } catch (_: InvalidPathException) {
            "Path is invalid"
        }
    }

    private fun isWindowsAbsolute(input: String): Boolean =
        WINDOWS_ABS_REGEX.containsMatchIn(input) || input.startsWith("\\\\")

    companion object {
        private val WINDOWS_ABS_REGEX = Regex("^[A-Za-z]:[\\\\/]")

        // Server-enforced limits on custom agent types (post-trim); validated
        // client-side too so the editor errors without a round trip.
        const val MAX_TYPE_NAME_LENGTH = 60
        const val MAX_TYPE_COMMAND_LENGTH = 1000

        // Delayed re-probes after a models_pending answer: the interval is
        // long enough for the agent's warm task to finish its typical probe,
        // short enough that the wizard usually has the warmed catalog before
        // the user reaches the Codex config step. The budget covers the
        // legitimate worst-case warmup (~30s: two 10s codex attempts + a 10s
        // agy probe; each retry cycle spends the delay plus the agent's ~3s
        // in-handler wait) with headroom, then stops.
        const val WARMUP_RETRY_DELAY_MS = 2_500L
        const val WARMUP_MAX_RETRIES = 8

        /**
         * Fallback Codex model quick-picks for the wizard dropdown, used when the
         * agent's dynamic `capabilities.cli.codex.models` list is absent/empty
         * (older agent). Matches the agent's own enumeration fallback. The
         * server's `model` field is free-form, so "Default" (a null model,
         * Codex's account default) is always offered alongside these. Current as
         * of 2026-08.
         */
        val CODEX_MODELS = listOf(
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
        )

        /** Codex reasoning-effort levels, low→high; "high" is the wizard default.
         *  Fallback only — when the agent advertises per-model sets
         *  (`capabilities.cli.codex.model_details`), [codexEffortsFor] serves the
         *  selected model's own levels instead. */
        val REASONING_EFFORTS = listOf("minimal", "low", "medium", "high")

        /**
         * Fallback Antigravity model quick-picks for the wizard dropdown, used
         * when the agent's dynamic `capabilities.cli.antigravity.models` list is
         * absent/empty (older agent or failed `agy models` probe). Entries are
         * the full display names `agy models` prints — passed verbatim to
         * `--model`. "Auto" (a null model — the CLI picks its own default) is
         * always offered alongside these. Current as of 2026-07.
         */
        val ANTIGRAVITY_MODELS = listOf(
            "Gemini 3.5 Flash (Medium)",
            "Gemini 3.5 Flash (High)",
            "Gemini 3.5 Flash (Low)",
            "Gemini 3.1 Pro (Low)",
            "Gemini 3.1 Pro (High)",
            "Claude Sonnet 4.6 (Thinking)",
            "Claude Opus 4.6 (Thinking)",
            "GPT-OSS 120B (Medium)",
        )

        /**
         * Curated Claude model aliases for the wizard dropdown. Aliases (not full
         * IDs) resolve to the provider-correct version and respect org
         * `availableModels` allowlists; "Default" (null) — the account default —
         * is offered alongside these. Current as of 2026-06.
         */
        val CLAUDE_MODELS = listOf("opus", "sonnet", "haiku", "fable")

        /** Claude effort ladder, low→high. xhigh/max only resolve on xhigh-capable
         *  models (opus / fable / sonnet); other models silently downshift, so
         *  [claudeEffortsFor] filters them out. */
        val CLAUDE_EFFORTS = listOf("low", "medium", "high", "xhigh", "max")
        private val CLAUDE_EFFORTS_BASE = listOf("low", "medium", "high")

        /** Effort levels offered for [model]: the full ladder for xhigh-capable
         *  opus/fable/sonnet, else low/medium/high (also for the null/default
         *  model, whose resolved capability is unknown — never offer a downshift). */
        fun claudeEffortsFor(model: String?): List<String> =
            if (model == "opus" || model == "fable" || model == "sonnet") CLAUDE_EFFORTS else CLAUDE_EFFORTS_BASE
    }
}

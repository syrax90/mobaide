package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.fs.PathCompletion
import com.mobaide.app.data.local.AntigravityLaunchPrefs
import com.mobaide.app.data.local.ClaudeLaunchPrefs
import com.mobaide.app.data.local.CodexLaunchPrefs
import com.mobaide.app.data.local.currentCodexApproval
import com.mobaide.app.data.local.OpenCodeLaunchPrefs
import com.mobaide.app.data.local.CopilotLaunchPrefs
import com.mobaide.app.data.local.CursorLaunchPrefs
import com.mobaide.app.data.local.JunieLaunchPrefs
import com.mobaide.app.data.local.PiLaunchPrefs
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_LINEAR
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_CHAT
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_TERMINAL
import com.mobaide.app.data.network.dto.AntigravityOptionsModel
import com.mobaide.app.data.network.dto.AutoScanCapability
import com.mobaide.app.data.network.dto.AutoScanTemplateSettingsDto
import com.mobaide.app.data.network.dto.ChatStoredSession
import com.mobaide.app.data.network.dto.ChatSupport
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.ClaudeScanOptionsDto
import com.mobaide.app.data.network.dto.CodexOptionsModel
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.CliModelDetail
import com.mobaide.app.data.network.dto.CopilotScanOptionsDto
import com.mobaide.app.data.network.dto.CursorScanOptionsDto
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.GitHubSetupRequest
import com.mobaide.app.data.network.dto.OpenCodeOptionsModel
import com.mobaide.app.data.network.dto.CopilotOptionsModel
import com.mobaide.app.data.network.dto.CursorOptionsModel
import com.mobaide.app.data.network.dto.JunieOptionsModel
import com.mobaide.app.data.network.dto.JunieScanOptionsDto
import com.mobaide.app.data.network.dto.OpenCodeScanOptionsDto
import com.mobaide.app.data.network.dto.PiScanOptionsDto
import com.mobaide.app.data.network.dto.PiOptionsModel
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.StartScanRequest
import com.mobaide.app.data.network.dto.WorkspaceUpdate
import com.mobaide.app.data.network.dto.WorktreeBasesResponse
import com.mobaide.app.data.network.dto.autoScanCapability
import com.mobaide.app.data.network.dto.chatSupport
import com.mobaide.app.data.network.dto.cliAvailable
import com.mobaide.app.data.network.dto.cliKnown
import com.mobaide.app.data.network.dto.cliModelDetails
import com.mobaide.app.data.network.dto.cliModels
import com.mobaide.app.data.network.dto.cliModelsPending
import com.mobaide.app.data.network.dto.gitCapability
import com.mobaide.app.data.repository.AutoScanRejectedException
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.AutoScanTemplateException
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.LinearMetadataRepository
import com.mobaide.app.data.repository.MetadataResult
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.data.repository.WorkspaceConflictException
import com.mobaide.app.data.repository.WorkspacesRepository
import com.mobaide.app.data.repository.apiErrorDetail
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.ui.autoscan.autoScanAgentName
import com.mobaide.app.ui.common.PathSuggestionsController
import com.mobaide.app.ui.common.PathSuggestionsUi
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.nio.file.InvalidPathException
import java.nio.file.Paths
import javax.inject.Inject

enum class CreateSessionStep { WorkspaceSelect, ModePick, StandardConfig, CodexConfig, AntigravityConfig, OpenCodeConfig, CopilotConfig, CursorConfig, JunieConfig, PiConfig, AutoScanConfig, TerminalConfig }

/**
 * Mode-picker selection. The built-in modes are singletons; [Custom] carries
 * the server id of an account-scoped custom agent type (stable across rename,
 * so a selection survives a concurrent edit).
 */
sealed interface CreateSessionMode {
    data object Standard : CreateSessionMode
    data object Codex : CreateSessionMode
    data object Antigravity : CreateSessionMode
    data object OpenCode : CreateSessionMode
    data object Copilot : CreateSessionMode
    data object Cursor : CreateSessionMode
    data object Junie : CreateSessionMode
    data object Pi : CreateSessionMode
    data object AutoScan : CreateSessionMode
    data object CustomShell : CreateSessionMode
    data class Custom(val typeId: String) : CreateSessionMode
}

/** The id `/api/session-mode-order` stores for a mode-picker card. */
val CreateSessionMode.orderId: String
    get() = when (this) {
        CreateSessionMode.Standard -> "claude"
        CreateSessionMode.Codex -> "codex"
        CreateSessionMode.Antigravity -> "antigravity"
        CreateSessionMode.OpenCode -> "opencode"
        CreateSessionMode.Copilot -> "copilot"
        CreateSessionMode.Cursor -> "cursor"
        CreateSessionMode.Junie -> "junie"
        CreateSessionMode.Pi -> "pi"
        CreateSessionMode.AutoScan -> "auto_scan"
        CreateSessionMode.CustomShell -> "shell"
        is CreateSessionMode.Custom -> "custom_$typeId"
    }

/** The built-in mode cards in their default order; custom types follow. */
val BUILT_IN_MODES: List<CreateSessionMode> = listOf(
    CreateSessionMode.Standard,
    CreateSessionMode.Codex,
    CreateSessionMode.Antigravity,
    CreateSessionMode.OpenCode,
    CreateSessionMode.Copilot,
    CreateSessionMode.Cursor,
    CreateSessionMode.Junie,
    CreateSessionMode.Pi,
    CreateSessionMode.AutoScan,
    CreateSessionMode.CustomShell,
)

/**
 * Put [modes] in the account's saved [order]. A mode the order names is
 * ranked by its place there; a mode it does not name (a custom type created
 * after the last reorder, a mode a newer client added) keeps its default place
 * among the other unnamed modes, after the named ones. Same rule on iOS and web.
 */
fun orderModes(modes: List<CreateSessionMode>, order: List<String>): List<CreateSessionMode> {
    if (order.isEmpty()) return modes
    val rank = order.withIndex().associate { (index, id) -> id to index }
    return modes.withIndex()
        .sortedBy { (index, mode) -> rank[mode.orderId] ?: (order.size + index) }
        .map { it.value }
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
 * Active selection on the workspace picker.
 *
 * `None` is the freshly-mounted state; `Stored` references a workspace from
 * [CreateSessionUiState.workspaces] by name; `CustomPath` is an ephemeral
 * one-off launch directory typed by the user and never persisted to
 * `workspaces.json` on the agent.
 */
sealed interface WorkspaceSelection {
    data object None : WorkspaceSelection
    data class Stored(val name: String) : WorkspaceSelection
    data class CustomPath(val path: String) : WorkspaceSelection
}

/** The worktree base-branch list of the selected workspace. */
sealed interface BaseBranchesLoad {
    data object Idle : BaseBranchesLoad
    data object Loading : BaseBranchesLoad
    data class Loaded(val response: WorktreeBasesResponse) : BaseBranchesLoad
    data object Failed : BaseBranchesLoad
}

data class CreateSessionUiState(
    val step: CreateSessionStep = CreateSessionStep.WorkspaceSelect,
    val workspaces: List<WorkspaceInfo> = emptyList(),
    val isLoadingWorkspaces: Boolean = false,
    val selection: WorkspaceSelection = WorkspaceSelection.None,
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
    val opencodeAvailable: Boolean? = null,
    // false = the agent lists its CLIs but not OpenCode: it predates the kind
    // and would open a plain shell for it.
    val opencodeKnown: Boolean = true,
    val copilotAvailable: Boolean? = null,
    // false = the agent lists its CLIs but not Copilot: it predates the kind
    // and would open a plain shell for it.
    val copilotKnown: Boolean = true,
    val cursorAvailable: Boolean? = null,
    // false = the agent lists its CLIs but not Cursor: it predates the kind
    // and would open a plain shell for it.
    val cursorKnown: Boolean = true,
    val junieAvailable: Boolean? = null,
    // false = the agent lists its CLIs but not Junie: it predates the kind
    // and would open a plain shell for it.
    val junieKnown: Boolean = true,
    val piAvailable: Boolean? = null,
    // false = the agent lists its CLIs but not Pi: it predates the kind
    // and would open a plain shell for it.
    val piKnown: Boolean = true,
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
    // Claude Code only: start the classic terminal instead of the chat view.
    val claudeTerminalUi: Boolean = false,
    // Claude Code only: the experimental Code Mode. It runs in the terminal UI
    // only, so it also opens the session there.
    val claudeCodeMode: Boolean = false,
    // capabilities.chat for Claude from the ModePick probe; null = not probed yet.
    val claudeChat: ChatSupport? = null,
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
    // Claude Code Mode for scans: forces the Terminal view, as in the Claude step.
    val autoScanClaudeCodeMode: Boolean = false,
    val autoScanFocusMode: Boolean = true,
    val autoScanClaudeAppendSystemPrompt: String = "",
    // capabilities.autoscan from the ModePick probe. null = an agent that
    // predates it (Linear and Claude Code only) or no answer yet.
    val autoScanCapability: AutoScanCapability? = null,
    // The ModePick probe answered, so a null capability means an older agent.
    val autoScanProbed: Boolean = false,
    // The Auto Scan step's source, filter and agent choices (newer agents).
    val autoScan: AutoScanWizardState = AutoScanWizardState(),
    // The PC's Auto Scan templates; not reset when the step re-seeds.
    val autoScanTemplates: AutoScanTemplatesUi = AutoScanTemplatesUi(),
    // The user hid the label tips on this device.
    val autoScanTipsHidden: Boolean = false,
    // Codex launch options (CodexConfig step). Defaults reflect the "Auto" preset.
    val codexModel: String? = null,
    val codexSandbox: String = "workspace-write",
    val codexApproval: String = "on-request",
    val codexFullAuto: Boolean = false,
    val codexReasoningEffort: String = "high",
    // Codex: start the classic terminal instead of the chat view.
    val codexTerminalUi: Boolean = false,
    // capabilities.chat for Codex from the ModePick probe; null = not probed yet.
    val codexChat: ChatSupport? = null,
    // Antigravity launch options (AntigravityConfig step). The two booleans are
    // mutually exclusive (server rejects the pair with a 422); the setters
    // enforce it. addDirsDraft is the raw text of the additional-directories
    // field, parsed into add_dirs at submit.
    val antigravityModel: String? = null,
    val antigravitySkipPermissions: Boolean = false,
    val antigravitySandbox: Boolean = false,
    val antigravityAddDirsDraft: String = "",
    // Antigravity: start the classic terminal instead of the chat view.
    val antigravityTerminalUi: Boolean = false,
    // capabilities.chat for Antigravity from the ModePick probe; null = not probed yet.
    val antigravityChat: ChatSupport? = null,
    val opencodeAutoApprove: Boolean = false,
    // OpenCode 2's Code Mode (MCP tools called from JavaScript); on in OpenCode by default.
    val opencodeCodeMode: Boolean = true,
    // OpenCode: start the classic terminal instead of the chat view.
    val opencodeTerminalUi: Boolean = false,
    // capabilities.chat for OpenCode from the ModePick probe; null = not probed yet.
    val opencodeChat: ChatSupport? = null,
    val copilotAllowAll: Boolean = false,
    val copilotSandbox: Boolean = false,
    // Copilot: start the classic terminal instead of the chat view.
    val copilotTerminalUi: Boolean = false,
    // capabilities.chat for Copilot from the ModePick probe; null = not probed yet.
    val copilotChat: ChatSupport? = null,
    val cursorForce: Boolean = false,
    // Cursor: start the classic terminal instead of the chat view.
    val cursorTerminalUi: Boolean = false,
    // capabilities.chat for Cursor from the ModePick probe; null = not probed yet.
    val cursorChat: ChatSupport? = null,
    val juniePlanMode: Boolean = false,
    val junieBrave: Boolean = false,
    // Junie: start the classic terminal instead of the chat view.
    val junieTerminalUi: Boolean = false,
    // capabilities.chat for Junie from the ModePick probe; null = not probed yet.
    val junieChat: ChatSupport? = null,
    // Pi: start the classic terminal instead of the chat view.
    val piTerminalUi: Boolean = false,
    // Pi's codemode tool: the model writes JavaScript that calls the other tools.
    val piCodeMode: Boolean = false,
    // capabilities.chat for Pi from the ModePick probe; null = not probed yet.
    val piChat: ChatSupport? = null,
    val initialPrompt: String = "",
    val worktree: Boolean = false,
    val branchName: String = "",
    // `capabilities.git.worktree_base`, read by the ModePick probe. Without it
    // the agent ignores base_branch, so the picker stays hidden.
    val worktreeBaseSupported: Boolean = false,
    val baseBranches: BaseBranchesLoad = BaseBranchesLoad.Idle,
    // The workspace [baseBranches] belongs to; a workspace change resets the list.
    val baseBranchesWorkspace: String? = null,
    // null = the agent's default branch.
    val baseBranch: String? = null,
    // The user chose [baseBranch] on this screen, so a reload must not re-preselect.
    val baseBranchPicked: Boolean = false,
    val submitting: Boolean = false,
    val autoScanStarting: Boolean = false,
    // Name of the stored workspace the AutoScan config step was last seeded for
    // (null = not yet seeded). Tracked per-workspace so switching workspaces mid-
    // wizard re-seeds the prompt, while re-entering the same workspace preserves
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
    // The account's mode-picker order (mode ids), synced through
    // CustomAgentTypesRepository; empty = default order.
    val modeOrder: List<String> = emptyList(),
    val typeEditor: TypeEditorState = TypeEditorState.Hidden,
    val typeEditorError: String? = null,
    val typeEditorSaving: Boolean = false,
    val deleteTypeTarget: CustomAgentTypeDto? = null,
    val deleteTypeInFlight: Boolean = false,
)

/** The mode-picker cards, built-in and custom, in the account's order. */
val CreateSessionUiState.orderedModes: List<CreateSessionMode>
    get() = orderModes(BUILT_IN_MODES + customTypes.map { CreateSessionMode.Custom(it.id) }, modeOrder)

/**
 * Read-only projection: the name of a stored workspace, or `null` for any
 * other selection (custom path / none). Lets callers that only care about
 * stored-workspace selections stay terse.
 */
val CreateSessionUiState.selectedWorkspaceName: String?
    get() = (selection as? WorkspaceSelection.Stored)?.name

/** The base-branch field shows: worktree on, a stored git workspace, and an agent that reads the field. */
val CreateSessionUiState.showBaseBranchPicker: Boolean
    get() = worktreeBaseSupported && worktree &&
        workspaces.firstOrNull { it.name == selectedWorkspaceName }?.is_git == true

/**
 * The `base_branch` to send. Only a loaded list of the selected workspace
 * yields one; loading or a failed list sends null (the agent's default).
 */
val CreateSessionUiState.effectiveBaseBranch: String?
    get() = baseBranch.takeIf {
        showBaseBranchPicker &&
            baseBranches is BaseBranchesLoad.Loaded &&
            baseBranchesWorkspace == selectedWorkspaceName
    }

/**
 * The autonomy preset currently reflected by the raw Codex sandbox/approval/
 * full-auto fields, or `null` when the user has dialed in a custom combination
 * via the Advanced controls (so the segmented control highlights nothing).
 */
val CreateSessionUiState.codexPreset: CodexAutonomyPreset?
    get() = codexPresetOf(codexSandbox, codexApproval, codexFullAuto)

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

/**
 * Whether a session starts in the chat view: Chat is chosen under Open sessions in and
 * the PC is not known to lack chat support (unknown = the agent decides).
 */
private fun usesChat(terminalUi: Boolean, chat: ChatSupport?): Boolean =
    !terminalUi && (chat == null || chat is ChatSupport.Available)

/** Why Chat cannot be chosen under Open sessions in, or null while the chat view is possible. */
fun chatUnavailableHint(chat: ChatSupport?, kind: String = "claude"): String? = when (chat) {
    ChatSupport.AgentOutdated -> "Update Mobaide on your PC to use the chat view."
    ChatSupport.Unavailable -> when (kind) {
        "antigravity" -> "The chat view needs a newer Antigravity CLI on your PC: run agy update there."
        "opencode" -> "The chat view is not available for OpenCode on your PC."
        "copilot" -> "The chat view is not available for GitHub Copilot on your PC."
        "cursor" -> "The chat view is not available for Cursor on your PC."
        "junie" -> "The chat view is not available for Junie on your PC."
        "pi" -> "The chat view is not available for Pi on your PC."
        else -> "The chat view needs Node.js on your PC."
    }
    else -> null
}

val CreateSessionUiState.claudeUsesChat: Boolean
    get() = usesChat(claudeTerminalUi, claudeChat)

val CreateSessionUiState.claudeChatUnavailableHint: String? get() = chatUnavailableHint(claudeChat)

/** "Continue a session" is offered: chat view, and the agent can list stored sessions. */
val CreateSessionUiState.claudeResumeAvailable: Boolean
    get() = claudeUsesChat && (claudeChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.codexUsesChat: Boolean get() = usesChat(codexTerminalUi, codexChat)

val CreateSessionUiState.codexChatUnavailableHint: String? get() = chatUnavailableHint(codexChat)

val CreateSessionUiState.codexResumeAvailable: Boolean
    get() = codexUsesChat && (codexChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.antigravityUsesChat: Boolean
    get() = usesChat(antigravityTerminalUi, antigravityChat)

val CreateSessionUiState.antigravityChatUnavailableHint: String?
    get() = chatUnavailableHint(antigravityChat, "antigravity")

val CreateSessionUiState.antigravityResumeAvailable: Boolean
    get() = antigravityUsesChat && (antigravityChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.opencodeUsesChat: Boolean
    get() = usesChat(opencodeTerminalUi, opencodeChat)

val CreateSessionUiState.opencodeChatUnavailableHint: String?
    get() = chatUnavailableHint(opencodeChat, "opencode")

val CreateSessionUiState.opencodeResumeAvailable: Boolean
    get() = opencodeUsesChat && (opencodeChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.copilotUsesChat: Boolean
    get() = usesChat(copilotTerminalUi, copilotChat)

val CreateSessionUiState.copilotChatUnavailableHint: String?
    get() = chatUnavailableHint(copilotChat, "copilot")

val CreateSessionUiState.copilotResumeAvailable: Boolean
    get() = copilotUsesChat && (copilotChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.cursorUsesChat: Boolean
    get() = usesChat(cursorTerminalUi, cursorChat)

val CreateSessionUiState.cursorChatUnavailableHint: String?
    get() = chatUnavailableHint(cursorChat, "cursor")

val CreateSessionUiState.cursorResumeAvailable: Boolean
    get() = cursorUsesChat && (cursorChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.junieUsesChat: Boolean
    get() = usesChat(junieTerminalUi, junieChat)

val CreateSessionUiState.junieChatUnavailableHint: String?
    get() = chatUnavailableHint(junieChat, "junie")

val CreateSessionUiState.junieResumeAvailable: Boolean
    get() = junieUsesChat && (junieChat as? ChatSupport.Available)?.resume == true

val CreateSessionUiState.piUsesChat: Boolean
    get() = usesChat(piTerminalUi, piChat)

val CreateSessionUiState.piChatUnavailableHint: String?
    get() = chatUnavailableHint(piChat, "pi")

val CreateSessionUiState.piResumeAvailable: Boolean
    get() = piUsesChat && (piChat as? ChatSupport.Available)?.resume == true

/** The folder "This folder" lists: the selected workspace's path, or the custom path. */
val CreateSessionUiState.selectedFolder: String?
    get() = when (val sel = selection) {
        is WorkspaceSelection.Stored -> workspaces.firstOrNull { it.name == sel.name }?.path
        is WorkspaceSelection.CustomPath -> sel.path.takeIf { it.isNotBlank() }
        WorkspaceSelection.None -> null
    }

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
    terminalUi = claudeTerminalUi,
    codeMode = claudeCodeMode,
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
    codeMode = autoScanClaudeCodeMode,
)

/** Snapshot of the Codex launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toCodexLaunchPrefs() = CodexLaunchPrefs(
    model = codexModel,
    sandbox = codexSandbox,
    approval = codexApproval,
    fullAuto = codexFullAuto,
    reasoningEffort = codexReasoningEffort,
    terminalUi = codexTerminalUi,
)

/** Snapshot of the Antigravity launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toAntigravityLaunchPrefs() = AntigravityLaunchPrefs(
    model = antigravityModel,
    skipPermissions = antigravitySkipPermissions,
    sandbox = antigravitySandbox,
    terminalUi = antigravityTerminalUi,
)

/** Snapshot of the OpenCode launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toOpenCodeLaunchPrefs() = OpenCodeLaunchPrefs(
    autoApprove = opencodeAutoApprove,
    terminalUi = opencodeTerminalUi,
    codeMode = opencodeCodeMode,
)

/** Snapshot of the Copilot launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toCopilotLaunchPrefs() = CopilotLaunchPrefs(
    allowAll = copilotAllowAll,
    sandbox = copilotSandbox,
    terminalUi = copilotTerminalUi,
)

/** Snapshot of the Cursor launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toCursorLaunchPrefs() = CursorLaunchPrefs(
    force = cursorForce,
    terminalUi = cursorTerminalUi,
)

/** Snapshot of the Junie launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toJunieLaunchPrefs() = JunieLaunchPrefs(
    planMode = juniePlanMode,
    brave = junieBrave,
    terminalUi = junieTerminalUi,
)

/** Snapshot of the Pi launch controls for persistence / re-defaulting. */
private fun CreateSessionUiState.toPiLaunchPrefs() = PiLaunchPrefs(
    terminalUi = piTerminalUi,
    codeMode = piCodeMode,
)

/**
 * Mode-picker gate. Only an explicit `false` from the agent disables a card;
 * `null` (unknown / not-yet-probed / probe failed) stays optimistically enabled
 * so a transient probe failure never blocks a CLI that's actually installed.
 */
val CreateSessionUiState.claudeEnabled: Boolean get() = claudeAvailable != false
val CreateSessionUiState.codexEnabled: Boolean get() = codexAvailable != false
val CreateSessionUiState.antigravityEnabled: Boolean get() = antigravityAvailable != false
val CreateSessionUiState.opencodeEnabled: Boolean get() = opencodeAvailable != false && opencodeKnown
val CreateSessionUiState.copilotEnabled: Boolean get() = copilotAvailable != false && copilotKnown
val CreateSessionUiState.cursorEnabled: Boolean get() = cursorAvailable != false && cursorKnown
val CreateSessionUiState.junieEnabled: Boolean get() = junieAvailable != false && junieKnown
val CreateSessionUiState.piEnabled: Boolean get() = piAvailable != false && piKnown

/** The selected stored workspace; null for a custom path or no selection. */
val CreateSessionUiState.selectedWorkspace: WorkspaceInfo?
    get() = selectedWorkspaceName?.let { name -> workspaces.firstOrNull { it.name == name } }

/**
 * Auto Scan needs a stored workspace. An agent with `capabilities.autoscan`
 * chooses the source and agent in the wizard; an older one scans Linear with
 * Claude Code only, so it also needs the Claude CLI and the workspace's
 * Linear API key.
 */
val CreateSessionUiState.autoScanEnabled: Boolean
    get() {
        val workspace = selectedWorkspace ?: return false
        return autoScanCapability != null || (claudeEnabled && workspace.has_linear_key)
    }

/** `capabilities.cli.<kind>.available` from the ModePick probe; null = unknown. */
fun CreateSessionUiState.cliAvailability(kind: String): Boolean? = when (kind) {
    "claude" -> claudeAvailable
    "codex" -> codexAvailable
    "antigravity" -> antigravityAvailable
    "opencode" -> opencodeAvailable
    "copilot" -> copilotAvailable
    "cursor" -> cursorAvailable
    "junie" -> junieAvailable
    "pi" -> piAvailable
    else -> null
}

/** `capabilities.chat` for [kind] from the ModePick probe; null = not probed yet. */
fun CreateSessionUiState.chatSupport(kind: String): ChatSupport? = when (kind) {
    "claude" -> claudeChat
    "codex" -> codexChat
    "antigravity" -> antigravityChat
    "opencode" -> opencodeChat
    "copilot" -> copilotChat
    "cursor" -> cursorChat
    "junie" -> junieChat
    "pi" -> piChat
    else -> null
}

/** The chosen Auto Scan agent can open its sessions in the chat view on this PC. */
val CreateSessionUiState.autoScanChatAvailable: Boolean
    get() = autoScanCli?.let { chatSupport(it) } is ChatSupport.Available

/** The session view a start sends: the choice, or Terminal while the agent has no chat view. */
val CreateSessionUiState.autoScanUi: String
    get() = if (autoScan.ui == AUTO_SCAN_UI_CHAT && autoScanChatAvailable) {
        AUTO_SCAN_UI_CHAT
    } else {
        AUTO_SCAN_UI_TERMINAL
    }

/** The Auto Scan agent, when the agent offers it; a remembered kind it no longer lists is no choice. */
val CreateSessionUiState.autoScanCli: String?
    get() = autoScan.cli?.takeIf { cli -> autoScanCapability?.clis?.contains(cli) == true }

/**
 * Why Start Auto Scan is disabled on a newer agent, in the order the step asks
 * for the choices; null when the scan can start.
 */
val CreateSessionUiState.autoScanStartBlocker: String?
    get() {
        val workspace = selectedWorkspace ?: return "Choose a saved workspace."
        val wizard = autoScan
        when (wizard.source) {
            null -> return "Choose where to find issues."
            AUTO_SCAN_SOURCE_GITHUB -> {
                if (!wizard.githubReady) {
                    return when {
                        wizard.githubStatus != null -> "Solve the GitHub problem above, then tap Check again."
                        wizard.githubStatusError != null -> "Could not check GitHub. Tap Check again."
                        else -> "Checking GitHub on the PC…"
                    }
                }
                if (wizard.githubFilter.labels.isEmpty()) return "Choose at least one label to pick up."
                if (!wizard.githubFilter.isValid) return "Fix the GitHub filter above."
            }
            else -> {
                if (!workspace.has_linear_key) return "Save a Linear API key first."
                if (!wizard.linearFilter.isValid) return "Fill in the Linear filter above."
            }
        }
        val cli = autoScanCli ?: return "Choose the AI agent."
        if (cliAvailability(cli) != true) return "${autoScanAgentName(cli)} is not installed on the PC."
        if (cli == "antigravity" && wizard.agents.antigravity.addDirsError != null) {
            return "Fix the additional directories above."
        }
        if (initialPrompt.length > AUTO_SCAN_PROMPT_MAX) {
            return "The instructions can have at most 32,000 characters."
        }
        return null
    }

/** The Claude Code options an Auto Scan keeps, from the Auto Scan bucket. */
fun CreateSessionUiState.autoScanClaudeScanOptions() = ClaudeScanOptionsDto(
    plan_mode = autoScanPlanMode,
    no_memory = autoScanNoMemory,
    skip_permissions = autoScanSkipPermissions,
    model = autoScanClaudeModel,
    effort = autoScanClaudeEffort,
    fast_mode = autoScanClaudeFastMode,
    ultracode = autoScanClaudeUltracode,
    append_system_prompt = autoScanClaudeAppendSystemPrompt.takeIf { it.isNotBlank() },
    debug = autoScanClaudeDebug,
    focus_mode = autoScanFocusMode,
    code_mode = autoScanClaudeCodeMode,
)

/**
 * The Auto Scan step's choices as template settings: both filters as drafts,
 * the prompt as typed, and the options of the chosen agent only. The start
 * request takes its agent options from here too.
 */
fun CreateSessionUiState.autoScanTemplateSettings(): AutoScanTemplateSettingsDto {
    val wizard = autoScan
    val cli = wizard.cli
    val agents = wizard.agents
    return AutoScanTemplateSettingsDto(
        source = wizard.source,
        cli = cli,
        // The view the user chose: a template may run on a PC where the
        // agent has the chat view. A start sends the effective one.
        ui = wizard.ui,
        interval_minutes = wizard.intervalMinutes,
        initial_prompt = initialPrompt,
        linear_filter = wizard.linearFilter.toDraftDto(),
        github_filter = wizard.githubFilter.toDto(),
        claude_options = autoScanClaudeScanOptions().takeIf { cli == "claude" },
        codex_options = agents.codex.toDto().takeIf { cli == "codex" },
        antigravity_options = agents.antigravity.toDto().takeIf { cli == "antigravity" },
        opencode_options = OpenCodeScanOptionsDto(agents.opencodeAutoApprove, agents.opencodeCodeMode)
            .takeIf { cli == "opencode" },
        copilot_options = CopilotScanOptionsDto(agents.copilotAllowAll).takeIf { cli == "copilot" },
        cursor_options = CursorScanOptionsDto(agents.cursorForce).takeIf { cli == "cursor" },
        junie_options = JunieScanOptionsDto(agents.juniePlanMode, agents.junieBrave).takeIf { cli == "junie" },
        pi_options = PiScanOptionsDto(agents.piCodeMode).takeIf { cli == "pi" },
    )
}

/**
 * The state with [settings] applied: each non-null field replaces the choice
 * it names, an options object its agent's Auto Scan bucket. Seeding a
 * workspace's saved choices and applying a template both go through here.
 */
fun CreateSessionUiState.withAutoScanSettings(settings: AutoScanTemplateSettingsDto): CreateSessionUiState {
    val claude = settings.claude_options
    val claudeModel = if (claude != null) claude.model else autoScanClaudeModel
    val claudeEffort = claude?.effort ?: autoScanClaudeEffort
    return copy(
        initialPrompt = settings.initial_prompt ?: initialPrompt,
        autoScan = autoScan.copy(
            source = settings.source
                ?.takeIf { it == AUTO_SCAN_SOURCE_GITHUB || it == AUTO_SCAN_SOURCE_LINEAR }
                ?: autoScan.source,
            cli = settings.cli ?: autoScan.cli,
            ui = autoScanUiOrNull(settings.ui) ?: autoScan.ui,
            intervalMinutes = settings.interval_minutes?.takeIf { it in AUTO_SCAN_INTERVAL_RANGE }
                ?: autoScan.intervalMinutes,
            linearFilter = settings.linear_filter?.let(LinearFilterDraft::from) ?: autoScan.linearFilter,
            githubFilter = settings.github_filter?.let(GitHubFilterDraft::from) ?: autoScan.githubFilter,
            agents = autoScan.agents.applied(settings),
            startError = null,
        ),
        autoScanPlanMode = claude?.plan_mode ?: autoScanPlanMode,
        autoScanNoMemory = claude?.no_memory ?: autoScanNoMemory,
        autoScanSkipPermissions = claude?.skip_permissions ?: autoScanSkipPermissions,
        autoScanClaudeModel = claudeModel,
        autoScanClaudeEffort = claudeEffort.takeIf { it in CreateSessionViewModel.claudeEffortsFor(claudeModel) }
            ?: "high",
        autoScanClaudeFastMode = claude?.fast_mode ?: autoScanClaudeFastMode,
        autoScanClaudeUltracode = claude?.ultracode ?: autoScanClaudeUltracode,
        autoScanClaudeDebug = claude?.debug ?: autoScanClaudeDebug,
        autoScanFocusMode = claude?.focus_mode ?: autoScanFocusMode,
        autoScanClaudeCodeMode = claude?.code_mode ?: autoScanClaudeCodeMode,
        autoScanClaudeAppendSystemPrompt = claude?.let { it.append_system_prompt.orEmpty() }
            ?: autoScanClaudeAppendSystemPrompt,
    )
}

/**
 * Why the step's choices cannot be saved as a template, or null: only what the
 * agent's draft models refuse. A half-made filter, or a label to add that is
 * also a trigger label, can still be saved.
 */
val CreateSessionUiState.autoScanTemplateSaveBlocker: String?
    get() {
        val wizard = autoScan
        val filter = wizard.githubFilter
        val labels = filter.labels.map { it.trim() }
        val active = filter.activeLabel.trim()
        val addDirsError = wizard.agents.antigravity.addDirsError.takeIf { wizard.cli == "antigravity" }
        return when {
            labels.any { ',' in it } -> "A label to pick up cannot contain a comma."
            labels.any { it.length > GITHUB_LABEL_MAX } ->
                "A label to pick up can have at most $GITHUB_LABEL_MAX characters."
            ',' in active -> "The label to add cannot contain a comma."
            active.length > GITHUB_LABEL_MAX ->
                "The label to add can have at most $GITHUB_LABEL_MAX characters."
            filter.issueTypes.any { it.trim().length > GITHUB_LABEL_MAX } ->
                "An issue type can have at most $GITHUB_LABEL_MAX characters."
            filter.assignee.trim().length > GITHUB_LABEL_MAX ->
                "The assignee can have at most $GITHUB_LABEL_MAX characters."
            initialPrompt.length > AUTO_SCAN_PROMPT_MAX ->
                "The instructions can have at most 32,000 characters."
            addDirsError != null -> "Additional directories: ${addDirsError.removeSuffix(".")}."
            else -> null
        }
    }

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
    CreateSessionMode.OpenCode -> opencodeEnabled
    CreateSessionMode.Copilot -> copilotEnabled
    CreateSessionMode.Cursor -> cursorEnabled
    CreateSessionMode.Junie -> junieEnabled
    CreateSessionMode.Pi -> piEnabled
    CreateSessionMode.CustomShell -> true
    is CreateSessionMode.Custom -> true
}

@HiltViewModel
class CreateSessionViewModel @Inject constructor(
    private val workspacesRepository: WorkspacesRepository,
    private val sessionRepository: SessionRepository,
    private val autoScanRepository: AutoScanRepository,
    private val preferencesStore: PreferencesStore,
    private val tokenStore: TokenStore,
    private val customAgentTypesRepository: CustomAgentTypesRepository,
    pathSuggestionRepository: PathSuggestionRepository,
    private val gitRepository: GitRepository,
    private val linearMetadataRepository: LinearMetadataRepository,
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

    // The GitHub setup terminal opens on top of the wizard, which stays on the
    // back stack so its choices survive the round trip.
    private val _navigateToSetupSession = Channel<String>(Channel.BUFFERED)
    val navigateToSetupSession: Flow<String> = _navigateToSetupSession.receiveAsFlow()

    init {
        viewModelScope.launch {
            preferencesStore.autoScanTipsHidden.collect { hidden ->
                _uiState.update { it.copy(autoScanTipsHidden = hidden) }
            }
        }
        viewModelScope.launch {
            // Pre-fill the Claude reasoning controls from the last successful
            // launch (mirrors the last-used-workspace pre-fill below).
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
                        claudeTerminalUi = saved.terminalUi,
                        claudeCodeMode = saved.codeMode,
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
                        autoScanClaudeCodeMode = saved.codeMode,
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
                        codexApproval = currentCodexApproval(saved.approval),
                        codexFullAuto = saved.fullAuto,
                        codexTerminalUi = saved.terminalUi,
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
                        antigravityTerminalUi = saved.terminalUi,
                    )
                }
            }
            // Same pre-fill for the OpenCode launch controls.
            preferencesStore.getLastUsedOpenCodeOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        opencodeAutoApprove = saved.autoApprove,
                        opencodeTerminalUi = saved.terminalUi,
                        opencodeCodeMode = saved.codeMode,
                    )
                }
            }
            // Same pre-fill for the Copilot launch controls.
            preferencesStore.getLastUsedCopilotOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        copilotAllowAll = saved.allowAll,
                        copilotSandbox = saved.sandbox,
                        copilotTerminalUi = saved.terminalUi,
                    )
                }
            }
            // Same pre-fill for the Cursor launch controls.
            preferencesStore.getLastUsedCursorOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        cursorForce = saved.force,
                        cursorTerminalUi = saved.terminalUi,
                    )
                }
            }
            // Same pre-fill for the Junie launch controls.
            preferencesStore.getLastUsedJunieOptions()?.let { saved ->
                _uiState.update {
                    it.copy(
                        juniePlanMode = saved.planMode,
                        junieBrave = saved.brave,
                        junieTerminalUi = saved.terminalUi,
                    )
                }
            }
            // Same pre-fill for the Pi launch controls.
            preferencesStore.getLastUsedPiOptions()?.let { saved ->
                _uiState.update { it.copy(piTerminalUi = saved.terminalUi, piCodeMode = saved.codeMode) }
            }
        }

        viewModelScope.launch {
            val last = preferencesStore.getLastUsedWorkspaceName()
            workspacesRepository.observeWorkspaces().collect { workspaces ->
                serverWorkspaces = workspaces
                val display = displayWorkspaces()
                _uiState.update { state ->
                    val resolved = state.selection.takeUnless { it is WorkspaceSelection.None }
                        ?: workspaces.firstOrNull { it.name == last }?.let { WorkspaceSelection.Stored(it.name) }
                        ?: WorkspaceSelection.None
                    state.copy(
                        workspaces = display,
                        isLoadingWorkspaces = workspaces.isEmpty(),
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
            // Refs moved on the agent: refresh a list already on screen.
            gitRepository.changed.collect { name ->
                val state = _uiState.value
                if (name == state.baseBranchesWorkspace &&
                    state.baseBranches is BaseBranchesLoad.Loaded &&
                    state.showBaseBranchPicker
                ) {
                    loadBaseBranches(name, silent = true)
                }
            }
        }

        viewModelScope.launch {
            customAgentTypesRepository.observeModeOrder().collect { order ->
                _uiState.update { it.copy(modeOrder = order) }
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
            try {
                customAgentTypesRepository.refreshModeOrder()
            } catch (_: Exception) {
                // The cards keep their default order until a refresh lands.
            }
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingWorkspaces = true) }
            try {
                val workspaces = workspacesRepository.listWorkspaces()
                val last = preferencesStore.getLastUsedWorkspaceName()
                if (serverWorkspaces.isEmpty()) serverWorkspaces = workspaces
                val display = displayWorkspaces()
                _uiState.update { state ->
                    if (state.workspaces.isEmpty()) {
                        val resolved = state.selection.takeUnless { it is WorkspaceSelection.None }
                            ?: workspaces.firstOrNull { it.name == last }?.let { WorkspaceSelection.Stored(it.name) }
                            ?: WorkspaceSelection.None
                        state.copy(
                            workspaces = display,
                            isLoadingWorkspaces = false,
                            selection = resolved,
                        )
                    } else {
                        state.copy(isLoadingWorkspaces = false)
                    }
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoadingWorkspaces = false, error = "Failed to load workspaces: ${e.message}")
                }
            }
        }
    }

    // The latest workspace list the agent sent, and the order a drop is saving.
    // Both are touched only on viewModelScope's main dispatcher.
    private var serverWorkspaces: List<WorkspaceInfo> = emptyList()
    private var pendingWorkspaceOrder: List<String>? = null
    private val workspaceOrderMutex = Mutex()

    /**
     * [serverWorkspaces] in the order to show: the server's own, or the order a
     * save in flight carries until the server's list matches it.
     */
    private fun displayWorkspaces(): List<WorkspaceInfo> {
        val sorted = serverWorkspaces.sortedBy { it.order }
        val pending = pendingWorkspaceOrder ?: return sorted
        if (sorted.map { it.name } == pending) {
            pendingWorkspaceOrder = null
            return sorted
        }
        val rank = pending.withIndex().associate { (index, name) -> name to index }
        return sorted.sortedBy { rank[it.name] ?: Int.MAX_VALUE }
    }

    /** Move one workspace row by index — the accessibility "Move up / down" path. */
    fun moveWorkspace(from: Int, to: Int) {
        val names = _uiState.value.workspaces.map { it.name }
        if (from !in names.indices || to !in names.indices || from == to) return
        reorderWorkspaces(names.toMutableList().apply { add(to, removeAt(from)) })
    }

    /**
     * Save a new workspace order (the agent's own, so every client shows it) and
     * show it at once. Saves run one at a time, and finish even if the user
     * leaves the wizard right after the drop.
     */
    fun reorderWorkspaces(names: List<String>) {
        if (names == _uiState.value.workspaces.map { it.name }) return
        pendingWorkspaceOrder = names
        _uiState.update { it.copy(workspaces = displayWorkspaces()) }
        viewModelScope.launch {
            try {
                val saved = withContext(NonCancellable) {
                    workspaceOrderMutex.withLock { workspacesRepository.reorderWorkspaces(names) }
                }
                // The answer is the agent's re-sorted list; take it rather than
                // wait for the `projects.changed` echo, which a down events
                // socket would never deliver.
                serverWorkspaces = saved
                if (pendingWorkspaceOrder == names) pendingWorkspaceOrder = null
                _uiState.update { it.copy(workspaces = displayWorkspaces()) }
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                if (pendingWorkspaceOrder == names) {
                    pendingWorkspaceOrder = null
                    _uiState.update { it.copy(workspaces = displayWorkspaces()) }
                    _transientMessages.tryEmit("Reorder failed — reverted.")
                }
            }
        }
    }

    /** Move one mode card by index — the accessibility "Move up / down" path. */
    fun moveMode(from: Int, to: Int) {
        val ids = _uiState.value.orderedModes.map { it.orderId }
        if (from !in ids.indices || to !in ids.indices || from == to) return
        reorderModes(ids.toMutableList().apply { add(to, removeAt(from)) })
    }

    /** Save the account's mode-picker order; the repository shows it at once. */
    fun reorderModes(ids: List<String>) {
        if (ids == _uiState.value.orderedModes.map { it.orderId }) return
        viewModelScope.launch {
            try {
                customAgentTypesRepository.saveModeOrder(ids)
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                _transientMessages.tryEmit("Reorder failed — reverted.")
            }
        }
    }

    fun selectWorkspace(name: String) {
        _uiState.update {
            val isGit = it.workspaces.firstOrNull { p -> p.name == name }?.is_git == true
            it.withBaseBranchesFor(name).copy(
                selection = WorkspaceSelection.Stored(name),
                customPathDraft = "",
                customPathError = null,
                worktree = if (isGit) it.worktree else false,
                branchName = if (isGit) it.branchName else "",
            )
        }
        ensureBaseBranches()
    }

    /**
     * Called after `WorkspacesViewModel.workspaceCreated` emits — uses the new
     * workspace's own `is_git` directly rather than looking it up in
     * `state.workspaces`, which has not yet been refreshed by the
     * `AgentEvent.WorkspacesChanged` bus event.
     */
    fun onWorkspaceCreated(workspace: WorkspaceInfo) = _uiState.update {
        it.withBaseBranchesFor(workspace.name).copy(
            selection = WorkspaceSelection.Stored(workspace.name),
            customPathDraft = "",
            customPathError = null,
            worktree = if (workspace.is_git) it.worktree else false,
            branchName = if (workspace.is_git) it.branchName else "",
        )
    }

    /**
     * Switches the picker into "Custom path…" mode. Loads any per-agent
     * recents asynchronously so the chips show up as soon as the panel
     * renders. Clears any prior draft / error — unless the mode is already on:
     * the row stays on screen, so a second tap only refreshes the recents and
     * must not wipe a path the user has typed (iOS `selectCustomPath`).
     */
    fun selectCustomPath() {
        if (_uiState.value.selection !is WorkspaceSelection.CustomPath) {
            _uiState.update {
                it.withBaseBranchesFor(null).copy(
                    selection = WorkspaceSelection.CustomPath(""),
                    customPathDraft = "",
                    customPathError = null,
                    worktree = false,
                    branchName = "",
                )
            }
            customPathSuggestions.onTextChange("")
            customPathSuggestions.probe()
        }
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
                WorkspaceSelection.CustomPath(PathCompletion.trimTrailingSeparator(value.trim()))
            } else {
                WorkspaceSelection.CustomPath("")
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

    fun confirmWorkspace() = _uiState.update { state ->
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> state.copy(step = CreateSessionStep.ModePick)
            is WorkspaceSelection.CustomPath ->
                if (sel.path.isNotBlank() && state.customPathError == null) {
                    state.copy(step = CreateSessionStep.ModePick)
                } else {
                    state
                }
            WorkspaceSelection.None -> state
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
                val hadAutoScanCapability = _uiState.value.autoScanCapability != null
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
                        claudeChat = version.chatSupport("claude"),
                        codexChat = version.chatSupport("codex"),
                        antigravityChat = version.chatSupport("antigravity"),
                        opencodeChat = version.chatSupport("opencode"),
                        copilotChat = version.chatSupport("copilot"),
                        cursorChat = version.chatSupport("cursor"),
                        junieChat = version.chatSupport("junie"),
                        piChat = version.chatSupport("pi"),
                        codexAvailable = version.cliAvailable("codex"),
                        antigravityAvailable = version.cliAvailable("antigravity"),
                        opencodeAvailable = version.cliAvailable("opencode"),
                        opencodeKnown = version.cliKnown("opencode"),
                        copilotAvailable = version.cliAvailable("copilot"),
                        copilotKnown = version.cliKnown("copilot"),
                        cursorAvailable = version.cliAvailable("cursor"),
                        cursorKnown = version.cliKnown("cursor"),
                        junieAvailable = version.cliAvailable("junie"),
                        junieKnown = version.cliKnown("junie"),
                        piAvailable = version.cliAvailable("pi"),
                        piKnown = version.cliKnown("pi"),
                        codexModels = version.cliModels("codex"),
                        antigravityModels = version.cliModels("antigravity"),
                        codexModelDetails = codexDetails,
                        worktreeBaseSupported = version.gitCapability("worktree_base"),
                        autoScanCapability = version.autoScanCapability(),
                        autoScanProbed = true,
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
                ensureBaseBranches()
                // The step can open before the probe answers; its templates
                // need the capability, so they load once it arrives.
                val now = _uiState.value
                if (!hadAutoScanCapability && now.autoScanCapability != null &&
                    now.step == CreateSessionStep.AutoScanConfig
                ) {
                    loadAutoScanTemplates()
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
            CreateSessionMode.OpenCode -> selectOpenCodeMode()
            CreateSessionMode.Copilot -> selectCopilotMode()
            CreateSessionMode.Cursor -> selectCursorMode()
            CreateSessionMode.Junie -> selectJunieMode()
            CreateSessionMode.Pi -> selectPiMode()
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

    fun selectOpenCodeMode() = _uiState.update {
        if (it.opencodeEnabled) it.copy(step = CreateSessionStep.OpenCodeConfig) else it
    }

    fun selectCopilotMode() = _uiState.update {
        if (it.copilotEnabled) it.copy(step = CreateSessionStep.CopilotConfig) else it
    }

    fun selectCursorMode() = _uiState.update {
        if (it.cursorEnabled) it.copy(step = CreateSessionStep.CursorConfig) else it
    }

    fun selectJunieMode() = _uiState.update {
        if (it.junieEnabled) it.copy(step = CreateSessionStep.JunieConfig) else it
    }

    fun selectPiMode() = _uiState.update {
        if (it.piEnabled) it.copy(step = CreateSessionStep.PiConfig) else it
    }

    /**
     * Commit the mode picker's Terminal card. A stored git workspace
     * opens the Terminal session page (worktree toggle); anything else
     * (non-git workspace, custom path) has nothing to configure, so the shell
     * session is created immediately rather than showing an empty sheet.
     */
    fun selectTerminalMode() {
        val state = _uiState.value
        val storedGit = (state.selection as? WorkspaceSelection.Stored)?.let { sel ->
            state.workspaces.firstOrNull { it.name == sel.name }?.is_git == true
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
     * stored git workspace opens the Terminal config page (worktree toggle) and
     * anything else launches immediately.
     */
    fun selectCustomAgentMode(typeId: String) {
        val state = _uiState.value
        val storedGit = (state.selection as? WorkspaceSelection.Stored)?.let { sel ->
            state.workspaces.firstOrNull { it.name == sel.name }?.is_git == true
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

    fun selectAutoScanMode() {
        seedAutoScanStep()
        loadAutoScanTemplates()
    }

    private fun seedAutoScanStep() = _uiState.update {
        // An older agent runs Claude Code only.
        if (it.autoScanCapability == null && !it.claudeEnabled) return@update it
        val workspaceName = it.selectedWorkspaceName
        if (workspaceName != null && it.autoScanConfigInitializedFor == workspaceName) {
            // Already seeded for this workspace — keep the user's in-flight edits.
            it.copy(step = CreateSessionStep.AutoScanConfig)
        } else {
            // First entry for this workspace, or the selected workspace changed since
            // the last entry: (re-)seed from this workspace's last-saved Auto Scan
            // prompt (persisted server-side on the previous scan start, delivered
            // via WorkspaceInfo); fall back to the agent-wide default, then empty.
            // The source, agent and filters come from the workspace's last scan
            // start; a filter nobody chose starts empty.
            val workspace = workspaceName
                ?.let { name -> it.workspaces.firstOrNull { p -> p.name == name } }
            val saved = workspace?.auto_scan
            cancelAutoScanLoads()
            val seeded = it.copy(
                step = CreateSessionStep.AutoScanConfig,
                autoScanConfigInitializedFor = workspaceName,
                // A newer agent picks the source's own default for an empty
                // prompt; the agent-wide default only fits Linear.
                initialPrompt = if (it.autoScanCapability != null) {
                    workspace?.auto_scan_prompt ?: ""
                } else {
                    workspace?.auto_scan_prompt ?: autoScanRepository.getAutoScanPrompt() ?: ""
                },
                autoScan = AutoScanWizardState(),
                autoScanTemplates = it.autoScanTemplates.copy(appliedName = null, message = null),
            )
            if (saved != null) seeded.withAutoScanSettings(saved.asTemplateSettings(prompt = null)) else seeded
        }
    }

    private var githubStatusJob: Job? = null
    private var githubLabelsJob: Job? = null
    private var linearMetadataJob: Job? = null

    private fun cancelAutoScanLoads() {
        githubStatusJob?.cancel()
        githubLabelsJob?.cancel()
        linearMetadataJob?.cancel()
    }

    private fun updateAutoScan(mutator: (AutoScanWizardState) -> AutoScanWizardState) =
        _uiState.update { it.copy(autoScan = mutator(it.autoScan)) }

    /** Applies [mutator] only while the Auto Scan step is still seeded for [workspace]. */
    private fun updateAutoScanFor(workspace: String, mutator: (AutoScanWizardState) -> AutoScanWizardState) =
        _uiState.update {
            if (it.autoScanConfigInitializedFor == workspace) it.copy(autoScan = mutator(it.autoScan)) else it
        }

    fun setAutoScanSource(source: String) = updateAutoScan { it.copy(source = source, startError = null) }

    fun setAutoScanCli(cli: String) = updateAutoScan { it.copy(cli = cli, startError = null) }

    private fun updateAutoScanAgents(mutator: (AutoScanAgentBuckets) -> AutoScanAgentBuckets) =
        updateAutoScan { it.copy(agents = mutator(it.agents), startError = null) }

    private fun updateAutoScanCodex(mutator: (CodexScanBucket) -> CodexScanBucket) =
        updateAutoScanAgents { it.copy(codex = mutator(it.codex)) }

    private fun updateAutoScanAntigravity(mutator: (AntigravityScanBucket) -> AntigravityScanBucket) =
        updateAutoScanAgents { it.copy(antigravity = mutator(it.antigravity)) }

    fun selectAutoScanCodexPreset(preset: CodexAutonomyPreset) = updateAutoScanCodex { it.withPreset(preset) }

    fun setAutoScanCodexModel(v: String?) {
        val details = _uiState.value.codexModelDetails
        updateAutoScanCodex {
            it.copy(model = v, reasoningEffort = it.reasoningEffort?.let { e -> codexEffortForModel(v, e, details) })
        }
    }

    fun setAutoScanCodexReasoningEffort(v: String?) = updateAutoScanCodex { it.copy(reasoningEffort = v) }

    fun setAutoScanUi(ui: String) = updateAutoScan { it.copy(ui = ui, startError = null) }

    fun setAutoScanInterval(minutes: Int) = updateAutoScan { it.copy(intervalMinutes = minutes, startError = null) }

    fun setAutoScanTipsHidden(hidden: Boolean) {
        _uiState.update { it.copy(autoScanTipsHidden = hidden) }
        viewModelScope.launch { preferencesStore.setAutoScanTipsHidden(hidden) }
    }
    fun setAutoScanCodexSandbox(v: String) = updateAutoScanCodex { it.copy(sandbox = v) }
    fun setAutoScanCodexApproval(v: String) = updateAutoScanCodex { it.copy(approval = v) }

    fun setAutoScanAntigravityModel(v: String?) = updateAutoScanAntigravity { it.copy(model = v) }
    fun setAutoScanAntigravityAddDirsDraft(v: String) = updateAutoScanAntigravity { it.copy(addDirsDraft = v) }

    /** Same mutual exclusion as [setAntigravitySkipPermissions]. */
    fun setAutoScanAntigravitySkipPermissions(v: Boolean) = updateAutoScanAntigravity {
        if (v) it.copy(skipPermissions = true, sandbox = false) else it.copy(skipPermissions = false)
    }

    fun setAutoScanAntigravitySandbox(v: Boolean) = updateAutoScanAntigravity {
        if (v) it.copy(sandbox = true, skipPermissions = false) else it.copy(sandbox = false)
    }

    fun setAutoScanOpenCodeAutoApprove(v: Boolean) = updateAutoScanAgents { it.copy(opencodeAutoApprove = v) }
    fun setAutoScanCopilotAllowAll(v: Boolean) = updateAutoScanAgents { it.copy(copilotAllowAll = v) }
    fun setAutoScanCursorForce(v: Boolean) = updateAutoScanAgents { it.copy(cursorForce = v) }
    fun setAutoScanJuniePlanMode(v: Boolean) = updateAutoScanAgents { it.copy(juniePlanMode = v) }
    fun setAutoScanJunieBrave(v: Boolean) = updateAutoScanAgents { it.copy(junieBrave = v) }

    fun setAutoScanOpenCodeCodeMode(v: Boolean) = updateAutoScanAgents { it.copy(opencodeCodeMode = v) }

    fun setAutoScanPiCodeMode(v: Boolean) = updateAutoScanAgents { it.copy(piCodeMode = v) }

    private fun updateAutoScanTemplates(mutator: (AutoScanTemplatesUi) -> AutoScanTemplatesUi) =
        _uiState.update { it.copy(autoScanTemplates = mutator(it.autoScanTemplates)) }

    private var templatesJob: Job? = null

    /** Read the PC's templates; [force] reloads after a failure ("Retry"). */
    fun loadAutoScanTemplates(force: Boolean = false) {
        val state = _uiState.value
        if (state.autoScanCapability == null) return
        if (!force && state.autoScanTemplates.loading) return
        templatesJob?.cancel()
        updateAutoScanTemplates { it.copy(loading = true, loadError = null) }
        templatesJob = viewModelScope.launch {
            try {
                val templates = autoScanRepository.templates()
                updateAutoScanTemplates { it.copy(templates = templates, loading = false, loaded = true) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val detail = (e as? AutoScanTemplateException)?.detail
                updateAutoScanTemplates {
                    it.copy(loading = false, loadError = detail ?: "Could not load the templates.")
                }
            }
        }
    }

    /** Replace the step's choices with the template's; it applies at once. */
    fun applyAutoScanTemplate(templateId: String) {
        val template = _uiState.value.autoScanTemplates.templates.firstOrNull { it.id == templateId } ?: return
        _uiState.update {
            it.withAutoScanSettings(template.settings).copy(
                autoScanTemplates = it.autoScanTemplates.copy(
                    appliedName = template.name,
                    message = "Applied “${template.name}”.",
                    saveError = null,
                    deleteError = null,
                ),
            )
        }
    }

    fun deleteAutoScanTemplate(templateId: String) {
        val template = _uiState.value.autoScanTemplates.templates.firstOrNull { it.id == templateId } ?: return
        updateAutoScanTemplates { it.copy(deleteError = null, message = null) }
        viewModelScope.launch {
            try {
                val templates = autoScanRepository.deleteTemplate(templateId)
                updateAutoScanTemplates {
                    it.copy(
                        templates = templates,
                        loaded = true,
                        appliedName = it.appliedName.takeUnless { name -> name == template.name },
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val refused = e as? AutoScanTemplateException
                updateAutoScanTemplates {
                    if (refused?.status == 404) {
                        // Another device deleted it first: the result is the same.
                        it.copy(
                            templates = it.templates.filterNot { t -> t.id == templateId },
                            appliedName = it.appliedName.takeUnless { name -> name == template.name },
                        )
                    } else {
                        it.copy(deleteError = refused?.detail ?: "Could not delete “${template.name}”.")
                    }
                }
            }
        }
    }

    /** Only once the list is loaded: the dialog must know which names a save would replace. */
    fun openAutoScanTemplateSave() = updateAutoScanTemplates {
        if (!it.canSave) it else it.copy(saveDialogOpen = true, saveError = null, message = null)
    }

    fun dismissAutoScanTemplateSave() = updateAutoScanTemplates {
        if (it.saving) it else it.copy(saveDialogOpen = false, saveError = null)
    }

    /** Save the step's choices as [name]; a template of the same name is replaced. */
    fun saveAutoScanTemplate(name: String) {
        val state = _uiState.value
        if (state.autoScanTemplates.saving || !state.autoScanTemplates.loaded) return
        val clean = name.trim().split(Regex("\\s+")).joinToString(" ")
        val error = when {
            clean.isEmpty() -> "Enter a name."
            clean.length > TEMPLATE_NAME_MAX -> "A name can have at most $TEMPLATE_NAME_MAX characters."
            else -> state.autoScanTemplateSaveBlocker
        }
        if (error != null) {
            updateAutoScanTemplates { it.copy(saveError = error) }
            return
        }
        val settings = state.autoScanTemplateSettings()
        updateAutoScanTemplates { it.copy(saving = true, saveError = null) }
        viewModelScope.launch {
            try {
                val templates = autoScanRepository.saveTemplate(clean, settings)
                updateAutoScanTemplates {
                    it.copy(
                        templates = templates,
                        loaded = true,
                        saving = false,
                        saveDialogOpen = false,
                        appliedName = clean,
                        message = "Saved “$clean”.",
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val refused = e as? AutoScanTemplateException
                val message = refused?.detail ?: if (refused?.status == 422) {
                    "The PC did not accept these settings. Check the filter and the agent options."
                } else {
                    "Could not save the template."
                }
                updateAutoScanTemplates {
                    it.copy(saving = false, saveError = message)
                }
            }
        }
    }

    private fun updateLinearFilter(mutator: (LinearFilterDraft) -> LinearFilterDraft) = updateAutoScan {
        it.copy(linearFilter = mutator(it.linearFilter), linearFilterTouched = true, startError = null)
    }

    fun toggleAutoScanLinearLabel(name: String) = updateLinearFilter {
        it.copy(triggerLabels = if (name in it.triggerLabels) it.triggerLabels - name else it.triggerLabels + name)
    }

    fun toggleAutoScanLinearState(name: String) = updateLinearFilter {
        it.copy(triggerStates = if (name in it.triggerStates) it.triggerStates - name else it.triggerStates + name)
    }

    fun setAutoScanLinearActiveState(v: String) = updateLinearFilter { it.copy(activeState = v) }

    fun setAutoScanLinearDoneState(v: String) = updateLinearFilter { it.copy(doneState = v) }

    fun setAutoScanLinearRemovalLabel(v: String) = updateLinearFilter { it.copy(removalLabel = v) }

    fun setAutoScanLinearKeyDraft(v: String) = updateAutoScan { it.copy(linearKeyDraft = v, linearKeyError = null) }

    fun toggleAutoScanLinearKeyVisible() = updateAutoScan { it.copy(linearKeyVisible = !it.linearKeyVisible) }

    /**
     * Save the typed Linear API key on the workspace (`PUT /api/projects/{name}`),
     * then load its labels and states. The answer is the updated workspace,
     * taken at once like [reorderWorkspaces] does; `projects.changed` follows.
     */
    fun saveAutoScanLinearKey() {
        val state = _uiState.value
        val workspace = state.selectedWorkspaceName ?: return
        val key = state.autoScan.linearKeyDraft.trim()
        if (key.isEmpty() || state.autoScan.linearKeySaving) return
        updateAutoScan { it.copy(linearKeySaving = true, linearKeyError = null) }
        viewModelScope.launch {
            try {
                val updated = workspacesRepository.updateWorkspace(workspace, WorkspaceUpdate(linear_api_key = key))
                serverWorkspaces = serverWorkspaces.map { if (it.name == workspace) updated else it }
                _uiState.update {
                    it.copy(
                        workspaces = displayWorkspaces(),
                        autoScan = it.autoScan.copy(
                            linearKeyDraft = "",
                            linearKeyVisible = false,
                            linearKeySaving = false,
                        ),
                    )
                }
                loadLinearMetadata(force = true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val message = when (e) {
                    is WorkspaceConflictException -> e.detail
                    is ApiException -> apiErrorDetail(e.errorBody) ?: "HTTP ${e.code}"
                    else -> e.message ?: "unknown error"
                }
                updateAutoScan { it.copy(linearKeySaving = false, linearKeyError = "Could not save the key: $message") }
            }
        }
    }

    /**
     * Load the Linear labels and states of the selected workspace once per
     * workspace; [force] reloads (the panel's refresh button, a new key).
     */
    fun loadLinearMetadata(force: Boolean = false) {
        val state = _uiState.value
        val workspace = state.selectedWorkspace ?: return
        if (!workspace.has_linear_key) return
        val name = workspace.name
        if (!force && state.autoScan.linearMetadataFor == name) return
        linearMetadataJob?.cancel()
        updateAutoScan {
            it.copy(linearMetadataLoading = true, linearMetadataError = null, linearMetadataFor = name)
        }
        linearMetadataJob = viewModelScope.launch {
            val (labels, states) = coroutineScope {
                val labels = async { linearMetadataRepository.getLabels(name, force) }
                val states = async { linearMetadataRepository.getStates(name, force) }
                labels.await() to states.await()
            }
            updateAutoScanFor(name) {
                it.copy(
                    linearLabels = labels.itemsOrCached(),
                    linearStates = states.itemsOrCached(),
                    linearMetadataLoading = false,
                    linearMetadataError = listOfNotNull(
                        (labels as? MetadataResult.Error)?.message,
                        (states as? MetadataResult.Error)?.message,
                    ).firstOrNull(),
                )
            }
        }
    }

    private fun <T> MetadataResult<T>.itemsOrCached(): List<T> = when (this) {
        is MetadataResult.Cached -> items
        is MetadataResult.Fresh -> items
        is MetadataResult.Error -> cached
    }

    /**
     * Ask the PC what a GitHub scan still needs (`gh` installed, signed in, a
     * GitHub repository). Runs each time the GitHub panel shows, so a return
     * from the setup terminal or the app's return to the foreground re-checks.
     * [userAsked] ("Check again") also reloads the repository's labels.
     */
    fun refreshGitHubStatus(userAsked: Boolean = false) {
        val state = _uiState.value
        val workspace = state.selectedWorkspaceName ?: return
        if (state.autoScanConfigInitializedFor != workspace) return
        githubStatusJob?.cancel()
        updateAutoScan { it.copy(githubStatusLoading = true, githubStatusError = null) }
        githubStatusJob = viewModelScope.launch {
            try {
                val status = autoScanRepository.githubStatus(workspace)
                updateAutoScanFor(workspace) { it.copy(githubStatus = status, githubStatusLoading = false) }
                if (status.problem == null) loadGitHubLabels(workspace, force = userAsked)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                updateAutoScanFor(workspace) {
                    it.copy(
                        githubStatusLoading = false,
                        githubStatusError = errorText(e, "Could not check GitHub on the PC."),
                    )
                }
            }
        }
    }

    private fun loadGitHubLabels(workspace: String, force: Boolean) {
        val current = _uiState.value.autoScan
        if (!force && current.githubLabelsFor == workspace && current.githubLabelsError == null) return
        githubLabelsJob?.cancel()
        updateAutoScanFor(workspace) {
            it.copy(githubLabelsLoading = true, githubLabelsError = null, githubLabelsFor = workspace)
        }
        githubLabelsJob = viewModelScope.launch {
            try {
                val response = autoScanRepository.githubLabels(workspace)
                updateAutoScanFor(workspace) {
                    it.copy(
                        githubLabels = response.labels,
                        githubIssueTypes = response.issue_types,
                        githubLabelsLoading = false,
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                updateAutoScanFor(workspace) {
                    it.copy(
                        githubLabelsLoading = false,
                        githubLabelsError = errorText(e, "Could not load the repository's labels."),
                    )
                }
            }
        }
    }

    fun toggleAutoScanGitHubLabel(name: String) = updateAutoScan {
        val labels = it.githubFilter.labels
        when {
            name in labels -> it.copy(
                githubFilter = it.githubFilter.copy(labels = labels - name),
                githubLabelLimitError = null,
                startError = null,
            )
            labels.size >= GITHUB_LABELS_MAX -> it.copy(githubLabelLimitError = "You can pick up to $GITHUB_LABELS_MAX labels.")
            else -> it.copy(
                githubFilter = it.githubFilter.copy(labels = labels + name),
                githubLabelLimitError = null,
                startError = null,
            )
        }
    }

    fun setAutoScanGitHubLabelQuery(v: String) = updateAutoScan { it.copy(githubLabelQuery = v) }

    /** "Try again" after the repository's labels failed to load. */
    fun retryGitHubLabels() {
        val workspace = _uiState.value.autoScanConfigInitializedFor ?: return
        loadGitHubLabels(workspace, force = true)
    }

    fun toggleAutoScanGitHubIssueType(name: String) = updateAutoScan {
        val types = it.githubFilter.issueTypes
        val chosen = types.any { type -> type.equals(name, ignoreCase = true) }
        when {
            chosen -> it.copy(
                githubFilter = it.githubFilter.copy(
                    issueTypes = types.filterNot { type -> type.equals(name, ignoreCase = true) },
                ),
                startError = null,
            )
            types.size >= GITHUB_ISSUE_TYPES_MAX -> it
            else -> it.copy(githubFilter = it.githubFilter.copy(issueTypes = types + name), startError = null)
        }
    }

    fun setAutoScanGitHubAssignee(v: String) =
        updateAutoScan { it.copy(githubFilter = it.githubFilter.copy(assignee = v), startError = null) }

    fun setAutoScanGitHubActiveLabel(v: String) =
        updateAutoScan { it.copy(githubFilter = it.githubFilter.copy(activeLabel = v), startError = null) }

    /**
     * Open the terminal session that installs ([action] `install`) or signs in
     * (`sign_in`) the GitHub CLI on the PC. It opens on top of the wizard;
     * back from it returns here and the panel re-checks.
     */
    fun startGitHubSetup(action: String) {
        val state = _uiState.value
        val workspace = state.selectedWorkspaceName ?: return
        if (state.autoScan.githubSetupStarting) return
        updateAutoScan { it.copy(githubSetupStarting = true, githubSetupError = null) }
        viewModelScope.launch {
            try {
                val session = sessionRepository.startGitHubSetup(
                    GitHubSetupRequest(workspace_name = workspace, action = action),
                )
                updateAutoScan { it.copy(githubSetupStarting = false) }
                _navigateToSetupSession.trySend(session.session_id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                updateAutoScan {
                    it.copy(
                        githubSetupStarting = false,
                        githubSetupError = errorText(e, "Could not open the terminal on the PC."),
                    )
                }
            }
        }
    }

    /** The agent's `detail` for an HTTP error, else [fallback]. */
    private fun errorText(e: Exception, fallback: String): String =
        (e as? ApiException)?.let { apiErrorDetail(it.errorBody) } ?: fallback

    fun submitAutoScan() {
        val state = _uiState.value
        if (state.autoScanCapability == null) {
            submitLegacyAutoScan(state)
            return
        }
        if (state.autoScanStarting || state.autoScanStartBlocker != null) return
        val workspaceName = state.selectedWorkspaceName ?: return
        val wizard = state.autoScan
        val source = wizard.source ?: return
        val cli = state.autoScanCli ?: return
        val claude = cli == "claude"
        val options = state.autoScanTemplateSettings()
        val request = StartScanRequest(
            workspace_name = workspaceName,
            source = source,
            cli = cli,
            ui = state.autoScanUi,
            interval_minutes = options.interval_minutes,
            initial_prompt = state.initialPrompt,
            claude_options = if (claude) state.autoScanClaudeOptions(initialPrompt = null) else null,
            codex_options = options.codex_options,
            antigravity_options = options.antigravity_options,
            opencode_options = options.opencode_options,
            copilot_options = options.copilot_options,
            cursor_options = options.cursor_options,
            junie_options = options.junie_options,
            pi_options = options.pi_options,
            linear_filter = wizard.linearFilter.toDto().takeIf { source == AUTO_SCAN_SOURCE_LINEAR },
            github_filter = wizard.githubFilter.toDto().takeIf { source == AUTO_SCAN_SOURCE_GITHUB },
        )
        _uiState.update { it.copy(autoScanStarting = true, autoScan = it.autoScan.copy(startError = null)) }
        viewModelScope.launch {
            try {
                autoScanRepository.startScan(request)
                preferencesStore.setLastUsedWorkspaceName(workspaceName)
                preferencesStore.setLastUsedAutoScanOptions(state.toAutoScanLaunchPrefs())
                _uiState.update { it.copy(autoScanStarting = false) }
                _navigateToAutoScan.tryEmit(workspaceName)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val message = when (e) {
                    is AutoScanRejectedException -> e.detail
                    else -> "Failed to start Auto Scan: ${e.message}"
                }
                _uiState.update {
                    it.copy(autoScanStarting = false, autoScan = it.autoScan.copy(startError = message))
                }
            }
        }
    }

    private fun CreateSessionUiState.autoScanClaudeOptions(initialPrompt: String?) = ClaudeOptionsModel(
        plan_mode = autoScanPlanMode,
        no_memory = autoScanNoMemory,
        skip_permissions = autoScanSkipPermissions,
        model = autoScanClaudeModel,
        effort = autoScanClaudeEffort,
        fast_mode = autoScanClaudeFastMode,
        ultracode = autoScanClaudeUltracode,
        debug = autoScanClaudeDebug,
        focus_mode = autoScanFocusMode,
        append_system_prompt = autoScanClaudeAppendSystemPrompt.takeIf { it.isNotBlank() },
        code_mode = autoScanClaudeCodeMode && autoScanUi != AUTO_SCAN_UI_CHAT,
        worktree = true,
        initial_prompt = initialPrompt,
    )

    /** An agent without `capabilities.autoscan`: Linear, Claude Code, the workspace's stored filter. */
    private fun submitLegacyAutoScan(state: CreateSessionUiState) {
        val workspaceName = (state.selection as? WorkspaceSelection.Stored)?.name ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(autoScanStarting = true) }
            try {
                val options = state.autoScanClaudeOptions(
                    initialPrompt = state.initialPrompt.takeIf { it.isNotBlank() },
                )
                autoScanRepository.startScan(workspaceName, options)
                preferencesStore.setLastUsedWorkspaceName(workspaceName)
                preferencesStore.setLastUsedAutoScanOptions(state.toAutoScanLaunchPrefs())
                _uiState.update { it.copy(autoScanStarting = false) }
                _navigateToAutoScan.tryEmit(workspaceName)
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
            CreateSessionStep.OpenCodeConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.CopilotConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.CursorConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.JunieConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.PiConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.AutoScanConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.TerminalConfig -> it.copy(step = CreateSessionStep.ModePick)
            CreateSessionStep.ModePick -> it.copy(
                step = CreateSessionStep.WorkspaceSelect,
                selectedMode = null,
            )
            CreateSessionStep.WorkspaceSelect -> it
        }
    }

    fun toggleNoMemory(v: Boolean) = _uiState.update { it.copy(noMemory = v) }
    fun toggleSkipPermissions(v: Boolean) = _uiState.update { it.copy(skipPermissions = v) }
    fun toggleFocusMode(v: Boolean) = _uiState.update { it.copy(focusMode = v) }
    fun toggleTerminalUi(v: Boolean) = _uiState.update { it.copy(claudeTerminalUi = v) }
    fun toggleClaudeCodeMode(v: Boolean) = _uiState.update { it.copy(claudeCodeMode = v) }

    fun toggleCodexTerminalUi(v: Boolean) = _uiState.update { it.copy(codexTerminalUi = v) }

    fun toggleAntigravityTerminalUi(v: Boolean) = _uiState.update { it.copy(antigravityTerminalUi = v) }

    fun toggleOpenCodeTerminalUi(v: Boolean) = _uiState.update { it.copy(opencodeTerminalUi = v) }

    fun toggleCopilotTerminalUi(v: Boolean) = _uiState.update { it.copy(copilotTerminalUi = v) }

    fun toggleCursorTerminalUi(v: Boolean) = _uiState.update { it.copy(cursorTerminalUi = v) }

    fun toggleJunieTerminalUi(v: Boolean) = _uiState.update { it.copy(junieTerminalUi = v) }

    fun togglePiTerminalUi(v: Boolean) = _uiState.update { it.copy(piTerminalUi = v) }
    fun setPiCodeMode(v: Boolean) = _uiState.update { it.copy(piCodeMode = v) }
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

    fun toggleAutoScanClaudeCodeMode(v: Boolean) =
        _uiState.update { it.copy(autoScanClaudeCodeMode = v, autoScan = it.autoScan.copy(startError = null)) }

    fun setAutoScanClaudeAppendSystemPrompt(v: String) =
        _uiState.update { it.copy(autoScanClaudeAppendSystemPrompt = v) }

    fun toggleWorktree(v: Boolean) {
        _uiState.update { it.copy(worktree = v) }
        ensureBaseBranches()
    }

    /** A pick from the base-branch sheet; null is the agent's default branch. */
    fun selectBaseBranch(ref: String?) = _uiState.update {
        it.copy(baseBranch = ref, baseBranchPicked = true)
    }

    fun retryBaseBranches() = ensureBaseBranches(force = true)

    private var baseBranchesJob: Job? = null

    /** Load the base-branch list once the picker shows, unless it is loaded or loading already. */
    private fun ensureBaseBranches(force: Boolean = false) {
        val state = _uiState.value
        if (!state.showBaseBranchPicker) return
        val workspace = state.selectedWorkspaceName ?: return
        val current = state.baseBranchesWorkspace == workspace &&
            (state.baseBranches is BaseBranchesLoad.Loading || state.baseBranches is BaseBranchesLoad.Loaded)
        if (current && !force) return
        loadBaseBranches(workspace, silent = false)
    }

    /** [silent] keeps the shown list while it reloads and keeps it on a failure. */
    private fun loadBaseBranches(workspace: String, silent: Boolean) {
        baseBranchesJob?.cancel()
        if (!silent) {
            _uiState.update {
                it.copy(baseBranches = BaseBranchesLoad.Loading, baseBranchesWorkspace = workspace)
            }
        }
        baseBranchesJob = viewModelScope.launch {
            val response = try {
                gitRepository.worktreeBases(workspace)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                null
            }
            _uiState.update { state ->
                if (state.baseBranchesWorkspace != workspace) return@update state
                when {
                    response != null -> {
                        val keepPick = state.baseBranchPicked &&
                            (state.baseBranch == null || response.refs.any { it.ref == state.baseBranch })
                        state.copy(
                            baseBranches = BaseBranchesLoad.Loaded(response),
                            baseBranch = if (keepPick) state.baseBranch else response.last_used ?: response.default,
                            baseBranchPicked = keepPick,
                        )
                    }
                    silent -> state
                    else -> state.copy(baseBranches = BaseBranchesLoad.Failed)
                }
            }
        }
    }

    /** Drops the base-branch list and the pick when the selection leaves its workspace. */
    private fun CreateSessionUiState.withBaseBranchesFor(workspace: String?): CreateSessionUiState {
        if (workspace != null && workspace == selectedWorkspaceName) return this
        baseBranchesJob?.cancel()
        return copy(
            baseBranches = BaseBranchesLoad.Idle,
            baseBranchesWorkspace = null,
            baseBranch = null,
            baseBranchPicked = false,
        )
    }
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
        state.copy(
            codexModel = v,
            codexReasoningEffort = codexEffortForModel(v, state.codexReasoningEffort, state.codexModelDetails),
        )
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

    fun setOpenCodeAutoApprove(v: Boolean) = _uiState.update { it.copy(opencodeAutoApprove = v) }
    fun setOpenCodeCodeMode(v: Boolean) = _uiState.update { it.copy(opencodeCodeMode = v) }

    fun setCopilotAllowAll(v: Boolean) = _uiState.update { it.copy(copilotAllowAll = v) }

    // Unlike Antigravity, Allow all and the sandbox combine: Copilot still asks
    // before a command leaves the sandbox.
    fun setCopilotSandbox(v: Boolean) = _uiState.update { it.copy(copilotSandbox = v) }

    fun setCursorForce(v: Boolean) = _uiState.update { it.copy(cursorForce = v) }

    fun setJuniePlanMode(v: Boolean) = _uiState.update { it.copy(juniePlanMode = v) }

    fun setJunieBrave(v: Boolean) = _uiState.update { it.copy(junieBrave = v) }

    fun consumeDismiss() = _uiState.update { it.copy(dismiss = false) }

    fun submit() {
        val state = _uiState.value
        if (state.submitting) return
        val ui = if (state.claudeUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        val claudeOptions = ClaudeOptionsModel(
            no_memory = state.noMemory,
            skip_permissions = state.skipPermissions,
            model = state.claudeModel,
            effort = state.claudeEffort,
            fast_mode = state.claudeFastMode,
            ultracode = state.claudeUltracode,
            debug = state.claudeDebug,
            focus_mode = state.focusMode,
            // Never with the chat view: the agent rejects Code Mode there.
            code_mode = state.claudeCodeMode && !state.claudeUsesChat,
            append_system_prompt = state.claudeAppendSystemPrompt.takeIf { it.isNotBlank() },
            worktree = state.worktree,
            branch_name = state.branchName.takeIf { it.isNotBlank() },
            base_branch = state.effectiveBaseBranch,
        )
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    claude_options = claudeOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        claude_options = claudeOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    /**
     * Continue a stored Claude Code, Codex, Antigravity, OpenCode, Copilot, Cursor, Junie or Pi session ([kind])
     * in a new chat session: the agent's current options without a worktree, in the
     * selected workspace when its folder is the row's folder, otherwise in the
     * row's folder.
     */
    fun resumeChatSession(kind: String, row: ChatStoredSession) {
        val state = _uiState.value
        if (state.submitting) return
        val workspace = (state.selection as? WorkspaceSelection.Stored)?.let { sel ->
            state.workspaces.firstOrNull { it.name == sel.name }
        }?.takeIf { samePath(it.path, row.cwd) }
        val base = CreateSessionRequest(
            workspace_name = workspace?.name,
            cwd = if (workspace == null) row.cwd else null,
            ui = SessionResponse.UI_CHAT,
            chat_resume_id = row.session_id,
        )
        val request = if (kind == "codex") {
            base.copy(cli = "codex", codex_options = state.codexOptions(worktree = false, branchName = null))
        } else if (kind == "antigravity") {
            base.copy(
                cli = "antigravity",
                antigravity_options = state.antigravityOptions(worktree = false, branchName = null),
            )
        } else if (kind == "opencode") {
            base.copy(
                cli = "opencode",
                opencode_options = state.opencodeOptions(worktree = false, branchName = null),
            )
        } else if (kind == "copilot") {
            base.copy(
                cli = "copilot",
                copilot_options = state.copilotOptions(worktree = false, branchName = null),
            )
        } else if (kind == "cursor") {
            base.copy(
                cli = "cursor",
                cursor_options = state.cursorOptions(worktree = false, branchName = null),
            )
        } else if (kind == "junie") {
            base.copy(
                cli = "junie",
                junie_options = state.junieOptions(worktree = false, branchName = null),
            )
        } else if (kind == "pi") {
            base.copy(
                cli = "pi",
                pi_options = state.piOptions(worktree = false, branchName = null),
            )
        } else {
            base.copy(
                cli = "claude",
                claude_options = ClaudeOptionsModel(
                    no_memory = state.noMemory,
                    skip_permissions = state.skipPermissions,
                    model = state.claudeModel,
                    effort = state.claudeEffort,
                    fast_mode = state.claudeFastMode,
                    ultracode = state.claudeUltracode,
                    debug = state.claudeDebug,
                    focus_mode = state.focusMode,
                    append_system_prompt = state.claudeAppendSystemPrompt.takeIf { it.isNotBlank() },
                    worktree = false,
                ),
            )
        }
        submitSession(
            selection = if (workspace != null) WorkspaceSelection.Stored(workspace.name) else WorkspaceSelection.None,
            request = request,
        )
    }

    private fun CreateSessionUiState.codexOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = CodexOptionsModel(
        model = codexModel?.takeIf { it.isNotBlank() },
        sandbox = codexSandbox,
        approval = codexApproval,
        reasoning_effort = codexReasoningEffort,
        full_auto = codexFullAuto,
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun CreateSessionUiState.antigravityOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = AntigravityOptionsModel(
        model = antigravityModel?.takeIf { it.isNotBlank() },
        skip_permissions = antigravitySkipPermissions,
        sandbox = antigravitySandbox,
        add_dirs = parseAntigravityAddDirs(antigravityAddDirsDraft),
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun CreateSessionUiState.opencodeOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = OpenCodeOptionsModel(
        auto_approve = opencodeAutoApprove,
        code_mode = opencodeCodeMode,
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun CreateSessionUiState.copilotOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = CopilotOptionsModel(
        allow_all = copilotAllowAll,
        sandbox = copilotSandbox,
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun CreateSessionUiState.cursorOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = CursorOptionsModel(
        force = cursorForce,
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun CreateSessionUiState.junieOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = JunieOptionsModel(
        plan_mode = juniePlanMode,
        brave = junieBrave,
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun CreateSessionUiState.piOptions(
        worktree: Boolean,
        branchName: String?,
        baseBranch: String? = null,
    ) = PiOptionsModel(
        code_mode = piCodeMode,
        worktree = worktree,
        branch_name = branchName,
        base_branch = baseBranch,
    )

    private fun samePath(a: String, b: String): Boolean =
        a.trimEnd('/', '\\') == b.trimEnd('/', '\\')

    fun submitCodex() {
        val state = _uiState.value
        if (state.submitting) return
        val codexOptions = state.codexOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.codexUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "codex",
                    codex_options = codexOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "codex",
                        codex_options = codexOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun submitAntigravity() {
        val state = _uiState.value
        if (state.submitting || antigravityAddDirsError(state.antigravityAddDirsDraft) != null) return
        val antigravityOptions = state.antigravityOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.antigravityUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "antigravity",
                    antigravity_options = antigravityOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "antigravity",
                        antigravity_options = antigravityOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun submitOpenCode() {
        val state = _uiState.value
        if (state.submitting) return
        val opencodeOptions = state.opencodeOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.opencodeUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "opencode",
                    opencode_options = opencodeOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "opencode",
                        opencode_options = opencodeOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    /**
     * Starts a terminal session that runs OpenCode's own sign-in in the chosen
     * folder. It is not an OpenCode session: no launch option applies.
     */
    fun connectOpenCodeProvider() {
        val state = _uiState.value
        if (state.submitting) return
        val request = CreateSessionRequest(
            cli = "opencode",
            opencode_options = OpenCodeOptionsModel(
                auto_approve = false,
                connect = true,
                worktree = false,
                branch_name = null,
                base_branch = null,
            ),
            ui = SessionResponse.UI_TERMINAL,
        )
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = request.copy(workspace_name = sel.name),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = request.copy(workspace_name = null, cwd = sel.path),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun submitCopilot() {
        val state = _uiState.value
        if (state.submitting) return
        val copilotOptions = state.copilotOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.copilotUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "copilot",
                    copilot_options = copilotOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "copilot",
                        copilot_options = copilotOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun submitCursor() {
        val state = _uiState.value
        if (state.submitting) return
        val cursorOptions = state.cursorOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.cursorUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "cursor",
                    cursor_options = cursorOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "cursor",
                        cursor_options = cursorOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun submitJunie() {
        val state = _uiState.value
        if (state.submitting) return
        val junieOptions = state.junieOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.junieUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "junie",
                    junie_options = junieOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "junie",
                        junie_options = junieOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun submitPi() {
        val state = _uiState.value
        if (state.submitting) return
        val piOptions = state.piOptions(
            worktree = state.worktree,
            branchName = state.branchName.takeIf { it.isNotBlank() },
            baseBranch = state.effectiveBaseBranch,
        )
        val ui = if (state.piUsesChat) SessionResponse.UI_CHAT else SessionResponse.UI_TERMINAL
        when (val sel = state.selection) {
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    cli = "pi",
                    pi_options = piOptions,
                    ui = ui,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        cli = "pi",
                        pi_options = piOptions.copy(worktree = false, branch_name = null, base_branch = null),
                        ui = ui,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
        }
    }

    fun setCodexApiKeyDraft(v: String) = _uiState.update {
        it.copy(codexApiKeyDraft = v, codexLoginError = null)
    }

    fun toggleCodexApiKeyVisible() = _uiState.update {
        it.copy(codexApiKeyVisible = !it.codexApiKeyVisible)
    }

    /** Closes the Codex login dialog and wipes the typed key from memory. */
    fun dismissCodexLogin() {
        pendingCodexResume = null
        _uiState.update {
            it.copy(
                codexLoginVisible = false,
                codexApiKeyDraft = "",
                codexApiKeyVisible = false,
                codexLoginSubmitting = false,
                codexLoginError = null,
            )
        }
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
                // A "Continue a session" retries that same request, not a new session.
                val resume = pendingCodexResume
                pendingCodexResume = null
                if (resume != null) submitSession(resume.first, resume.second) else submitCodex()
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
            is WorkspaceSelection.Stored -> submitSession(
                selection = sel,
                request = CreateSessionRequest(
                    workspace_name = sel.name,
                    command = null,
                    cli = cli,
                    claude_options = null,
                    worktree = state.worktree,
                    branch_name = state.branchName.takeIf { it.isNotBlank() },
                    base_branch = state.effectiveBaseBranch,
                ),
            )
            is WorkspaceSelection.CustomPath -> {
                if (sel.path.isBlank() || state.customPathError != null) return
                submitSession(
                    selection = sel,
                    request = CreateSessionRequest(
                        workspace_name = null,
                        cwd = sel.path,
                        command = null,
                        cli = cli,
                        claude_options = null,
                        worktree = false,
                        branch_name = null,
                    ),
                )
            }
            WorkspaceSelection.None -> Unit
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

    /** The Codex resume that hit `codex_auth_required`, re-sent after the sign-in. */
    private var pendingCodexResume: Pair<WorkspaceSelection, CreateSessionRequest>? = null

    private fun submitSession(selection: WorkspaceSelection, request: CreateSessionRequest) {
        viewModelScope.launch {
            _uiState.update { it.copy(submitting = true) }
            try {
                val response = sessionRepository.createSession(request)
                when (selection) {
                    is WorkspaceSelection.Stored -> {
                        preferencesStore.setLastUsedWorkspaceName(selection.name)
                    }
                    is WorkspaceSelection.CustomPath -> {
                        val scope = agentScopeKey()
                        if (scope != null) {
                            preferencesStore.addRecentCustomPath(scope, selection.path)
                        }
                    }
                    WorkspaceSelection.None -> Unit
                }
                // Remember the per-CLI launch controls for the next session of
                // the same kind. The request shape selects which blob to write:
                // Claude submits carry claude_options, Codex submits carry
                // codex_options, Antigravity submits carry antigravity_options,
                // OpenCode submits carry opencode_options (a provider sign-in
                // is not a launch, so it stores nothing),
                // Copilot submits carry copilot_options,
                // Cursor submits carry cursor_options,
                // Junie submits carry junie_options,
                // Pi submits carry pi_options,
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
                if (request.opencode_options?.connect == false) {
                    preferencesStore.setLastUsedOpenCodeOptions(_uiState.value.toOpenCodeLaunchPrefs())
                }
                if (request.copilot_options != null) {
                    preferencesStore.setLastUsedCopilotOptions(_uiState.value.toCopilotLaunchPrefs())
                }
                if (request.cursor_options != null) {
                    preferencesStore.setLastUsedCursorOptions(_uiState.value.toCursorLaunchPrefs())
                }
                if (request.junie_options != null) {
                    preferencesStore.setLastUsedJunieOptions(_uiState.value.toJunieLaunchPrefs())
                }
                if (request.pi_options != null) {
                    preferencesStore.setLastUsedPiOptions(_uiState.value.toPiLaunchPrefs())
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
                    pendingCodexResume = (selection to request).takeIf { request.chat_resume_id != null }
                    _uiState.update {
                        it.copy(submitting = false, codexLoginVisible = true, codexLoginError = null)
                    }
                } else if (e is ApiException && isChatUnavailable(e.errorBody)) {
                    val detail = chatUnavailableDetail(e.errorBody)?.trim()?.trimEnd('.')?.takeIf { it.isNotEmpty() }
                        ?: "The chat view cannot start on your PC"
                    _uiState.update {
                        it.copy(
                            submitting = false,
                            error = "$detail. Choose Terminal under Open sessions in to use the classic terminal.",
                        )
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
                    // workspace (agent-side "Workspace 'X' not found"), which must
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

    /** The `chat_unavailable` 503: flat through the relay, nested under `detail` on direct REST. */
    private fun chatUnavailableBody(body: String): JsonObject? = runCatching {
        val obj = Json.parseToJsonElement(body).jsonObject
        when {
            obj["chat_unavailable"]?.jsonPrimitive?.booleanOrNull == true -> obj
            (obj["detail"] as? JsonObject)?.get("chat_unavailable")?.jsonPrimitive?.booleanOrNull == true ->
                obj["detail"] as JsonObject
            else -> null
        }
    }.getOrNull()

    private fun isChatUnavailable(body: String): Boolean = chatUnavailableBody(body) != null

    private fun chatUnavailableDetail(body: String): String? =
        chatUnavailableBody(body)?.get("detail")?.jsonPrimitive?.contentOrNull

    /**
     * True when an error body carries the `agent_update_required` marker the
     * relay sends (409, top-level) for custom-type launches against an agent
     * whose tunnel negotiated protocol < 7.
     */
    private fun isAgentUpdateRequired(body: String): Boolean = runCatching {
        Json.parseToJsonElement(body)
            .jsonObject["agent_update_required"]?.jsonPrimitive?.booleanOrNull == true
    }.getOrDefault(false)

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

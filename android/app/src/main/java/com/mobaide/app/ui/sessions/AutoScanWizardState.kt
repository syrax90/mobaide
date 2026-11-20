package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_CHAT
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_TERMINAL
import com.mobaide.app.data.network.dto.AntigravityScanOptionsDto
import com.mobaide.app.data.network.dto.AutoScanFilterDraftDto
import com.mobaide.app.data.network.dto.AutoScanFilterDto
import com.mobaide.app.data.network.dto.AutoScanSettingsDto
import com.mobaide.app.data.network.dto.AutoScanTemplateDto
import com.mobaide.app.data.network.dto.AutoScanTemplateSettingsDto
import com.mobaide.app.data.network.dto.CliModelDetail
import com.mobaide.app.data.network.dto.CodexScanOptionsDto
import com.mobaide.app.data.network.dto.CopilotScanOptionsDto
import com.mobaide.app.data.network.dto.CursorScanOptionsDto
import com.mobaide.app.data.network.dto.GitHubIssueTypeDto
import com.mobaide.app.data.network.dto.GitHubLabelDto
import com.mobaide.app.data.network.dto.GitHubScanFilterDto
import com.mobaide.app.data.network.dto.GitHubStatusDto
import com.mobaide.app.data.network.dto.JunieScanOptionsDto
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import com.mobaide.app.data.network.dto.OpenCodeScanOptionsDto
import com.mobaide.app.ui.autoscan.autoScanAgentName
import com.mobaide.app.ui.autoscan.autoScanSourceName
import com.mobaide.app.ui.common.LinearFilterErrors

/** The Linear filter as the wizard edits it; empty fields mean "not chosen yet". */
data class LinearFilterDraft(
    val triggerStates: Set<String> = emptySet(),
    val triggerLabels: Set<String> = emptySet(),
    val activeState: String = "",
    val doneState: String = "",
    val removalLabel: String = "",
) {
    val isValid: Boolean
        get() = triggerStates.isNotEmpty() &&
            triggerLabels.isNotEmpty() &&
            activeState.isNotBlank() &&
            doneState.isNotBlank() &&
            removalLabel.isNotBlank()

    val errors: LinearFilterErrors
        get() = LinearFilterErrors(
            triggerLabels = "Select at least one trigger label".takeIf { triggerLabels.isEmpty() },
            triggerStates = "Select at least one trigger state".takeIf { triggerStates.isEmpty() },
            activeState = "Active state is required".takeIf { activeState.isBlank() },
            doneState = "Done state is required".takeIf { doneState.isBlank() },
            removalLabel = "Removal label is required".takeIf { removalLabel.isBlank() },
        )

    fun toDto(): AutoScanFilterDto = AutoScanFilterDto(
        state_names = triggerStates.toList(),
        label_names = triggerLabels.toList(),
        active_state = activeState.trim(),
        done_state = doneState.trim(),
        removal_label = removalLabel.trim(),
    )

    fun toDraftDto(): AutoScanFilterDraftDto = AutoScanFilterDraftDto(
        state_names = triggerStates.toList(),
        label_names = triggerLabels.toList(),
        active_state = activeState.trim(),
        done_state = doneState.trim(),
        removal_label = removalLabel.trim(),
    )

    companion object {
        fun from(dto: AutoScanFilterDto?): LinearFilterDraft = dto?.let { from(it.toDraft()) } ?: LinearFilterDraft()

        fun from(dto: AutoScanFilterDraftDto): LinearFilterDraft = LinearFilterDraft(
            triggerStates = dto.state_names.toSet(),
            triggerLabels = dto.label_names.toSet(),
            activeState = dto.active_state,
            doneState = dto.done_state,
            removalLabel = dto.removal_label,
        )
    }
}

private fun AutoScanFilterDto.toDraft() = AutoScanFilterDraftDto(
    state_names = state_names,
    label_names = label_names,
    active_state = active_state,
    done_state = done_state,
    removal_label = removal_label,
)

/** The GitHub filter as the wizard edits it; [issueTypes] empty takes every type. */
data class GitHubFilterDraft(
    val labels: List<String> = emptyList(),
    val assignee: String = "",
    val activeLabel: String = "",
    val issueTypes: List<String> = emptyList(),
) {
    val activeLabelError: String?
        get() {
            val active = activeLabel.trim()
            if (active.isEmpty()) return null
            if (labels.any { it.equals(active, ignoreCase = true) }) {
                return "The label to add must not be one of the labels to pick up."
            }
            return githubLabelError(active)
        }

    val assigneeError: String?
        get() = "At most $GITHUB_LABEL_MAX characters".takeIf { assignee.trim().length > GITHUB_LABEL_MAX }

    val isValid: Boolean
        get() = labels.size in 1..GITHUB_LABELS_MAX && activeLabelError == null && assigneeError == null

    fun toDto(): GitHubScanFilterDto = GitHubScanFilterDto(
        labels = labels,
        assignee = assignee.trim().ifEmpty { null },
        active_label = activeLabel.trim().ifEmpty { null },
        issue_types = issueTypes,
    )

    companion object {
        fun from(dto: GitHubScanFilterDto?): GitHubFilterDraft = dto?.let {
            GitHubFilterDraft(
                labels = it.labels,
                assignee = it.assignee.orEmpty(),
                activeLabel = it.active_label.orEmpty(),
                issueTypes = it.issue_types,
            )
        } ?: GitHubFilterDraft()
    }
}

/**
 * Codex's Auto Scan options. Defaults run without asking (Full access), as
 * the agent does for a start that names no options. A null
 * [reasoningEffort] is "Agent default": Codex uses its own setting.
 */
data class CodexScanBucket(
    val model: String? = null,
    val sandbox: String = "workspace-write",
    val approval: String = "never",
    val reasoningEffort: String? = "high",
    val fullAuto: Boolean = true,
) {
    val preset: CodexAutonomyPreset? get() = codexPresetOf(sandbox, approval, fullAuto)

    fun toDto() = CodexScanOptionsDto(
        model = model,
        sandbox = sandbox,
        approval = approval,
        reasoning_effort = reasoningEffort,
        full_auto = fullAuto,
    )

    fun withPreset(preset: CodexAutonomyPreset): CodexScanBucket = when (preset) {
        CodexAutonomyPreset.ReadOnly -> copy(sandbox = "read-only", approval = "on-request", fullAuto = false)
        CodexAutonomyPreset.Auto -> copy(sandbox = "workspace-write", approval = "on-request", fullAuto = false)
        CodexAutonomyPreset.FullAccess -> copy(fullAuto = true)
    }

    fun applied(dto: CodexScanOptionsDto) = CodexScanBucket(
        model = dto.model,
        sandbox = dto.sandbox,
        approval = dto.approval,
        reasoningEffort = dto.reasoning_effort,
        fullAuto = dto.full_auto,
    )
}

/** Antigravity's Auto Scan options; [skipPermissions] and [sandbox] exclude each other. */
data class AntigravityScanBucket(
    val model: String? = null,
    val skipPermissions: Boolean = true,
    val sandbox: Boolean = false,
    val addDirsDraft: String = "",
) {
    val addDirsError: String? get() = antigravityAddDirsError(addDirsDraft)

    fun toDto() = AntigravityScanOptionsDto(
        model = model,
        skip_permissions = skipPermissions,
        sandbox = sandbox,
        add_dirs = parseAntigravityAddDirs(addDirsDraft),
    )

    companion object {
        fun from(dto: AntigravityScanOptionsDto) = AntigravityScanBucket(
            model = dto.model,
            skipPermissions = dto.skip_permissions,
            sandbox = dto.sandbox && !dto.skip_permissions,
            addDirsDraft = dto.add_dirs.joinToString("\n"),
        )
    }
}

/**
 * The Auto Scan options of the agents other than Claude Code, apart from the
 * normal-session ones. Defaults run without asking, as the agent does for a
 * start that names no options. Code Mode defaults match the new-session wizard.
 */
data class AutoScanAgentBuckets(
    val codex: CodexScanBucket = CodexScanBucket(),
    val antigravity: AntigravityScanBucket = AntigravityScanBucket(),
    val opencodeAutoApprove: Boolean = true,
    val opencodeCodeMode: Boolean = true,
    val copilotAllowAll: Boolean = true,
    val cursorForce: Boolean = true,
    val juniePlanMode: Boolean = false,
    val junieBrave: Boolean = true,
    val piCodeMode: Boolean = false,
)

/** The additional-directories draft as a list: one per line, or comma-separated. */
fun parseAntigravityAddDirs(draft: String): List<String> =
    draft.split('\n', ',').map { it.trim() }.filter { it.isNotEmpty() }

/** The agent refuses a flag-shaped directory (it could smuggle a blocked flag). */
fun antigravityAddDirsError(draft: String): String? =
    "Directories must not start with “-”.".takeIf { parseAntigravityAddDirs(draft).any { it.startsWith("-") } }

/**
 * The Codex effort to keep when the model changes: the same clamp as the
 * Claude model switch, targeting the catalog's per-model default. A model
 * without a catalog entry (or the null Default model) keeps [effort] — its
 * capabilities are unknown, so never downshift on a guess.
 */
fun codexEffortForModel(model: String?, effort: String, details: List<CliModelDetail>?): String {
    val detail = model?.let { m -> details?.firstOrNull { it.id == m } } ?: return effort
    if (effort in detail.efforts) return effort
    return detail.defaultEffort ?: "high".takeIf { it in detail.efforts } ?: detail.efforts.first()
}

/** The autonomy preset the raw Codex fields match, or null for a custom combination. */
fun codexPresetOf(sandbox: String, approval: String, fullAuto: Boolean): CodexAutonomyPreset? = when {
    fullAuto -> CodexAutonomyPreset.FullAccess
    sandbox == "read-only" && approval == "on-request" -> CodexAutonomyPreset.ReadOnly
    sandbox == "workspace-write" && approval == "on-request" -> CodexAutonomyPreset.Auto
    else -> null
}

/** The workspace's saved Auto Scan choices as template settings, so one function applies both. */
fun AutoScanSettingsDto.asTemplateSettings(prompt: String?) = AutoScanTemplateSettingsDto(
    source = source,
    cli = cli,
    ui = ui,
    interval_minutes = interval_minutes,
    initial_prompt = prompt,
    linear_filter = linear_filter?.toDraft(),
    github_filter = github_filter,
    claude_options = claude_options,
    codex_options = codex_options,
    antigravity_options = antigravity_options,
    opencode_options = opencode_options,
    copilot_options = copilot_options,
    cursor_options = cursor_options,
    junie_options = junie_options,
    pi_options = pi_options,
)

/** The template's buckets with each option object it carries taking over its agent's bucket. */
fun AutoScanAgentBuckets.applied(settings: AutoScanTemplateSettingsDto) = AutoScanAgentBuckets(
    codex = settings.codex_options?.let(codex::applied) ?: codex,
    antigravity = settings.antigravity_options?.let(AntigravityScanBucket::from) ?: antigravity,
    opencodeAutoApprove = settings.opencode_options?.auto_approve ?: opencodeAutoApprove,
    opencodeCodeMode = settings.opencode_options?.code_mode ?: opencodeCodeMode,
    copilotAllowAll = settings.copilot_options?.allow_all ?: copilotAllowAll,
    cursorForce = settings.cursor_options?.force ?: cursorForce,
    juniePlanMode = settings.junie_options?.plan_mode ?: juniePlanMode,
    junieBrave = settings.junie_options?.brave ?: junieBrave,
    piCodeMode = settings.pi_options?.code_mode ?: piCodeMode,
)

/** "GitHub · Codex": a template's source and agent, for its picker entry. */
val AutoScanTemplateDto.summary: String
    get() = listOfNotNull(
        settings.source?.let(::autoScanSourceName),
        settings.cli?.let(::autoScanAgentName),
    ).joinToString(" · ").ifEmpty { "No source or agent chosen" }

/** Template names match ignoring case and runs of white space, as on the agent. */
fun templateNameKey(name: String): String = name.trim().split(Regex("\\s+")).joinToString(" ").lowercase()

const val TEMPLATE_NAME_MAX = 60

/** The PC's Auto Scan templates as the wizard shows them, kept across workspace re-seeds. */
data class AutoScanTemplatesUi(
    val templates: List<AutoScanTemplateDto> = emptyList(),
    val loading: Boolean = false,
    val loadError: String? = null,
    // The list came from the PC at least once, so a name clash can be told apart.
    val loaded: Boolean = false,
    val saveDialogOpen: Boolean = false,
    val saving: Boolean = false,
    val saveError: String? = null,
    val deleteError: String? = null,
    // The template the step's choices came from, for the picker and the Save dialog.
    val appliedName: String? = null,
    // "Applied “x”." / "Saved “x”." after the last action.
    val message: String? = null,
) {
    /** "Save as template…" is offered: the list is loaded, so a name clash can be shown. */
    val canSave: Boolean get() = loaded && !loading && loadError == null && !saving
}

/** The waits a scan offers between cycles, in minutes; the agent takes any whole minute in range. */
val AUTO_SCAN_INTERVALS = listOf(1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 1440)
const val AUTO_SCAN_INTERVAL_DEFAULT = 5
val AUTO_SCAN_INTERVAL_RANGE = 1..1440

/** "5 min", "1 hour", "24 hours", "45 min". */
fun autoScanIntervalLabel(minutes: Int): String = when {
    minutes >= 60 && minutes % 60 == 0 -> (minutes / 60).let { if (it == 1) "1 hour" else "$it hours" }
    else -> "$minutes min"
}

/** A valid session view, or null for a value this client does not know. */
fun autoScanUiOrNull(value: String?): String? = value?.takeIf { it == AUTO_SCAN_UI_TERMINAL || it == AUTO_SCAN_UI_CHAT }

const val GITHUB_LABELS_MAX = 20
const val GITHUB_ISSUE_TYPES_MAX = 20
const val GITHUB_LABEL_MAX = 100
const val AUTO_SCAN_PROMPT_MAX = 32_000

/** Why [name] (trimmed) cannot be a GitHub label of the filter, or null when it can. */
fun githubLabelError(name: String): String? = when {
    name.isEmpty() -> "Enter a label name."
    // gh splits a label option's value on commas.
    ',' in name -> "A label name cannot contain a comma."
    name.length > GITHUB_LABEL_MAX -> "A label name can have at most $GITHUB_LABEL_MAX characters."
    else -> null
}

/**
 * The Auto Scan step's choices and the source data they need, for the
 * workspace the step was seeded for. Survives a trip to the GitHub setup
 * terminal because the wizard stays on the back stack.
 */
data class AutoScanWizardState(
    val source: String? = null,
    val cli: String? = null,
    val agents: AutoScanAgentBuckets = AutoScanAgentBuckets(),
    // The session view the user chose; Terminal is sent while the agent has no chat view.
    val ui: String = AUTO_SCAN_UI_CHAT,
    val intervalMinutes: Int = AUTO_SCAN_INTERVAL_DEFAULT,
    val linearFilter: LinearFilterDraft = LinearFilterDraft(),
    val linearFilterTouched: Boolean = false,
    val linearLabels: List<LinearLabelDto> = emptyList(),
    val linearStates: List<LinearStateDto> = emptyList(),
    val linearMetadataLoading: Boolean = false,
    val linearMetadataError: String? = null,
    // The workspace the Linear labels and states were loaded for.
    val linearMetadataFor: String? = null,
    val linearKeyDraft: String = "",
    val linearKeyVisible: Boolean = false,
    val linearKeySaving: Boolean = false,
    val linearKeyError: String? = null,
    // null until the first answer; kept on screen while a re-check runs.
    val githubStatus: GitHubStatusDto? = null,
    val githubStatusLoading: Boolean = false,
    val githubStatusError: String? = null,
    val githubLabels: List<GitHubLabelDto> = emptyList(),
    // The repository's GitHub issue types, read with its labels.
    val githubIssueTypes: List<GitHubIssueTypeDto> = emptyList(),
    val githubLabelsLoading: Boolean = false,
    val githubLabelsError: String? = null,
    // The workspace the repository labels were loaded for.
    val githubLabelsFor: String? = null,
    val githubFilter: GitHubFilterDraft = GitHubFilterDraft(),
    // Filters the repository's labels; never adds a name.
    val githubLabelQuery: String = "",
    val githubLabelLimitError: String? = null,
    val githubSetupStarting: Boolean = false,
    val githubSetupError: String? = null,
    // The agent's answer to the last failed start, shown above the Start button.
    val startError: String? = null,
) {
    /** A GitHub scan can start: `gh` is installed, signed in and sees the repository. */
    val githubReady: Boolean
        get() = githubStatus != null && githubStatus.problem == null
}

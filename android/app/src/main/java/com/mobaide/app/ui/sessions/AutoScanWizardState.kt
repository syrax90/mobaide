package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.AutoScanFilterDto
import com.mobaide.app.data.network.dto.GitHubLabelDto
import com.mobaide.app.data.network.dto.GitHubScanFilterDto
import com.mobaide.app.data.network.dto.GitHubStatusDto
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
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

    companion object {
        fun from(dto: AutoScanFilterDto?): LinearFilterDraft = dto?.let {
            LinearFilterDraft(
                triggerStates = it.state_names.toSet(),
                triggerLabels = it.label_names.toSet(),
                activeState = it.active_state,
                doneState = it.done_state,
                removalLabel = it.removal_label,
            )
        } ?: LinearFilterDraft()
    }
}

/** The GitHub filter as the wizard edits it. */
data class GitHubFilterDraft(
    val labels: List<String> = emptyList(),
    val assignee: String = "",
    val activeLabel: String = "",
) {
    val activeLabelError: String?
        get() = githubLabelError(activeLabel.trim()).takeIf { activeLabel.isNotBlank() }

    val assigneeError: String?
        get() = "At most $GITHUB_LABEL_MAX characters".takeIf { assignee.trim().length > GITHUB_LABEL_MAX }

    val isValid: Boolean
        get() = labels.size in 1..GITHUB_LABELS_MAX && activeLabelError == null && assigneeError == null

    fun toDto(): GitHubScanFilterDto = GitHubScanFilterDto(
        labels = labels,
        assignee = assignee.trim().ifEmpty { null },
        active_label = activeLabel.trim().ifEmpty { null },
    )

    companion object {
        fun from(dto: GitHubScanFilterDto?): GitHubFilterDraft = dto?.let {
            GitHubFilterDraft(
                labels = it.labels,
                assignee = it.assignee.orEmpty(),
                activeLabel = it.active_label.orEmpty(),
            )
        } ?: GitHubFilterDraft()
    }
}

const val GITHUB_LABELS_MAX = 20
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
    // Remembered on the device, not per workspace.
    val autoApprove: Boolean = true,
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
    val githubLabelsLoading: Boolean = false,
    val githubLabelsError: String? = null,
    // The workspace the repository labels were loaded for.
    val githubLabelsFor: String? = null,
    val githubFilter: GitHubFilterDraft = GitHubFilterDraft(),
    val githubLabelDraft: String = "",
    val githubLabelDraftError: String? = null,
    val githubSetupStarting: Boolean = false,
    val githubSetupError: String? = null,
    // The agent's answer to the last failed start, shown above the Start button.
    val startError: String? = null,
) {
    /** A GitHub scan can start: `gh` is installed, signed in and sees the repository. */
    val githubReady: Boolean
        get() = githubStatus != null && githubStatus.problem == null
}

package com.mobaide.app.ui.projects

import com.mobaide.app.data.network.dto.AutoScanFilterDto
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.ProjectUpdate
import com.mobaide.app.data.network.dto.ProjectWrite
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull

enum class FlagType { STRING, BOOLEAN, NUMBER }

/** One row in the flags editor. `textValue` holds the raw text; for BOOLEAN
 *  we use `"true"`/`"false"` and render via Switch. */
data class FlagEntry(
    val key: String = "",
    val type: FlagType = FlagType.STRING,
    val textValue: String = "",
) {
    val keyValid: Boolean get() = key.isNotBlank()

    val valueValid: Boolean
        get() = when (type) {
            FlagType.STRING -> true
            FlagType.BOOLEAN -> textValue == "true" || textValue == "false"
            FlagType.NUMBER -> textValue.isNotBlank() &&
                (textValue.toLongOrNull() != null || textValue.toDoubleOrNull() != null)
        }

    fun toJsonElement(): JsonElement = when (type) {
        FlagType.STRING -> JsonPrimitive(textValue)
        FlagType.BOOLEAN -> JsonPrimitive(textValue == "true")
        FlagType.NUMBER -> textValue.toLongOrNull()?.let(::JsonPrimitive)
            ?: JsonPrimitive(textValue.toDoubleOrNull() ?: 0.0)
    }

    companion object {
        /**
         * Best-effort inference of a wire [JsonElement] into a UI row.
         *
         * Important ordering gotcha: `JsonPrimitive.booleanOrNull` inspects
         * the primitive's CONTENT, so for a JSON string `"true"` it returns
         * `true` — which would misclassify a string as a boolean. We check
         * `isString` first to distinguish JSON strings from JSON booleans
         * / numbers.
         *
         * Non-primitives (null / object / array) fall back to STRING with
         * the raw JSON text so power-user flags at least survive a round
         * trip.
         */
        fun fromJsonElement(key: String, element: JsonElement): FlagEntry {
            val primitive = (element as? JsonPrimitive)
                ?: return FlagEntry(key, FlagType.STRING, element.toString())
            return when {
                primitive.isString ->
                    FlagEntry(key, FlagType.STRING, primitive.contentOrNull.orEmpty())
                primitive.booleanOrNull != null ->
                    FlagEntry(key, FlagType.BOOLEAN, if (primitive.booleanOrNull == true) "true" else "false")
                primitive.longOrNull != null ->
                    FlagEntry(key, FlagType.NUMBER, primitive.longOrNull!!.toString())
                primitive.doubleOrNull != null ->
                    FlagEntry(key, FlagType.NUMBER, primitive.doubleOrNull!!.toString())
                else ->
                    FlagEntry(key, FlagType.STRING, primitive.contentOrNull.orEmpty())
            }
        }
    }
}

/**
 * State for the create/edit bottom sheet. Errors are surfaced as computed
 * `val` getters — they only "display" (non-null) after the field is
 * touched OR the user has attempted submit.
 */
data class ProjectFormState(
    // null = create mode; non-null = edit mode whose `name` is the PUT target.
    val editing: ProjectInfo? = null,

    val name: String = "",
    val path: String = "",
    val condaEnv: String = "",
    val linearApiKey: String = "",
    val linearKeyVisible: Boolean = false,
    val clearLinearKey: Boolean = false,

    val flags: List<FlagEntry> = emptyList(),
    val flagsDirty: Boolean = false,

    // Auto Scan filter selections — multi-select sets for trigger labels &
    // states, single-string for active/done/removal. Dirty flag mirrors the
    // `flagsDirty` pattern: only send the field on the wire if the user
    // actually edited the form.
    val triggerLabels: Set<String> = setOf("AutoPipeline"),
    val triggerStates: Set<String> = setOf("Proposals"),
    val activeState: String = "In Progress",
    val doneState: String = "Done",
    val removalLabel: String = "AutoPipeline",
    val filterDirty: Boolean = false,

    // Discovery results from the Linear discovery endpoints. Populated
    // asynchronously after the sheet opens — until then, the chip groups
    // render only the currently-selected entries (so the form is editable
    // even before the network fetch returns).
    val availableLabels: List<LinearLabelDto> = emptyList(),
    val availableStates: List<LinearStateDto> = emptyList(),
    val filterMetadataLoading: Boolean = false,
    val filterMetadataError: String? = null,

    val touched: Set<Field> = emptySet(),
    val submitAttempted: Boolean = false,
    val submitting: Boolean = false,
    val nameServerError: String? = null,
    /**
     * Server-side or transport error from the most recent save attempt
     * (anything other than the 409-name-conflict that goes to
     * [nameServerError]). Lives in StateFlow-backed form state — not on
     * the lossy events SharedFlow — so a save failure is *guaranteed*
     * visible while the sheet is open, regardless of collector timing.
     * Cleared on the next field edit.
     */
    val submitError: String? = null,
) {
    enum class Field { NAME, PATH, CONDA, KEY, FLAG, FILTER }

    val isEdit: Boolean get() = editing != null

    /** Whether the Auto Scan filter section should render at all. The filter
     *  is only meaningful for projects with a Linear API key; otherwise the
     *  scanner cannot start regardless of the filter values. */
    val showAutoScanFilterSection: Boolean
        get() = linearApiKey.isNotBlank() ||
            clearLinearKey.not() && (editing?.has_linear_key == true)

    private fun fieldVisible(field: Field): Boolean =
        submitAttempted || field in touched

    val nameError: String? get() {
        if (nameServerError != null) return nameServerError
        if (!fieldVisible(Field.NAME)) return null
        return when {
            name.isBlank() -> "Name is required"
            !name.matches(VALID_NAME) -> "Use letters, digits, spaces, _-."
            else -> null
        }
    }

    val pathError: String? get() =
        if (fieldVisible(Field.PATH) && path.isBlank()) "Path is required" else null

    /** Per-index errors for the flags list. Duplicate keys mark the SECOND offender. */
    val flagKeyErrors: Map<Int, String>
        get() {
            val visible = fieldVisible(Field.FLAG)
            val errors = mutableMapOf<Int, String>()
            val seen = mutableSetOf<String>()
            flags.forEachIndexed { idx, row ->
                if (visible && row.key.isBlank()) {
                    errors[idx] = "Key required"
                } else if (row.key.isNotBlank()) {
                    if (!seen.add(row.key)) errors[idx] = "Duplicate key"
                }
            }
            return errors
        }

    val flagValueErrors: Map<Int, String>
        get() {
            val visible = fieldVisible(Field.FLAG)
            if (!visible) return emptyMap()
            val errors = mutableMapOf<Int, String>()
            flags.forEachIndexed { idx, row ->
                if (!row.valueValid) {
                    errors[idx] = when (row.type) {
                        FlagType.NUMBER -> "Not a number"
                        FlagType.BOOLEAN -> "Expected true/false"
                        FlagType.STRING -> "Invalid value"
                    }
                }
            }
            return errors
        }

    /** "Select at least one trigger label" — null until the user has touched
     *  the section or attempted submit, mirroring the name/path pattern. */
    val triggerLabelsError: String? get() =
        if (fieldVisible(Field.FILTER) && triggerLabels.isEmpty())
            "Select at least one trigger label"
        else null

    val triggerStatesError: String? get() =
        if (fieldVisible(Field.FILTER) && triggerStates.isEmpty())
            "Select at least one trigger state"
        else null

    val activeStateError: String? get() =
        if (fieldVisible(Field.FILTER) && activeState.isBlank())
            "Active state is required"
        else null

    val doneStateError: String? get() =
        if (fieldVisible(Field.FILTER) && doneState.isBlank())
            "Done state is required"
        else null

    val removalLabelError: String? get() =
        if (fieldVisible(Field.FILTER) && removalLabel.isBlank())
            "Removal label is required"
        else null

    private val filterValid: Boolean
        get() = triggerLabels.isNotEmpty() &&
            triggerStates.isNotEmpty() &&
            activeState.isNotBlank() &&
            doneState.isNotBlank() &&
            removalLabel.isNotBlank()

    val canSave: Boolean
        get() = !submitting
            && name.isNotBlank()
            && path.isNotBlank()
            && name.matches(VALID_NAME)
            && flags.all { it.keyValid && it.valueValid }
            && flags.distinctBy { it.key }.size == flags.size
            && nameServerError == null
            && filterValid

    private fun currentFilterDto(): AutoScanFilterDto = AutoScanFilterDto(
        state_names = triggerStates.toList(),
        label_names = triggerLabels.toList(),
        active_state = activeState.trim(),
        done_state = doneState.trim(),
        removal_label = removalLabel.trim(),
    )

    fun toCreateBody(): ProjectWrite = ProjectWrite(
        name = name.trim(),
        path = path.trim(),
        linear_api_key = linearApiKey.ifBlank { null },
        conda_env = condaEnv.ifBlank { null },
        default_flags = flags.associate { it.key to it.toJsonElement() },
        auto_scan_filter = currentFilterDto(),
    )

    /**
     * Build a PUT body honouring three-state semantics. The resulting
     * `ProjectUpdate` must be encoded with `explicitNulls = false` so that
     * Kotlin-`null` fields are OMITTED from the JSON (server treats absent
     * = keep existing value).
     */
    fun toUpdateBody(): ProjectUpdate {
        val current = editing ?: error("toUpdateBody called without editing target")
        val trimmedName = name.trim()
        val trimmedPath = path.trim()
        val trimmedConda = condaEnv.trim()

        val nameOut: String? =
            if (trimmedName != current.name) trimmedName else null

        val pathOut: String? =
            if (trimmedPath != current.path) trimmedPath else null

        // linear_api_key: explicit clear → "", user typed a new key → value, else omit.
        val keyOut: String? = when {
            clearLinearKey -> ""
            linearApiKey.isNotEmpty() -> linearApiKey
            else -> null
        }

        // conda_env: compare to current; if user cleared the text, send "" to
        // explicitly clear on server; if unchanged, omit.
        val currentConda = current.conda_env.orEmpty()
        val condaOut: String? = when {
            trimmedConda == currentConda -> null
            trimmedConda.isEmpty() -> ""
            else -> trimmedConda
        }

        val flagsOut: Map<String, JsonElement>? =
            if (flagsDirty) flags.associate { it.key to it.toJsonElement() } else null

        // auto_scan_filter: send only if the user touched the section.
        // Otherwise null = keep existing on the server side. There is no
        // "clear" semantics — the filter is always present.
        val filterOut: AutoScanFilterDto? =
            if (filterDirty) currentFilterDto() else null

        return ProjectUpdate(
            name = nameOut,
            path = pathOut,
            linear_api_key = keyOut,
            conda_env = condaOut,
            default_flags = flagsOut,
            auto_scan_filter = filterOut,
        )
    }

    companion object {
        // Permissive but excludes slashes / control chars. Server accepts any
        // non-empty string, but this rule prevents accidental path-like input.
        private val VALID_NAME = Regex("^[A-Za-z0-9 ._\\-]+$")

        fun forCreate(): ProjectFormState = ProjectFormState(editing = null)

        fun forEdit(project: ProjectInfo): ProjectFormState = ProjectFormState(
            editing = project,
            name = project.name,
            path = project.path,
            condaEnv = project.conda_env.orEmpty(),
            // linear_api_key is never echoed by the server, so the field
            // starts blank in edit mode. Empty + !clearLinearKey = "keep".
            linearApiKey = "",
            clearLinearKey = false,
            flags = project.default_flags.map { (k, v) -> FlagEntry.fromJsonElement(k, v) },
            flagsDirty = false,
            triggerLabels = project.auto_scan_filter.label_names.toSet(),
            triggerStates = project.auto_scan_filter.state_names.toSet(),
            activeState = project.auto_scan_filter.active_state,
            doneState = project.auto_scan_filter.done_state,
            removalLabel = project.auto_scan_filter.removal_label,
            filterDirty = false,
        )
    }
}

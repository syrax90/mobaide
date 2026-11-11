package com.mobaide.app.ui.projects

import com.mobaide.app.data.network.dto.AutoScanFilterDto
import com.mobaide.app.data.network.dto.ProjectInfo
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure unit tests for [ProjectFormState] and [FlagEntry] — no coroutines,
 * no mocks. The form state is the single source of truth for validation and
 * the wire-encoding rules for `ProjectWrite` / `ProjectUpdate`.
 */
class ProjectFormStateTest {

    // --- name / path validation ---

    @Test
    fun nameError_null_beforeTouch_evenWhenBlank() {
        val s = ProjectFormState.forCreate().copy(name = "")
        assertNull(s.nameError)
    }

    @Test
    fun nameError_required_afterTouch() {
        val s = ProjectFormState.forCreate()
            .copy(name = "", touched = setOf(ProjectFormState.Field.NAME))
        assertEquals("Name is required", s.nameError)
    }

    @Test
    fun nameError_rejectsSlashes() {
        val s = ProjectFormState.forCreate()
            .copy(name = "a/b", submitAttempted = true)
        assertNotNull(s.nameError)
    }

    @Test
    fun nameError_serverErrorAlwaysVisible() {
        val s = ProjectFormState.forCreate()
            .copy(name = "alpha", nameServerError = "Cannot rename: scan active")
        assertEquals("Cannot rename: scan active", s.nameError)
    }

    // --- flag validation ---

    @Test
    fun flagKeyError_empty_afterTouch() {
        val s = ProjectFormState.forCreate().copy(
            flags = listOf(FlagEntry(key = "", type = FlagType.STRING, textValue = "x")),
            touched = setOf(ProjectFormState.Field.FLAG),
        )
        assertEquals("Key required", s.flagKeyErrors[0])
    }

    @Test
    fun flagKeyError_duplicateMarksSecondRow() {
        val s = ProjectFormState.forCreate().copy(
            flags = listOf(
                FlagEntry("dup", FlagType.STRING, "a"),
                FlagEntry("dup", FlagType.STRING, "b"),
            ),
            touched = setOf(ProjectFormState.Field.FLAG),
        )
        assertNull(s.flagKeyErrors[0])
        assertEquals("Duplicate key", s.flagKeyErrors[1])
    }

    @Test
    fun flagValue_numberLongAndDoubleBothValid() {
        assertTrue(FlagEntry("k", FlagType.NUMBER, "42").valueValid)
        assertTrue(FlagEntry("k", FlagType.NUMBER, "3.14").valueValid)
        assertFalse(FlagEntry("k", FlagType.NUMBER, "").valueValid)
        assertFalse(FlagEntry("k", FlagType.NUMBER, "oops").valueValid)
    }

    // --- canSave ---

    @Test
    fun canSave_trueOnlyWhenEverythingValid() {
        val ok = ProjectFormState.forCreate().copy(
            name = "alpha",
            path = "/p/alpha",
            flags = listOf(FlagEntry("k", FlagType.STRING, "v")),
        )
        assertTrue(ok.canSave)
    }

    @Test
    fun canSave_falseWhenSubmitting() {
        val s = ProjectFormState.forCreate().copy(
            name = "alpha",
            path = "/p/alpha",
            submitting = true,
        )
        assertFalse(s.canSave)
    }

    @Test
    fun canSave_falseOnServerError() {
        val s = ProjectFormState.forCreate().copy(
            name = "alpha",
            path = "/p/alpha",
            nameServerError = "Duplicate",
        )
        assertFalse(s.canSave)
    }

    // --- JsonElement round-trip ---

    @Test
    fun flagEntry_roundTrip_boolean() {
        val forward = FlagEntry("k", FlagType.BOOLEAN, "true").toJsonElement()
        assertEquals(true, (forward as JsonPrimitive).booleanOrNull)
        val back = FlagEntry.fromJsonElement("k", forward)
        assertEquals(FlagType.BOOLEAN, back.type)
        assertEquals("true", back.textValue)
    }

    @Test
    fun flagEntry_roundTrip_numberLong() {
        val forward = FlagEntry("k", FlagType.NUMBER, "42").toJsonElement()
        assertEquals(42L, (forward as JsonPrimitive).longOrNull)
        val back = FlagEntry.fromJsonElement("k", forward)
        assertEquals(FlagType.NUMBER, back.type)
        assertEquals("42", back.textValue)
    }

    @Test
    fun flagEntry_roundTrip_numberDouble() {
        val forward = FlagEntry("k", FlagType.NUMBER, "3.14").toJsonElement()
        assertEquals(3.14, (forward as JsonPrimitive).doubleOrNull!!, 0.0001)
        val back = FlagEntry.fromJsonElement("k", forward)
        assertEquals(FlagType.NUMBER, back.type)
    }

    @Test
    fun flagEntry_jsonStringContent_true_stillInferredAsString_notBoolean() {
        // Regression: JsonPrimitive.booleanOrNull parses string content, so a
        // JSON string "true" could be misclassified as a boolean if isString
        // isn't checked first.
        val back = FlagEntry.fromJsonElement("k", JsonPrimitive("true"))
        assertEquals(FlagType.STRING, back.type)
        assertEquals("true", back.textValue)
    }

    // --- ProjectWrite / ProjectUpdate shaping ---

    @Test
    fun toCreateBody_sendsTrimmedFields_andTypedFlags() {
        val s = ProjectFormState.forCreate().copy(
            name = "  alpha  ",
            path = "  /p/alpha ",
            condaEnv = "envx",
            linearApiKey = "sek",
            flags = listOf(
                FlagEntry("verbose", FlagType.BOOLEAN, "true"),
                FlagEntry("retries", FlagType.NUMBER, "3"),
                FlagEntry("model", FlagType.STRING, "opus"),
            ),
        )
        val body = s.toCreateBody()
        assertEquals("alpha", body.name)
        assertEquals("/p/alpha", body.path)
        assertEquals("envx", body.conda_env)
        assertEquals("sek", body.linear_api_key)
        assertEquals(true, (body.default_flags["verbose"] as JsonPrimitive).booleanOrNull)
        assertEquals(3L, (body.default_flags["retries"] as JsonPrimitive).longOrNull)
        assertEquals("opus", (body.default_flags["model"] as JsonPrimitive).content)
    }

    @Test
    fun toCreateBody_blankApiKey_isNull() {
        val s = ProjectFormState.forCreate().copy(
            name = "a",
            path = "/p",
            linearApiKey = "",
        )
        assertNull(s.toCreateBody().linear_api_key)
    }

    @Test
    fun toUpdateBody_unchangedFields_areNull() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha")
        val s = ProjectFormState.forEdit(current)
        val body = s.toUpdateBody()
        assertNull(body.name)
        assertNull(body.path)
        assertNull(body.linear_api_key)
        assertNull(body.conda_env)
        assertNull(body.default_flags)
    }

    @Test
    fun toUpdateBody_renameOnly_sendsName() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha")
        val s = ProjectFormState.forEdit(current).copy(name = "beta")
        val body = s.toUpdateBody()
        assertEquals("beta", body.name)
        assertNull(body.path)
    }

    @Test
    fun toUpdateBody_clearLinearKey_sendsEmptyString() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha", has_linear_key = true)
        val s = ProjectFormState.forEdit(current).copy(clearLinearKey = true)
        assertEquals("", s.toUpdateBody().linear_api_key)
    }

    @Test
    fun toUpdateBody_typedNewLinearKey_sendsValue() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha")
        val s = ProjectFormState.forEdit(current).copy(linearApiKey = "new-sek")
        assertEquals("new-sek", s.toUpdateBody().linear_api_key)
    }

    @Test
    fun toUpdateBody_flagsDirty_sendsNewMap() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha")
        val s = ProjectFormState.forEdit(current).copy(
            flags = listOf(FlagEntry("k", FlagType.STRING, "v")),
            flagsDirty = true,
        )
        val body = s.toUpdateBody()
        assertNotNull(body.default_flags)
        assertEquals(1, body.default_flags!!.size)
    }

    @Test
    fun toUpdateBody_flagsClean_flagsNull() {
        val current = ProjectInfo(
            name = "alpha",
            path = "/p/alpha",
            default_flags = mapOf("k" to JsonPrimitive("v")),
        )
        val s = ProjectFormState.forEdit(current)
        // `forEdit` builds flags from the project but flagsDirty stays false
        // until the user mutates one.
        assertFalse(s.flagsDirty)
        assertNull(s.toUpdateBody().default_flags)
    }

    @Test
    fun toUpdateBody_clearConda_sendsEmptyString_whenPreviouslySet() {
        val current = ProjectInfo(
            name = "alpha",
            path = "/p/alpha",
            conda_env = "old",
        )
        val s = ProjectFormState.forEdit(current).copy(condaEnv = "")
        assertEquals("", s.toUpdateBody().conda_env)
    }

    @Test
    fun forEdit_linearKeyStartsBlank() {
        val current = ProjectInfo(
            name = "alpha",
            path = "/p/alpha",
            has_linear_key = true,
        )
        val s = ProjectFormState.forEdit(current)
        assertEquals("", s.linearApiKey)
        assertFalse(s.clearLinearKey)
    }

    @Test
    fun forEdit_loadsFilterFromProject() {
        val current = ProjectInfo(
            name = "alpha",
            path = "/p/alpha",
            auto_scan_filter = AutoScanFilterDto(
                state_names = listOf("Backlog", "Triage"),
                label_names = listOf("AI-ready"),
                active_state = "Doing",
                done_state = "Released",
                removal_label = "AI-ready",
            ),
        )
        val s = ProjectFormState.forEdit(current)
        assertEquals(setOf("Backlog", "Triage"), s.triggerStates)
        assertEquals(setOf("AI-ready"), s.triggerLabels)
        assertEquals("Doing", s.activeState)
        assertEquals("Released", s.doneState)
        assertEquals("AI-ready", s.removalLabel)
        assertFalse(s.filterDirty)
    }

    @Test
    fun triggerLabelsError_visibleAfterTouch_whenEmpty() {
        val s = ProjectFormState.forCreate().copy(
            triggerLabels = emptySet(),
            touched = setOf(ProjectFormState.Field.FILTER),
        )
        assertEquals("Select at least one trigger label", s.triggerLabelsError)
    }

    @Test
    fun canSave_falseWhenFilterIncomplete() {
        val s = ProjectFormState.forCreate().copy(
            name = "alpha",
            path = "/p/alpha",
            triggerLabels = emptySet(),
        )
        assertFalse(s.canSave)
    }

    @Test
    fun toCreateBody_emitsCurrentFilter() {
        val s = ProjectFormState.forCreate().copy(
            name = "alpha",
            path = "/p/alpha",
            triggerLabels = setOf("AutoPipeline", "AI-ready"),
            triggerStates = setOf("Proposals", "Backlog"),
            activeState = "Doing",
            doneState = "Released",
            removalLabel = "AI-ready",
        )
        val body = s.toCreateBody()
        assertNotNull(body.auto_scan_filter)
        val f = body.auto_scan_filter!!
        assertTrue("AutoPipeline" in f.label_names)
        assertTrue("AI-ready" in f.label_names)
        assertEquals("Doing", f.active_state)
        assertEquals("Released", f.done_state)
        assertEquals("AI-ready", f.removal_label)
    }

    @Test
    fun toUpdateBody_filterDirty_sendsFilter() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha")
        val s = ProjectFormState.forEdit(current).copy(
            triggerLabels = setOf("AutoPipeline", "AI-ready"),
            filterDirty = true,
        )
        val body = s.toUpdateBody()
        assertNotNull(body.auto_scan_filter)
        assertTrue("AI-ready" in body.auto_scan_filter!!.label_names)
    }

    @Test
    fun toUpdateBody_filterClean_omitsFilter() {
        val current = ProjectInfo(
            name = "alpha",
            path = "/p/alpha",
            auto_scan_filter = AutoScanFilterDto(
                state_names = listOf("Proposals"),
                label_names = listOf("AutoPipeline"),
            ),
        )
        val s = ProjectFormState.forEdit(current)
        // No mutation → filterDirty stays false → null on the wire.
        assertFalse(s.filterDirty)
        assertNull(s.toUpdateBody().auto_scan_filter)
    }

    @Test
    fun showAutoScanFilterSection_trueInCreateModeWithKey() {
        val s = ProjectFormState.forCreate().copy(linearApiKey = "lin_test")
        assertTrue(s.showAutoScanFilterSection)
    }

    @Test
    fun showAutoScanFilterSection_trueInEditModeWithExistingKey() {
        val current = ProjectInfo(name = "alpha", path = "/p/alpha", has_linear_key = true)
        val s = ProjectFormState.forEdit(current)
        // linearApiKey stays "" in edit mode (server doesn't echo it back),
        // but the section still renders because the project HAS a key.
        assertTrue(s.showAutoScanFilterSection)
    }
}

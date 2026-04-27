package com.mobaide.app.ui.projects

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
    fun nameError_required_afterSubmitAttempt() {
        val s = ProjectFormState.forCreate().copy(name = "", submitAttempted = true)
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

    @Test
    fun pathError_required_afterTouch() {
        val s = ProjectFormState.forCreate()
            .copy(path = "", touched = setOf(ProjectFormState.Field.PATH))
        assertEquals("Path is required", s.pathError)
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
    fun flagValueError_numberNonNumeric() {
        val s = ProjectFormState.forCreate().copy(
            flags = listOf(FlagEntry("k", FlagType.NUMBER, "abc")),
            touched = setOf(ProjectFormState.Field.FLAG),
        )
        assertEquals("Not a number", s.flagValueErrors[0])
    }

    @Test
    fun flagValue_numberLongAndDoubleBothValid() {
        assertTrue(FlagEntry("k", FlagType.NUMBER, "42").valueValid)
        assertTrue(FlagEntry("k", FlagType.NUMBER, "3.14").valueValid)
        assertFalse(FlagEntry("k", FlagType.NUMBER, "").valueValid)
        assertFalse(FlagEntry("k", FlagType.NUMBER, "oops").valueValid)
    }

    @Test
    fun flagValue_booleanAlwaysValidForTrueOrFalse() {
        assertTrue(FlagEntry("k", FlagType.BOOLEAN, "true").valueValid)
        assertTrue(FlagEntry("k", FlagType.BOOLEAN, "false").valueValid)
    }

    @Test
    fun flagValue_stringAlwaysValid() {
        assertTrue(FlagEntry("k", FlagType.STRING, "").valueValid)
        assertTrue(FlagEntry("k", FlagType.STRING, "any").valueValid)
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
    fun canSave_falseWhenNameBlank() {
        val s = ProjectFormState.forCreate().copy(name = "", path = "/p")
        assertFalse(s.canSave)
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
    fun flagEntry_roundTrip_string() {
        val forward = FlagEntry("k", FlagType.STRING, "opus").toJsonElement()
        val back = FlagEntry.fromJsonElement("k", forward)
        assertEquals(FlagType.STRING, back.type)
        assertEquals("opus", back.textValue)
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
    fun toUpdateBody_condaUnchanged_isNull() {
        val current = ProjectInfo(
            name = "alpha",
            path = "/p/alpha",
            conda_env = "old",
        )
        val s = ProjectFormState.forEdit(current) // condaEnv defaults to "old"
        assertNull(s.toUpdateBody().conda_env)
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
}

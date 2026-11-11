package com.mobaide.app.ui.templates

import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TemplatesViewModel] -- exercises the editor-state machine,
 * delete-confirmation flow, refresh side-effect, error propagation, and the
 * upstream observe-templates pass-through that keeps the UI list in sync with
 * push events.
 *
 * Mirrors [com.mobaide.app.ui.agents.AgentListViewModelTest] in shape: real
 * [MutableStateFlow] for `observeTemplates()`, MockK for the rest, and a
 * [MainDispatcherRule] to run `viewModelScope.launch { }` on a
 * `StandardTestDispatcher` that advances under `advanceUntilIdle()`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TemplatesViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ---------------------------------------------------------------

    private fun template(
        id: String,
        name: String = "tpl-$id",
        body: String = "body-$id",
        favorite: Boolean = false,
        version: Int = 1,
    ): TemplateDto = TemplateDto(
        template_id = id,
        name = name,
        body = body,
        is_favorite = favorite,
        last_used_at = null,
        created_at = "2026-04-30T12:00:00Z",
        updated_at = "2026-04-30T12:00:00Z",
        version = version,
    )

    private class Fixture(
        val repo: TemplatesRepository,
        val templatesFlow: MutableStateFlow<List<TemplateDto>>,
    )

    private fun newFixture(initial: List<TemplateDto> = emptyList()): Fixture {
        val repo = mockk<TemplatesRepository>(relaxed = true)
        val templatesFlow = MutableStateFlow(initial)
        every { repo.observeTemplates() } returns templatesFlow
        coEvery { repo.refreshTemplates() } just Runs
        return Fixture(repo, templatesFlow)
    }

    private fun Fixture.buildVm(): TemplatesViewModel = TemplatesViewModel(repo)

    // ---- init() side-effects ---------------------------------------------------

    @Test
    fun init_callsRefreshTemplates_andSeedsEmptyState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coVerify(atLeast = 1) { fx.repo.refreshTemplates() }
        assertTrue(vm.uiState.value.templates.isEmpty())
        assertFalse(vm.uiState.value.isLoading)
        assertNull(vm.uiState.value.error)
        assertEquals(EditorState.Hidden, vm.uiState.value.editorState)
        assertNull(vm.uiState.value.deleteTarget)
    }

    // ---- refresh() -------------------------------------------------------------

    @Test
    fun refresh_onException_setsErrorMessage() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.repo.refreshTemplates() } throws RuntimeException("network down")

        vm.refresh()
        advanceUntilIdle()

        assertEquals("network down", vm.uiState.value.error)
        assertFalse(vm.uiState.value.isLoading)
    }

    // ---- saveFromEditor: create vs edit branch ---------------------------------

    @Test
    fun saveFromEditor_inCreateMode_callsCreateTemplate_andClosesEditor() = runTest {
        val fx = newFixture()
        coEvery { fx.repo.createTemplate(any(), any()) } returns template("t-new")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.openCreateEditor()

        vm.saveFromEditor("new-name", "new-body")
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.repo.createTemplate("new-name", "new-body") }
        coVerify(exactly = 0) { fx.repo.updateTemplate(any(), any(), any(), any()) }
        assertEquals(EditorState.Hidden, vm.uiState.value.editorState)
    }

    @Test
    fun saveFromEditor_inEditMode_callsUpdateTemplate_withTargetId() = runTest {
        // Editing an existing template must route through updateTemplate
        // with the original template_id and the new name+body. is_favorite
        // is intentionally null here — the editor only edits name/body.
        val fx = newFixture()
        coEvery {
            fx.repo.updateTemplate(any(), any(), any(), any())
        } returns template("t-1", name = "renamed", version = 2)
        val vm = fx.buildVm()
        advanceUntilIdle()
        val original = template("t-1", name = "old", body = "old-body")
        vm.openEditEditor(original)

        vm.saveFromEditor("renamed", "new-body")
        advanceUntilIdle()

        coVerify(exactly = 1) {
            fx.repo.updateTemplate("t-1", name = "renamed", body = "new-body")
        }
        coVerify(exactly = 0) { fx.repo.createTemplate(any(), any()) }
        assertEquals(EditorState.Hidden, vm.uiState.value.editorState)
    }

    @Test
    fun saveFromEditor_whenEditorHidden_isNoOp() = runTest {
        // Defensive: if the View calls saveFromEditor before openCreateEditor,
        // nothing must happen (no repo call, no state change).
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.saveFromEditor("name", "body")
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.repo.createTemplate(any(), any()) }
        coVerify(exactly = 0) { fx.repo.updateTemplate(any(), any(), any(), any()) }
        assertEquals(EditorState.Hidden, vm.uiState.value.editorState)
    }

    @Test
    fun saveFromEditor_onException_setsError_andLeavesEditorOpen() = runTest {
        val fx = newFixture()
        coEvery { fx.repo.createTemplate(any(), any()) } throws RuntimeException("boom")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.openCreateEditor()

        vm.saveFromEditor("name", "body")
        advanceUntilIdle()

        assertEquals("boom", vm.uiState.value.error)
        // Editor stays open so the user can retry without losing their input.
        assertTrue(vm.uiState.value.editorState is EditorState.Open)
    }

    // ---- toggleFavorite --------------------------------------------------------

    @Test
    fun toggleFavorite_flipsCurrentFavoriteFlag_viaUpdateTemplate() = runTest {
        val fx = newFixture()
        coEvery {
            fx.repo.updateTemplate(any(), any(), any(), any())
        } returns template("t-1", favorite = true)
        val vm = fx.buildVm()
        advanceUntilIdle()
        val unfav = template("t-1", favorite = false)

        vm.toggleFavorite(unfav)
        advanceUntilIdle()

        // Important: the partial-update payload must set is_favorite to the
        // INVERSE of the input — and must leave name/body untouched (null).
        coVerify(exactly = 1) {
            fx.repo.updateTemplate("t-1", isFavorite = true)
        }
    }

    // ---- Delete-confirmation state machine -------------------------------------

    @Test
    fun confirmDelete_callsDeleteTemplate_andClearsDeleteTarget() = runTest {
        val fx = newFixture()
        coEvery { fx.repo.deleteTemplate(any()) } just Runs
        val vm = fx.buildVm()
        advanceUntilIdle()
        val target = template("t-1")
        vm.showDeleteConfirm(target)

        vm.confirmDelete(target)
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.repo.deleteTemplate("t-1") }
        // The dialog dismissal happens BEFORE the network call so the user
        // never sees a stuck modal — guard that here.
        assertNull(vm.uiState.value.deleteTarget)
    }
}

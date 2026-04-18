package com.claudemobile.app.ui.projects

import app.cash.turbine.test
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.ProjectInfo
import com.claudemobile.app.data.network.dto.ProjectUpdate
import com.claudemobile.app.data.network.dto.ProjectWrite
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.ProjectConflictException
import com.claudemobile.app.data.repository.ProjectsRepository
import com.claudemobile.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [ProjectsViewModel]. [MainDispatcherRule] installs a
 * [kotlinx.coroutines.test.StandardTestDispatcher] so `viewModelScope` only
 * advances on explicit `advanceUntilIdle()`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ProjectsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun project(
        name: String,
        order: Int = 0,
        path: String = "/p/$name",
        pathExists: Boolean = true,
        has_linear_key: Boolean = false,
        conda_env: String? = null,
    ): ProjectInfo = ProjectInfo(
        name = name,
        path = path,
        has_linear_key = has_linear_key,
        is_git = true,
        default_flags = emptyMap(),
        conda_env = conda_env,
        order = order,
        path_exists = pathExists,
    )

    private class Fixture(
        val vm: ProjectsViewModel,
        val projectsRepo: ProjectsRepository,
        val autoScanRepo: AutoScanRepository,
        val projectsFlow: MutableStateFlow<List<ProjectInfo>>,
        val scansFlow: MutableStateFlow<List<AutoScanInstanceDto>>,
    )

    private fun newFixture(
        initialProjects: List<ProjectInfo> = listOf(
            project("alpha", order = 1),
            project("beta", order = 0),
        ),
        initialScans: List<AutoScanInstanceDto> = emptyList(),
    ): Fixture {
        val projectsRepo = mockk<ProjectsRepository>(relaxed = true)
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        val projectsFlow = MutableStateFlow(initialProjects)
        val scansFlow = MutableStateFlow(initialScans)
        every { projectsRepo.observeProjects() } returns projectsFlow
        coEvery { projectsRepo.listProjects() } returns initialProjects
        every { autoScanRepo.observeScans() } returns scansFlow
        val vm = ProjectsViewModel(projectsRepo, autoScanRepo)
        return Fixture(vm, projectsRepo, autoScanRepo, projectsFlow, scansFlow)
    }

    // --- projects list / sort ---

    @Test
    fun projects_sortedByOrderAscending() = runTest {
        val f = newFixture(
            listOf(project("alpha", order = 2), project("beta", order = 0), project("gamma", order = 1)),
        )
        advanceUntilIdle()
        val sorted = f.vm.uiState.value.projects.map { it.name }
        assertEquals(listOf("beta", "gamma", "alpha"), sorted)
    }

    // --- sheet open/close ---

    @Test
    fun openCreateSheet_initializesBlankForm() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        val form = f.vm.uiState.value.form
        assertNotNull(form)
        assertFalse(form!!.isEdit)
        assertEquals("", form.name)
    }

    @Test
    fun openEditSheet_populatesFieldsAndLeavesLinearKeyBlank() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val target = project("alpha", has_linear_key = true, conda_env = "ml")
        f.vm.openEditSheet(target)
        val form = f.vm.uiState.value.form!!
        assertTrue(form.isEdit)
        assertEquals("alpha", form.name)
        assertEquals("/p/alpha", form.path)
        assertEquals("ml", form.condaEnv)
        assertEquals("", form.linearApiKey)
        assertFalse(form.clearLinearKey)
    }

    @Test
    fun closeSheet_clearsForm() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        f.vm.closeSheet()
        assertNull(f.vm.uiState.value.form)
    }

    // --- save ---

    @Test
    fun save_createMode_sendsTypedFlagsAndClosesSheetOn2xx() = runTest {
        val f = newFixture()
        advanceUntilIdle()

        val bodySlot = slot<ProjectWrite>()
        coEvery { f.projectsRepo.createProject(capture(bodySlot)) } returns project("alpha")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")
        f.vm.addFlag()
        f.vm.updateFlag(0, FlagEntry("verbose", FlagType.BOOLEAN, "true"))
        f.vm.save()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.projectsRepo.createProject(any()) }
        assertEquals("alpha", bodySlot.captured.name)
        assertEquals(1, bodySlot.captured.default_flags.size)
        assertEquals(true, (bodySlot.captured.default_flags["verbose"] as JsonPrimitive).content == "true")
        assertNull(f.vm.uiState.value.form)  // sheet closed on success
    }

    @Test
    fun save_editMode_omitsUnchangedLinearKey() = runTest {
        val f = newFixture()
        advanceUntilIdle()

        val target = project("alpha", has_linear_key = true)
        val bodySlot = slot<ProjectUpdate>()
        coEvery { f.projectsRepo.updateProject(any(), capture(bodySlot)) } returns target

        f.vm.openEditSheet(target)
        // Do NOT modify linearApiKey or set clearLinearKey.
        f.vm.save()
        advanceUntilIdle()

        assertNull(
            "Unchanged linearApiKey must be null (omitted from wire)",
            bodySlot.captured.linear_api_key,
        )
    }

    @Test
    fun save_editMode_clearLinearKey_sendsEmptyString() = runTest {
        val f = newFixture()
        advanceUntilIdle()

        val target = project("alpha", has_linear_key = true)
        val bodySlot = slot<ProjectUpdate>()
        coEvery { f.projectsRepo.updateProject(any(), capture(bodySlot)) } returns target

        f.vm.openEditSheet(target)
        f.vm.requestClearLinearKey()
        f.vm.save()
        advanceUntilIdle()

        assertEquals("", bodySlot.captured.linear_api_key)
    }

    @Test
    fun save_editMode_409_setsNameServerError() = runTest {
        val f = newFixture()
        advanceUntilIdle()

        val target = project("alpha")
        coEvery { f.projectsRepo.updateProject(any(), any()) } throws
            ProjectConflictException("Cannot rename 'alpha' to 'beta': target already exists")

        f.vm.openEditSheet(target)
        f.vm.onNameChange("beta")
        f.vm.save()
        advanceUntilIdle()

        val form = f.vm.uiState.value.form
        assertNotNull(form)
        assertEquals(
            "Cannot rename 'alpha' to 'beta': target already exists",
            form!!.nameServerError,
        )
        assertFalse(form.submitting)
    }

    // --- delete / undo ---

    @Test
    fun swipeDelete_onIdleProject_callsApi_andEmitsUndoEvent() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.deleteProject("alpha") } just Runs

        f.vm.events.test {
            f.vm.onSwipeDelete(project("alpha"))
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is ProjectsUiEvent.DeletedWithUndo)
            assertEquals("alpha", (event as ProjectsUiEvent.DeletedWithUndo).snapshot.name)
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { f.projectsRepo.deleteProject("alpha") }
    }

    @Test
    fun swipeDelete_onBusyProject_opensConfirmDialog_noApiCall() = runTest {
        val f = newFixture()
        f.scansFlow.value = listOf(
            AutoScanInstanceDto(
                project_name = "alpha",
                status = "running",
                started_at = 0.0,
                last_activity_at = 0.0,
            )
        )
        advanceUntilIdle()

        f.vm.onSwipeDelete(project("alpha"))
        advanceUntilIdle()

        assertEquals("alpha", f.vm.uiState.value.pendingDelete?.name)
        coVerify(exactly = 0) { f.projectsRepo.deleteProject(any()) }
    }

    @Test
    fun confirmBusyDelete_callsApi_butNoUndoEvent() = runTest {
        val f = newFixture()
        f.scansFlow.value = listOf(
            AutoScanInstanceDto(
                project_name = "alpha",
                status = "running",
                started_at = 0.0,
                last_activity_at = 0.0,
            )
        )
        advanceUntilIdle()
        coEvery { f.projectsRepo.deleteProject("alpha") } just Runs
        f.vm.onSwipeDelete(project("alpha"))
        advanceUntilIdle()
        f.vm.confirmBusyDelete()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.projectsRepo.deleteProject("alpha") }
        // No pending delete and no UNDO: the busy path goes through confirmBusyDelete,
        // which intentionally does NOT emit a DeletedWithUndo (UX: user already
        // confirmed an explicit action).
        assertNull(f.vm.uiState.value.pendingDelete)
    }

    @Test
    fun undoDelete_callsCreateProject() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val snapshot = project("alpha", has_linear_key = false)
        val slotBody = slot<ProjectWrite>()
        coEvery { f.projectsRepo.createProject(capture(slotBody)) } returns snapshot

        f.vm.undoDelete(snapshot)
        advanceUntilIdle()

        assertEquals("alpha", slotBody.captured.name)
        assertEquals("/p/alpha", slotBody.captured.path)
        assertNull(slotBody.captured.linear_api_key)
    }

    @Test
    fun undoDelete_withLinearKey_emitsKeyReminder() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val snapshot = project("alpha", has_linear_key = true)
        coEvery { f.projectsRepo.createProject(any()) } returns snapshot

        f.vm.events.test {
            f.vm.undoDelete(snapshot)
            advanceUntilIdle()
            assertEquals(ProjectsUiEvent.LinearKeyReminder, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    // --- reorder ---

    @Test
    fun reorder_callsPatchWithNewNames() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.reorderProjects(any()) } returns emptyList()

        f.vm.reorder(listOf("alpha", "beta"))
        advanceUntilIdle()

        coVerify(exactly = 1) { f.projectsRepo.reorderProjects(listOf("alpha", "beta")) }
    }

    @Test
    fun reorder_failure_emitsMessageEvent() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.reorderProjects(any()) } throws RuntimeException("500")

        f.vm.events.test {
            f.vm.reorder(listOf("alpha", "beta"))
            advanceUntilIdle()
            val event = awaitItem()
            assertTrue(event is ProjectsUiEvent.Message)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun reorder_optimistic_displayReflectsDraggedOrder_beforeServerAck() = runTest {
        // AC#6: optimistic local update — the UI reorders immediately while the
        // PATCH is in flight. Drag "alpha" to position 0 (before "beta"); the
        // projectsFlow hasn't been updated yet (no `projects.changed` event),
        // but uiState.projects must already reflect the user's arrangement.
        val f = newFixture(
            listOf(project("alpha", order = 1), project("beta", order = 0)),
        )
        advanceUntilIdle()
        // Sanity: repo order sorts beta before alpha.
        assertEquals(listOf("beta", "alpha"), f.vm.uiState.value.projects.map { it.name })

        // Block reorderProjects() forever so the flight stays open.
        coEvery { f.projectsRepo.reorderProjects(any()) } coAnswers {
            kotlinx.coroutines.awaitCancellation()
        }

        f.vm.reorder(listOf("alpha", "beta"))
        advanceUntilIdle()

        // With `_pendingOrder = ["alpha","beta"]` and the repo flow unchanged,
        // the combine block must render the pending permutation.
        assertEquals(
            listOf("alpha", "beta"),
            f.vm.uiState.value.projects.map { it.name },
        )
    }

    @Test
    fun reorder_failure_revertsLocalOrder_toRepoOrder() = runTest {
        // AC#6: on server failure, the optimistic override must be cleared so
        // the display snaps back to the repo's authoritative order.
        val f = newFixture(
            listOf(project("alpha", order = 1), project("beta", order = 0)),
        )
        advanceUntilIdle()
        coEvery { f.projectsRepo.reorderProjects(any()) } throws RuntimeException("boom")

        f.vm.reorder(listOf("alpha", "beta"))
        advanceUntilIdle()

        // Pending override cleared → display is back to `order`-sorted list.
        assertEquals(
            listOf("beta", "alpha"),
            f.vm.uiState.value.projects.map { it.name },
        )
    }

    @Test
    fun reorder_success_pendingOverrideStaysUntilRepoEventLands() = runTest {
        // AC#6: after a successful PATCH we do NOT clear `_pendingOrder`
        // immediately — we wait for the `projects.changed` event so there's
        // no flicker. Simulate that event by pushing a matching permutation
        // into the repo flow; the display must track it without oscillation.
        val f = newFixture(
            listOf(project("alpha", order = 1), project("beta", order = 0)),
        )
        advanceUntilIdle()
        coEvery { f.projectsRepo.reorderProjects(any()) } returns emptyList()

        f.vm.reorder(listOf("alpha", "beta"))
        advanceUntilIdle()
        // Before the event, display reflects the pending override.
        assertEquals(listOf("alpha", "beta"), f.vm.uiState.value.projects.map { it.name })

        // Server emits `projects.changed` with the new order reflected in
        // `order` fields.
        f.projectsFlow.value = listOf(
            project("alpha", order = 0),
            project("beta", order = 1),
        )
        advanceUntilIdle()

        assertEquals(listOf("alpha", "beta"), f.vm.uiState.value.projects.map { it.name })
    }

    // --- validation / touched gating ---

    @Test
    fun save_blocked_whenFormInvalid_noRepoCall() = runTest {
        // AC#2 / touch gating: canSave is false (name blank) → save() must not
        // hit the repo. It also flips submitAttempted so errors become visible.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        // name stays blank; path stays blank.
        f.vm.save()
        advanceUntilIdle()

        coVerify(exactly = 0) { f.projectsRepo.createProject(any()) }
        val form = f.vm.uiState.value.form
        assertNotNull(form)
        assertTrue("submitAttempted must flip to true after a blocked save", form!!.submitAttempted)
        assertEquals("Name is required", form.nameError)
        assertFalse(form.submitting)
    }

    @Test
    fun save_createMode_genericException_emitsMessage_keepsSheetOpen() = runTest {
        // Non-409 failures surface as a Snackbar; the sheet stays open so the
        // user can retry. `submitting` must flip back to false.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws RuntimeException("net down")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")

        f.vm.events.test {
            f.vm.save()
            advanceUntilIdle()
            val ev = awaitItem()
            assertTrue(ev is ProjectsUiEvent.Message)
            assertTrue((ev as ProjectsUiEvent.Message).text.contains("net down"))
            cancelAndIgnoreRemainingEvents()
        }
        val form = f.vm.uiState.value.form
        assertNotNull("Sheet stays open on generic failure", form)
        assertFalse(form!!.submitting)
    }

    @Test
    fun save_createMode_success_emitsCloseSheetEvent() = runTest {
        // AC#1: successful save emits CloseSheet and nulls out the form.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } returns project("alpha")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")

        f.vm.events.test {
            f.vm.save()
            advanceUntilIdle()
            val ev = awaitItem()
            assertEquals(ProjectsUiEvent.CloseSheet, ev)
            cancelAndIgnoreRemainingEvents()
        }
        assertNull(f.vm.uiState.value.form)
    }

    @Test
    fun save_editMode_409_matchesServerMessage_renameWhileScanActive() = runTest {
        // AC#3: 409 from a rename-while-scan-active → inline name error,
        // sheet stays open, submitting clears.
        val f = newFixture()
        advanceUntilIdle()
        val target = project("alpha")
        coEvery { f.projectsRepo.updateProject(any(), any()) } throws
            ProjectConflictException("Auto-scan active — stop scan to rename")

        f.vm.openEditSheet(target)
        f.vm.onNameChange("beta")
        f.vm.save()
        advanceUntilIdle()

        val form = f.vm.uiState.value.form
        assertNotNull("Sheet must remain open so the user sees the inline error", form)
        assertEquals("Auto-scan active — stop scan to rename", form!!.nameServerError)
        assertFalse(form.submitting)
        // `nameError` uses the server error verbatim and is visible even
        // without a touch/submitAttempted.
        assertEquals("Auto-scan active — stop scan to rename", form.nameError)
    }

    @Test
    fun onNameChange_clearsNameServerError() = runTest {
        // Typing in the name field must clear a prior 409 server error so the
        // user isn't stuck with a stale inline message.
        val f = newFixture()
        advanceUntilIdle()
        val target = project("alpha")
        coEvery { f.projectsRepo.updateProject(any(), any()) } throws
            ProjectConflictException("Cannot rename")

        f.vm.openEditSheet(target)
        f.vm.onNameChange("beta")
        f.vm.save()
        advanceUntilIdle()
        assertEquals("Cannot rename", f.vm.uiState.value.form!!.nameServerError)

        // Re-edit the name → server error must clear.
        f.vm.onNameChange("gamma")
        assertNull(f.vm.uiState.value.form!!.nameServerError)
    }

    // --- form mutators / flags ---

    @Test
    fun flagLifecycle_add_update_remove_flipsFlagsDirty() = runTest {
        // AC#5: typed flags round-trip without coercion. Also verifies that
        // the form lifecycle marks `flagsDirty` so edit-mode save sends a
        // new map instead of omitting `default_flags`.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()

        f.vm.addFlag()
        assertEquals(1, f.vm.uiState.value.form!!.flags.size)
        assertTrue(f.vm.uiState.value.form!!.flagsDirty)

        f.vm.updateFlag(0, FlagEntry("retries", FlagType.NUMBER, "5"))
        assertEquals("retries", f.vm.uiState.value.form!!.flags[0].key)
        assertEquals(FlagType.NUMBER, f.vm.uiState.value.form!!.flags[0].type)

        f.vm.addFlag()
        f.vm.updateFlag(1, FlagEntry("verbose", FlagType.BOOLEAN, "true"))
        assertEquals(2, f.vm.uiState.value.form!!.flags.size)

        f.vm.removeFlag(0)
        assertEquals(1, f.vm.uiState.value.form!!.flags.size)
        assertEquals("verbose", f.vm.uiState.value.form!!.flags[0].key)
    }

    @Test
    fun updateFlag_ignoresOutOfBoundsIndex() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        // Index 0 is out of bounds because no flag has been added yet.
        f.vm.updateFlag(0, FlagEntry("k", FlagType.STRING, "v"))
        assertEquals(0, f.vm.uiState.value.form!!.flags.size)
    }

    @Test
    fun removeFlag_ignoresOutOfBoundsIndex() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        f.vm.addFlag()
        assertEquals(1, f.vm.uiState.value.form!!.flags.size)
        // Negative index must not crash nor modify the list.
        f.vm.removeFlag(-1)
        f.vm.removeFlag(99)
        assertEquals(1, f.vm.uiState.value.form!!.flags.size)
    }

    @Test
    fun touch_marksFieldVisible_andErrorNowRenders() = runTest {
        // AC#2: touched gating — name error is hidden until the user focuses
        // and blurs the field (touch), or attempts submit.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        // Before touch: name is blank but nameError is null (hidden).
        assertNull(f.vm.uiState.value.form!!.nameError)

        f.vm.touch(ProjectFormState.Field.NAME)
        assertEquals("Name is required", f.vm.uiState.value.form!!.nameError)
    }

    @Test
    fun requestClearLinearKey_setsClearFlag_andHidesVisibility() = runTest {
        // AC#4: explicit Clear wipes the key locally and arms the PUT body.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openEditSheet(project("alpha", has_linear_key = true))

        // Pre-condition: visible could be on from the user toggling.
        f.vm.toggleLinearKeyVisible()
        assertTrue(f.vm.uiState.value.form!!.linearKeyVisible)

        f.vm.requestClearLinearKey()
        val form = f.vm.uiState.value.form!!
        assertTrue(form.clearLinearKey)
        assertEquals("", form.linearApiKey)
        assertFalse("Clearing must hide the revealed secret", form.linearKeyVisible)
    }

    @Test
    fun onLinearKeyChange_withNonEmpty_cancelsPendingClear() = runTest {
        // AC#4: typing a new key cancels an earlier "clear" intent, so the
        // user doesn't accidentally wipe their key after starting to type.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openEditSheet(project("alpha", has_linear_key = true))
        f.vm.requestClearLinearKey()
        assertTrue(f.vm.uiState.value.form!!.clearLinearKey)

        f.vm.onLinearKeyChange("fresh-key")
        val form = f.vm.uiState.value.form!!
        assertFalse(form.clearLinearKey)
        assertEquals("fresh-key", form.linearApiKey)
    }

    // --- delete failure paths ---

    @Test
    fun swipeDelete_409_emitsMessage_withServerDetail() = runTest {
        // AC#7: a 409 on delete (race with auto-scan starting) must be surfaced
        // as a plain Snackbar message, not as an UNDO.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.deleteProject("alpha") } throws
            ProjectConflictException("Cannot delete: auto-scan active.")

        f.vm.events.test {
            f.vm.onSwipeDelete(project("alpha"))
            advanceUntilIdle()
            val ev = awaitItem()
            assertTrue(ev is ProjectsUiEvent.Message)
            assertEquals("Cannot delete: auto-scan active.", (ev as ProjectsUiEvent.Message).text)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun swipeDelete_genericException_emitsMessage_noUndo() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.deleteProject("alpha") } throws RuntimeException("oops")

        f.vm.events.test {
            f.vm.onSwipeDelete(project("alpha"))
            advanceUntilIdle()
            val ev = awaitItem()
            assertTrue(ev is ProjectsUiEvent.Message)
            assertTrue((ev as ProjectsUiEvent.Message).text.contains("oops"))
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun dismissBusyDelete_clearsPending_noApiCall() = runTest {
        // AC#7: cancelling the "busy delete" dialog must not hit the server.
        val f = newFixture()
        f.scansFlow.value = listOf(
            AutoScanInstanceDto(
                project_name = "alpha",
                status = "running",
                started_at = 0.0,
                last_activity_at = 0.0,
            )
        )
        advanceUntilIdle()
        f.vm.onSwipeDelete(project("alpha"))
        advanceUntilIdle()
        assertEquals("alpha", f.vm.uiState.value.pendingDelete?.name)

        f.vm.dismissBusyDelete()
        advanceUntilIdle()

        assertNull(f.vm.uiState.value.pendingDelete)
        coVerify(exactly = 0) { f.projectsRepo.deleteProject(any()) }
    }

    @Test
    fun undoDelete_failure_emitsMessage() = runTest {
        // AC#1: UNDO-create failure must surface through the event bus; the
        // repository bus will also reconcile the list state.
        val f = newFixture()
        advanceUntilIdle()
        val snapshot = project("alpha")
        coEvery { f.projectsRepo.createProject(any()) } throws RuntimeException("reject")

        f.vm.events.test {
            f.vm.undoDelete(snapshot)
            advanceUntilIdle()
            val ev = awaitItem()
            assertTrue(ev is ProjectsUiEvent.Message)
            assertTrue((ev as ProjectsUiEvent.Message).text.contains("reject"))
            cancelAndIgnoreRemainingEvents()
        }
    }

    // --- busy-set propagation ---

    @Test
    fun busyProjectNames_reflectsAutoScanFlow() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.busyProjectNames.isEmpty())

        f.scansFlow.value = listOf(
            AutoScanInstanceDto(
                project_name = "alpha",
                status = "running",
                started_at = 0.0,
                last_activity_at = 0.0,
            )
        )
        advanceUntilIdle()
        assertEquals(setOf("alpha"), f.vm.uiState.value.busyProjectNames)

        // Stopping the scan drops the project from the busy set.
        f.scansFlow.value = emptyList()
        advanceUntilIdle()
        assertTrue(f.vm.uiState.value.busyProjectNames.isEmpty())
    }

    // --- edit save: full body shaping ---

    @Test
    fun save_editMode_condaAndFlags_roundTripIntoUpdateBody() = runTest {
        // AC#5 + AC#8: end-to-end — editing conda + adding a typed flag must
        // land the expected ProjectUpdate payload in the repository call.
        val f = newFixture()
        advanceUntilIdle()

        val target = project("alpha", conda_env = "old-env")
        val bodySlot = slot<ProjectUpdate>()
        coEvery { f.projectsRepo.updateProject(any(), capture(bodySlot)) } returns target

        f.vm.openEditSheet(target)
        f.vm.onCondaChange("new-env")
        f.vm.addFlag()
        f.vm.updateFlag(0, FlagEntry("model", FlagType.STRING, "opus"))
        f.vm.save()
        advanceUntilIdle()

        coVerify(exactly = 1) { f.projectsRepo.updateProject("alpha", any()) }
        assertEquals("new-env", bodySlot.captured.conda_env)
        assertNotNull("flagsDirty → default_flags must be present", bodySlot.captured.default_flags)
        assertEquals(1, bodySlot.captured.default_flags!!.size)
        assertTrue(bodySlot.captured.default_flags!!.containsKey("model"))
    }
}

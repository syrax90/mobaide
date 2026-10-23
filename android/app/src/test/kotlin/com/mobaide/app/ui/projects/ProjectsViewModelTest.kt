package com.mobaide.app.ui.projects

import app.cash.turbine.test
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.FsDirsResponse
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.ProjectUpdate
import com.mobaide.app.data.network.dto.ProjectWrite
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.LinearMetadataRepository
import com.mobaide.app.data.repository.MetadataResult
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.data.repository.ProjectConflictException
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
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
        val metadataRepo: LinearMetadataRepository,
        val projectsFlow: MutableStateFlow<List<ProjectInfo>>,
        val scansFlow: MutableStateFlow<List<AutoScanInstanceDto>>,
        val restApi: RestApi,
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
        val metadataRepo = mockk<LinearMetadataRepository>(relaxed = true)
        val projectsFlow = MutableStateFlow(initialProjects)
        val scansFlow = MutableStateFlow(initialScans)
        every { projectsRepo.observeProjects() } returns projectsFlow
        coEvery { projectsRepo.listProjects() } returns initialProjects
        every { autoScanRepo.observeScans() } returns scansFlow
        // Default: metadata repo returns empty cache + empty fresh — keeps
        // tests that don't care about filter UI uncluttered.
        coEvery { metadataRepo.getLabels(any(), any()) } returns
            MetadataResult.Cached<LinearLabelDto>(emptyList())
        coEvery { metadataRepo.getStates(any(), any()) } returns
            MetadataResult.Cached<LinearStateDto>(emptyList())
        // The Path field's folder suggestions run on a real repository over a
        // strict RestApi: nothing reaches it unless the field gains focus.
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxed = true)
        every { tokenStore.getAgentId() } returns "agent-1"
        val vm = ProjectsViewModel(
            projectsRepo, autoScanRepo, metadataRepo, PathSuggestionRepository(restApi, tokenStore),
        )
        return Fixture(vm, projectsRepo, autoScanRepo, metadataRepo, projectsFlow, scansFlow, restApi)
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
    fun pathFocus_listsTheEditedProjectsParent_andAPickDrillsIntoTheFolder() = runTest {
        // The edit sheet fills the path without a keystroke, so focusing the
        // field must be what hands it to the suggestions.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.restApi.getAgentVersion() } returns VersionInfoResponse(
            name = "mobaide_agent",
            server_version = "1.4.0",
            protocol_versions = listOf(7),
            min_client_version = 1,
            capabilities = Json.parseToJsonElement("""{"fs":{"dirs":true}}""") as JsonObject,
        )
        coEvery { f.restApi.listDirs("/p/", null) } returns
            FsDirsResponse("/p", listOf("alpha", "alpha-old", "beta"), truncated = false)

        f.vm.openEditSheet(project("alpha"))
        f.vm.onPathFocusChange(true)
        advanceTimeBy(201)

        assertEquals(listOf("alpha", "alpha-old"), f.vm.pathSuggestionState.value.names)

        f.vm.pickPathSuggestion("alpha-old")

        assertEquals("/p/alpha-old/", f.vm.uiState.value.form!!.path)
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

    @Test
    fun save_createMode_genericException_setsSubmitErrorOnFormState() = runTest {
        // Load-bearing contract for the "silent-save-failure" bug: a non-409
        // failure must leave a *visible* message in the StateFlow-backed
        // form state (`submitError`), not just on the lossy events
        // SharedFlow. The existing `_emitsMessage_keepsSheetOpen` test
        // covers the snackbar; this one pins the inline banner that the
        // user actually sees regardless of collector timing.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws
            RuntimeException("server exploded")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")
        f.vm.save()
        advanceUntilIdle()

        val form = f.vm.uiState.value.form
        assertNotNull("Sheet stays open so the banner can render", form)
        // Message format is "Save failed: <detail>". We pin the prefix
        // explicitly and assert the underlying cause is present so a
        // future i18n change still trips on a missing detail.
        val err = form!!.submitError
        assertNotNull("submitError must be populated on generic failure", err)
        assertTrue(
            "submitError must include the underlying cause; got $err",
            err!!.contains("server exploded"),
        )
        assertTrue(
            "submitError should be the 'Save failed' banner copy; got $err",
            err.startsWith("Save failed"),
        )
        assertFalse("submitting must clear after the failure", form.submitting)
    }

    @Test
    fun save_genericException_clearsPriorSubmitError_beforeRetryRunsAgain() =
        runTest {
            // Subtle but load-bearing: each `save()` invocation starts by
            // clearing `submitError` on the snapshot it operates on (see
            // `attempted = form.copy(... submitError = null)`). That way a
            // second failed retry re-renders a fresh banner instead of a
            // stale one. We verify by failing once, asserting the banner,
            // then failing AGAIN with a different message and asserting
            // the new message replaces (not appends).
            val f = newFixture()
            advanceUntilIdle()
            coEvery { f.projectsRepo.createProject(any()) } throws
                RuntimeException("first failure")

            f.vm.openCreateSheet()
            f.vm.onNameChange("alpha")
            f.vm.onPathChange("/p/alpha")
            f.vm.save()
            advanceUntilIdle()
            assertTrue(
                f.vm.uiState.value.form!!.submitError!!.contains("first failure"),
            )

            coEvery { f.projectsRepo.createProject(any()) } throws
                RuntimeException("second failure")
            f.vm.save()
            advanceUntilIdle()

            val err = f.vm.uiState.value.form!!.submitError
            assertNotNull(err)
            assertTrue(
                "Banner must reflect the latest failure; got $err",
                err!!.contains("second failure"),
            )
            assertFalse(
                "Stale message must not linger from the first try",
                err.contains("first failure"),
            )
        }

    @Test
    fun onNameChange_clearsSubmitError() = runTest {
        // Editing any field after a failure must wipe the inline banner —
        // the user is mid-correction, the stale 5xx detail no longer
        // applies. Pinned for `onNameChange` because the name is the
        // first field a typical user touches after a save bounce.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws
            RuntimeException("net down")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")
        f.vm.save()
        advanceUntilIdle()
        assertNotNull(
            "pre-condition: submitError must be set after the failure",
            f.vm.uiState.value.form!!.submitError,
        )

        // User edits the name → banner clears.
        f.vm.onNameChange("alpha2")
        assertNull(f.vm.uiState.value.form!!.submitError)
    }

    @Test
    fun onPathChange_clearsSubmitError() = runTest {
        // Same contract as `onNameChange_clearsSubmitError` but pinned on
        // the `path` field — both call sites must reset the banner so the
        // contract isn't accidentally narrowed to one mutator in a refactor.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws
            RuntimeException("net down")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")
        f.vm.save()
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.form!!.submitError)

        f.vm.onPathChange("/p/alpha2")
        assertNull(f.vm.uiState.value.form!!.submitError)
    }

    @Test
    fun flagAndKeyMutators_clearSubmitError() = runTest {
        // The "any field-edit clears the banner" contract has to cover ALL
        // user-initiated form mutators, not just the four text setters —
        // otherwise editing a flag value or hitting "Clear key" after a
        // save bounce leaves a stale "Save failed" banner pinned above
        // the action row. Pin all four mid-form mutators here so a future
        // refactor can't narrow the contract back to text-only.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws
            RuntimeException("net down")

        // updateFlag clears submitError.
        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")
        // Add a flag and immediately give it a valid key+value so canSave
        // stays true; otherwise the blank-key flag blocks save() before
        // reaching the repo call that sets submitError.
        f.vm.addFlag()
        f.vm.updateFlag(0, FlagEntry("retries", FlagType.NUMBER, "5"))
        f.vm.save()
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.form!!.submitError)
        // Now change the flag value to verify updateFlag clears submitError.
        f.vm.updateFlag(0, FlagEntry("retries", FlagType.NUMBER, "10"))
        assertNull(
            "updateFlag must clear submitError",
            f.vm.uiState.value.form!!.submitError,
        )

        // removeFlag clears submitError on a fresh failure.
        f.vm.save()
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.form!!.submitError)
        f.vm.removeFlag(0)
        assertNull(
            "removeFlag must clear submitError",
            f.vm.uiState.value.form!!.submitError,
        )

        // addFlag clears submitError on a fresh failure.
        f.vm.save()
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.form!!.submitError)
        f.vm.addFlag()
        assertNull(
            "addFlag must clear submitError",
            f.vm.uiState.value.form!!.submitError,
        )

        // requestClearLinearKey clears submitError on a fresh failure
        // (edit-mode form, since clear-key is only meaningful there).
        val edit = ProjectInfo(
            name = "p1", path = "/p/p1", conda_env = null,
            default_flags = emptyMap(), order = 0,
            has_linear_key = true, is_git = false, path_exists = true,
        )
        f.vm.openEditSheet(edit)
        coEvery { f.projectsRepo.updateProject(any(), any()) } throws
            RuntimeException("net down")
        f.vm.save()
        advanceUntilIdle()
        assertNotNull(f.vm.uiState.value.form!!.submitError)
        f.vm.requestClearLinearKey()
        assertNull(
            "requestClearLinearKey must clear submitError",
            f.vm.uiState.value.form!!.submitError,
        )
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
    fun openEditSheet_withLinearKey_loadsMetadataIntoForm() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.metadataRepo.getLabels("alpha", false) } returns
            MetadataResult.Fresh(
                listOf(LinearLabelDto("AutoPipeline", "#ff0000"), LinearLabelDto("AI-ready", "#00ff00")),
            )
        coEvery { f.metadataRepo.getStates("alpha", false) } returns
            MetadataResult.Fresh(
                listOf(LinearStateDto("Proposals", "backlog"), LinearStateDto("In Progress", "started")),
            )

        f.vm.openEditSheet(project("alpha", has_linear_key = true))
        advanceUntilIdle()

        val form = f.vm.uiState.value.form
        assertNotNull(form)
        assertEquals(2, form!!.availableLabels.size)
        assertEquals(2, form.availableStates.size)
        assertNull(form.filterMetadataError)
        assertFalse(form.filterMetadataLoading)
    }

    @Test
    fun openEditSheet_withoutLinearKey_doesNotCallMetadataRepo() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openEditSheet(project("beta", has_linear_key = false))
        advanceUntilIdle()
        coVerify(exactly = 0) { f.metadataRepo.getLabels(any(), any()) }
        coVerify(exactly = 0) { f.metadataRepo.getStates(any(), any()) }
    }

    @Test
    fun loadFilterMetadata_errorPath_keepsCachedAndSurfacesMessage() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.metadataRepo.getLabels("alpha", false) } returns
            MetadataResult.Error("net down", cached = listOf(LinearLabelDto("AutoPipeline")))
        coEvery { f.metadataRepo.getStates("alpha", false) } returns
            MetadataResult.Cached(emptyList())

        f.vm.openEditSheet(project("alpha", has_linear_key = true))
        advanceUntilIdle()
        val form = f.vm.uiState.value.form!!
        assertEquals(1, form.availableLabels.size)
        assertEquals("net down", form.filterMetadataError)
    }

    @Test
    fun toggleTriggerLabel_marksFilterDirty_andTouchesFilterField() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()
        f.vm.toggleTriggerLabel("AI-ready")

        val form = f.vm.uiState.value.form!!
        assertTrue("FILTER touched after toggle", ProjectFormState.Field.FILTER in form.touched)
        assertTrue(form.filterDirty)
        assertTrue("AI-ready added to selected", "AI-ready" in form.triggerLabels)
        // Toggling again removes.
        f.vm.toggleTriggerLabel("AI-ready")
        assertFalse("AI-ready" in f.vm.uiState.value.form!!.triggerLabels)
    }

    @Test
    fun save_create_sendsAutoScanFilterOnTheWire() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val bodySlot = slot<ProjectWrite>()
        coEvery { f.projectsRepo.createProject(capture(bodySlot)) } returns project("alpha")

        f.vm.openCreateSheet()
        f.vm.onNameChange("alpha")
        f.vm.onPathChange("/p/alpha")
        f.vm.toggleTriggerLabel("AI-ready")  // adds in addition to default AutoPipeline
        f.vm.toggleTriggerState("Backlog")   // adds in addition to default Proposals
        f.vm.onActiveStateChange("Doing")
        f.vm.save()
        advanceUntilIdle()

        val filter = bodySlot.captured.auto_scan_filter
        assertNotNull("auto_scan_filter must be populated on create", filter)
        assertTrue("AutoPipeline" in filter!!.label_names)
        assertTrue("AI-ready" in filter.label_names)
        assertTrue("Proposals" in filter.state_names)
        assertTrue("Backlog" in filter.state_names)
        assertEquals("Doing", filter.active_state)
    }

    @Test
    fun save_edit_filterUnchanged_omitsFromUpdateBody() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val target = project("alpha", has_linear_key = true)
        val bodySlot = slot<ProjectUpdate>()
        coEvery { f.projectsRepo.updateProject(any(), capture(bodySlot)) } returns target

        f.vm.openEditSheet(target)
        f.vm.save()
        advanceUntilIdle()

        // No filter mutation → null on the wire (server treats absent = keep).
        assertNull(
            "Unchanged filter must be null in PUT body",
            bodySlot.captured.auto_scan_filter,
        )
    }

    @Test
    fun save_edit_filterChanged_emitsAppliesOnNextScanMessage() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val target = project("alpha", has_linear_key = true)
        coEvery { f.projectsRepo.updateProject(any(), any()) } returns target

        f.vm.openEditSheet(target)
        f.vm.toggleTriggerLabel("AI-ready")

        val messages = mutableListOf<String>()
        f.vm.events.test {
            f.vm.save()
            advanceUntilIdle()
            // At least one message will fire (the "applies on next cycle" one).
            // The CloseSheet event fires too. Drain a few.
            for (i in 0 until 3) {
                val ev = awaitItem() as? ProjectsUiEvent.Message ?: continue
                messages += ev.text
                if (messages.any { "applies on" in it }) break
            }
            cancelAndIgnoreRemainingEvents()
        }
        assertTrue(
            "Filter-dirty save must emit the 'applies on next scan cycle' snackbar; got $messages",
            messages.any { "applies on" in it },
        )
    }

    @Test
    fun refreshFilterMetadata_inCreateMode_emitsHelpfulMessage_noNetworkCall() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        f.vm.openCreateSheet()

        f.vm.events.test {
            f.vm.refreshFilterMetadata()
            advanceUntilIdle()
            val ev = awaitItem()
            assertTrue(ev is ProjectsUiEvent.Message)
            assertTrue((ev as ProjectsUiEvent.Message).text.contains("Save the project first"))
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 0) { f.metadataRepo.getLabels(any(), any()) }
    }

    @Test
    fun refreshFilterMetadata_inEditMode_forcesNetworkRefresh() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.metadataRepo.getLabels("alpha", any()) } returns
            MetadataResult.Cached(emptyList())
        coEvery { f.metadataRepo.getStates("alpha", any()) } returns
            MetadataResult.Cached(emptyList())

        f.vm.openEditSheet(project("alpha", has_linear_key = true))
        advanceUntilIdle()
        f.vm.refreshFilterMetadata()
        advanceUntilIdle()

        // The second call (refreshFilterMetadata) must pass forceRefresh=true.
        coVerify(atLeast = 1) { f.metadataRepo.getLabels("alpha", true) }
        coVerify(atLeast = 1) { f.metadataRepo.getStates("alpha", true) }
    }

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

    @Test
    fun save_createMode_success_emitsRepositoryReturnedProjectOn_projectCreated() = runTest {
        // A successful create must surface the *exact* ProjectInfo
        // returned by the repository through `projectCreated`. The in-context
        // CreateSession dropdown depends on this object's `is_git` flag to
        // decide whether to clamp worktree — it cannot wait for the
        // `projects.changed` bus event, which is eventually-consistent.
        val f = newFixture()
        advanceUntilIdle()
        val created = project("gamma", path = "/p/gamma")  // is_git = true by helper default
        coEvery { f.projectsRepo.createProject(any()) } returns created

        f.vm.openCreateSheet()
        f.vm.onNameChange("gamma")
        f.vm.onPathChange("/p/gamma")

        f.vm.projectCreated.test {
            f.vm.save()
            advanceUntilIdle()
            val emitted = awaitItem()
            // Must be the repository's returned instance — not a synthetic
            // re-build from the form fields. Subscribers rely on `is_git`
            // and other server-supplied fields landing as-is.
            assertEquals("gamma", emitted.name)
            assertTrue(
                "Emitted ProjectInfo must carry the server's is_git verbatim",
                emitted.is_git,
            )
            assertEquals("/p/gamma", emitted.path)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun save_editMode_doesNotEmit_projectCreated() = runTest {
        // `projectCreated` is the *create-only* signal. Edit-mode
        // saves must NOT emit on it — otherwise an unrelated edit would
        // hijack the CreateSession dropdown's selection.
        val f = newFixture()
        advanceUntilIdle()
        val target = project("alpha")
        coEvery { f.projectsRepo.updateProject(any(), any()) } returns target

        f.vm.projectCreated.test {
            f.vm.openEditSheet(target)
            f.vm.save()
            advanceUntilIdle()
            // Drain the buffer: nothing should be there. expectNoEvents() is
            // the precise contract — no emission, ever, on the edit path.
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun save_createMode_failure_doesNotEmit_projectCreated() = runTest {
        // A failed create must not emit on `projectCreated` — only
        // successful creates select the new project. A premature emission
        // would auto-select a phantom project that the server rejected.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws RuntimeException("net down")

        f.vm.projectCreated.test {
            f.vm.openCreateSheet()
            f.vm.onNameChange("gamma")
            f.vm.onPathChange("/p/gamma")
            f.vm.save()
            advanceUntilIdle()
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun save_createMode_409Conflict_doesNotEmit_projectCreated() = runTest {
        // 409 (duplicate name) takes the same no-emit path as a
        // generic failure — pinned separately so a refactor of the
        // catch-block ordering can't accidentally emit before raising.
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.createProject(any()) } throws
            ProjectConflictException("Project 'alpha' already exists")

        f.vm.projectCreated.test {
            f.vm.openCreateSheet()
            f.vm.onNameChange("alpha")
            f.vm.onPathChange("/p/alpha")
            f.vm.save()
            advanceUntilIdle()
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }
}

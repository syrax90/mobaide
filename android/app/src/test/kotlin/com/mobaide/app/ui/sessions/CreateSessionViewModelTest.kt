package com.mobaide.app.ui.sessions

import app.cash.turbine.test
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.data.repository.SessionRepository
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
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [CreateSessionViewModel] — exercises the wizard step flow
 * (ProjectSelect -> ModePick -> StandardConfig / AutoScanConfig), worktree
 * clamping when the selected project is non-git, the `submit()` ->
 * [CreateSessionRequest] payload shape, the AutoScan preset defaults, the
 * navigation SharedFlow emissions on success, and the error state on
 * repository failure.
 *
 * [MainDispatcherRule] installs a StandardTestDispatcher so that the two
 * `viewModelScope.launch { ... }` blocks in `init { }` and the `submit*`
 * paths only run when the test yields via `advanceUntilIdle()`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CreateSessionViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private fun project(
        name: String,
        isGit: Boolean = true,
    ): ProjectInfo = ProjectInfo(name = name, path = "/p/$name", is_git = isGit)

    private class Fixture(
        val projectsRepo: ProjectsRepository,
        val sessionRepo: SessionRepository,
        val autoScanRepo: AutoScanRepository,
        val prefs: PreferencesStore,
        val projectsFlow: MutableStateFlow<List<ProjectInfo>>,
    )

    private fun newFixture(
        initialProjects: List<ProjectInfo> = listOf(
            project("alpha", isGit = true),
            project("beta", isGit = false),
        ),
        lastUsedProject: String? = null,
        autoScanPrompt: String? = "default auto scan prompt",
    ): Fixture {
        val projectsRepo = mockk<ProjectsRepository>(relaxed = true)
        val sessionRepo = mockk<SessionRepository>(relaxed = true)
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        val prefs = mockk<PreferencesStore>(relaxed = true)
        val projectsFlow = MutableStateFlow(initialProjects)

        every { projectsRepo.observeProjects() } returns projectsFlow
        coEvery { projectsRepo.listProjects() } returns initialProjects
        coEvery { prefs.getLastUsedProjectName() } returns lastUsedProject
        coEvery { prefs.setLastUsedProjectName(any()) } just Runs
        coEvery { prefs.setLastUsedSessionId(any()) } just Runs
        every { autoScanRepo.getAutoScanPrompt() } returns autoScanPrompt

        return Fixture(projectsRepo, sessionRepo, autoScanRepo, prefs, projectsFlow)
    }

    private fun Fixture.buildVm(): CreateSessionViewModel =
        CreateSessionViewModel(projectsRepo, sessionRepo, autoScanRepo, prefs)

    // ---- Wizard step flow --------------------------------------------------

    @Test
    fun wizardFlow_ProjectSelect_to_ModePick_to_StandardConfig() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)

        vm.selectProject("alpha")
        vm.confirmProject()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
        assertEquals("alpha", vm.uiState.value.selectedProjectName)

        vm.selectStandardMode()
        assertEquals(CreateSessionStep.StandardConfig, vm.uiState.value.step)
    }

    @Test
    fun confirmProject_doesNothing_whenNoProjectSelected() = runTest {
        val fx = newFixture(initialProjects = emptyList())
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.confirmProject()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
    }

    @Test
    fun goBack_fromStandardConfig_returnsToModePick_andFromModePickClearsSelectedMode() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Standard)
        vm.selectStandardMode()

        vm.goBack()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)

        vm.goBack()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
        assertNull(vm.uiState.value.selectedMode)
    }

    // ---- Worktree clamping on non-git project ------------------------------

    @Test
    fun selectProject_nonGit_clampsWorktreeOff_andClearsBranchName() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // User toggled worktree on + set a branch name while a git project
        // was implicitly active...
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        assertTrue(vm.uiState.value.worktree)
        assertEquals("feature/x", vm.uiState.value.branchName)

        // ...then picks a non-git project. The VM must clamp both off.
        vm.selectProject("beta")
        assertEquals("beta", vm.uiState.value.selectedProjectName)
        assertFalse(vm.uiState.value.worktree)
        assertEquals("", vm.uiState.value.branchName)
    }

    @Test
    fun selectProject_gitProject_preservesExistingWorktreeAndBranch() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.toggleWorktree(true)
        vm.setBranchName("main")
        vm.selectProject("alpha") // is_git = true

        assertTrue(vm.uiState.value.worktree)
        assertEquals("main", vm.uiState.value.branchName)
    }

    // ---- Standard submit path ----------------------------------------------

    @Test
    fun submit_happyPath_sendsCorrectPayload_andEmitsNavigateToSession() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse(
                session_id = "sess-123",
                state = "starting",
                created_at = "2026-04-11T00:00:00Z",
            )

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectStandardMode()
        vm.toggleNoMemory(true)
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        vm.navigateToSession.test {
            vm.submit()
            advanceUntilIdle()

            assertEquals("sess-123", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val req = requestSlot.captured
        assertEquals("alpha", req.project_name)
        val opts = req.claude_options
        assertNotNull(opts)
        assertTrue(opts!!.no_memory)
        assertTrue(opts.skip_permissions)
        assertTrue("max_effort must default to ON", opts.max_effort)
        assertTrue(opts.worktree)
        assertEquals("feature/x", opts.branch_name)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId("sess-123") }
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submit_withMaxEffortOff_sendsMaxEffortFalse() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-m", "starting", "2026-04-21T00:00:00Z")

        vm.selectProject("alpha")
        vm.toggleMaxEffort(false)
        vm.submit()
        advanceUntilIdle()

        val opts = requestSlot.captured.claude_options
        assertNotNull(opts)
        assertFalse("max_effort must propagate as false when toggled off", opts!!.max_effort)
    }

    @Test
    fun submit_blankBranchName_sendsNullBranchName() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-1", "starting", "2026-04-11T00:00:00Z")

        vm.selectProject("alpha")
        vm.setBranchName("   ") // whitespace-only -> takeIf { it.isNotBlank() } drops it
        vm.submit()
        advanceUntilIdle()

        assertNull(requestSlot.captured.claude_options?.branch_name)
    }

    @Test
    fun submit_noSelectedProject_isNoop() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.submit()
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submit_onException_setsErrorMessage_andClearsSubmittingFlag() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws RuntimeException("boom")

        vm.selectProject("alpha")
        vm.submit()
        advanceUntilIdle()

        assertEquals("Failed to create session: boom", vm.uiState.value.error)
        assertFalse(vm.uiState.value.submitting)
    }

    // ---- AutoScan preset + submit ------------------------------------------

    @Test
    fun selectAutoScanMode_firstCall_seedsPresetDefaults() = runTest {
        val fx = newFixture(autoScanPrompt = "scan everything")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()

        val s = vm.uiState.value
        assertEquals(CreateSessionStep.AutoScanConfig, s.step)
        assertTrue("AutoScan preset must enable worktree", s.worktree)
        assertEquals("scan everything", s.initialPrompt)
        assertTrue(s.autoScanConfigInitialized)
    }

    @Test
    fun selectAutoScanMode_secondCall_doesNotOverwriteUserEdits() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode() // seeds defaults
        vm.setInitialPrompt("user edit")
        vm.toggleNoMemory(true)
        vm.goBack()
        vm.selectAutoScanMode() // must NOT re-seed — autoScanConfigInitialized=true

        val s = vm.uiState.value
        assertEquals("user edit", s.initialPrompt)
        assertTrue(s.noMemory)
    }

    @Test
    fun submitAutoScan_happyPath_sendsOptions_andEmitsNavigateToAutoScan() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode() // seeds worktree=true, initialPrompt="seed"

        vm.navigateToAutoScan.test {
            vm.submitAutoScan()
            advanceUntilIdle()

            assertEquals("alpha", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val opts = optsSlot.captured
        assertTrue(opts.skip_permissions)
        assertTrue("max_effort must default to ON in Auto Scan payload", opts.max_effort)
        assertTrue(opts.worktree)
        assertNull(opts.branch_name)
        assertEquals("seed", opts.initial_prompt)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        assertFalse(vm.uiState.value.autoScanStarting)
    }

    @Test
    fun submitAutoScan_withMaxEffortOff_sendsMaxEffortFalse() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.toggleMaxEffort(false)
        vm.submitAutoScan()
        advanceUntilIdle()

        assertFalse(
            "max_effort must propagate as false when toggled off in Auto Scan",
            optsSlot.captured.max_effort,
        )
    }

    @Test
    fun submitAutoScan_onException_setsAutoScanError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.autoScanRepo.startScan(any(), any()) } throws RuntimeException("capacity")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.submitAutoScan()
        advanceUntilIdle()

        assertEquals("Failed to start Auto Scan: capacity", vm.uiState.value.error)
        assertFalse(vm.uiState.value.autoScanStarting)
    }

    @Test
    fun onProjectCreated_gitProject_selectsByNameAndPreservesWorktreeAndBranch() = runTest {
        // Why `onProjectCreated` exists rather than reusing
        // `selectProject(name)`. The new project is NOT yet in `state.projects`
        // (the `AgentEvent.ProjectsChanged` bus event arrives asynchronously),
        // so `selectProject(name)`'s lookup would default `is_git` to `false`
        // via `firstOrNull()?.is_git == true` and incorrectly clear worktree
        // for a real git project. `onProjectCreated` reads `is_git` off the
        // passed `ProjectInfo` directly.
        val fx = newFixture(initialProjects = emptyList())  // simulate bus lag
        val vm = fx.buildVm()
        advanceUntilIdle()
        // User had configured a worktree before opening the create-project
        // sheet (e.g. switched modes). Their settings must survive selection.
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        vm.onProjectCreated(project("gamma", isGit = true))

        val s = vm.uiState.value
        assertEquals("gamma", s.selectedProjectName)
        assertTrue(
            "Worktree must be preserved when the newly-created project is git",
            s.worktree,
        )
        assertEquals("feature/x", s.branchName)
    }

    @Test
    fun onProjectCreated_nonGitProject_clampsWorktreeOff_andClearsBranchName() = runTest {
        val fx = newFixture(initialProjects = emptyList())
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        vm.onProjectCreated(project("gamma", isGit = false))

        val s = vm.uiState.value
        assertEquals("gamma", s.selectedProjectName)
        assertFalse(
            "Worktree must clamp off for a non-git newly-created project",
            s.worktree,
        )
        assertEquals("", s.branchName)
    }

    @Test
    fun onProjectCreated_doesNotConsultProjectsList_ignoringStaleEntry() = runTest {
        // Even when `state.projects` is populated AND happens to
        // contain a same-named project with a CONFLICTING `is_git` (e.g.
        // the projects flow hasn't refreshed yet with the latest server
        // snapshot), `onProjectCreated` must trust the passed-in
        // ProjectInfo, never `state.projects`. This pins the contract that
        // `selectProject(name)` would violate.
        val fx = newFixture(
            // Stale fixture says "gamma" is non-git...
            initialProjects = listOf(project("gamma", isGit = false)),
        )
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        // ...but the freshly-returned ProjectInfo from the create call says
        // it IS git. The new method must believe the argument, not the list.
        vm.onProjectCreated(project("gamma", isGit = true))

        val s = vm.uiState.value
        assertEquals("gamma", s.selectedProjectName)
        assertTrue(
            "Worktree must follow the passed ProjectInfo.is_git, not state.projects",
            s.worktree,
        )
        assertEquals("feature/x", s.branchName)
    }

    @Test
    fun onProjectCreated_doesNotChangeWizardStep() = runTest {
        // After the sheet closes the user remains on the
        // ProjectSelect step — the new project is auto-selected in the
        // dropdown, but the wizard does NOT advance. Pinning this so a
        // future "auto-confirm on create" tweak surfaces as a test failure
        // rather than silently changing UX.
        val fx = newFixture(initialProjects = emptyList())
        val vm = fx.buildVm()
        advanceUntilIdle()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)

        vm.onProjectCreated(project("gamma", isGit = true))

        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
    }
}
